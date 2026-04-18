"""Jinja2 template engine for the email service.

Module-level registry:
  Walk the templates directory at import time, discover every leaf template
  directory (one with a ``manifest.py``), compile and cache a
  ``CompiledTemplate`` record.

  Auth templates (name starts with ``auth/``) with dynamic subjects are
  **fatal** — they raise ``EmailRenderError`` during the registry build so
  FastAPI startup fails immediately.

  Non-auth templates with a bad manifest are marked ``available=False`` and
  a ``email.template.load_failed`` event is logged.  The caller receives
  ``TEMPLATE_UNAVAILABLE`` on ``render()``.

Public API:
  ``render(name, context)`` → ``(subject, text_body, html_body | None)``

Internal helper (also used by tests):
  ``build_registry(templates_root)`` → ``dict[str, CompiledTemplate]``
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined, meta, select_autoescape
from jinja2 import Template as JinjaTemplate

from app.observability.events import EmailEvent, OutcomeCode
from app.services.email.errors import EmailRenderError

logger = logging.getLogger(__name__)

# ── Template root ─────────────────────────────────────────────────────────────

_TEMPLATES_ROOT = Path(__file__).parent / "templates"

# ── CompiledTemplate ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompiledTemplate:
    """Immutable record for a compiled, validated template."""

    name: str                         # e.g. "notifications/run_complete"
    required_context: frozenset[str]
    subject_context: frozenset[str]
    subject_tpl: JinjaTemplate
    text_tpl: JinjaTemplate
    html_tpl: JinjaTemplate | None    # None when body.html is absent
    available: bool = True            # False when non-auth manifest failed
    load_error_code: str | None = None


# ── Subject post-render deny patterns ────────────────────────────────────────

_RE_URL = re.compile(r"https?://", re.IGNORECASE)
_RE_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{24,}")
_RE_CONTROL_CHAR = re.compile(r"[\x00-\x1f]")
_SUBJECT_MAX_LEN = 200

# ── Registry builder ──────────────────────────────────────────────────────────


def _build_jinja_env(templates_root: Path) -> Environment:
    """Create a Jinja2 Environment bound to *templates_root*."""
    return Environment(
        loader=FileSystemLoader(str(templates_root)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def _load_manifest(manifest_path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Import ``manifest.py`` via importlib and extract context sets.

    Returns ``(REQUIRED_CONTEXT, SUBJECT_CONTEXT)``.
    Raises ``ValueError`` if attributes are missing or wrong type.
    """
    module_name = f"_email_manifest_{manifest_path.parent.as_posix().replace('/', '_').replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, manifest_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load manifest spec from {manifest_path}")
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules to avoid re-loading in tests
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    required = getattr(mod, "REQUIRED_CONTEXT", _MISSING)
    subject = getattr(mod, "SUBJECT_CONTEXT", _MISSING)

    if required is _MISSING or not isinstance(required, frozenset):
        raise ValueError(
            f"manifest.py at {manifest_path} must define REQUIRED_CONTEXT as frozenset"
        )
    if subject is _MISSING or not isinstance(subject, frozenset):
        raise ValueError(
            f"manifest.py at {manifest_path} must define SUBJECT_CONTEXT as frozenset"
        )

    return frozenset(required), frozenset(subject)


_MISSING = object()


def _check_subject_ast(env: Environment, subject_source: str, subject_context: frozenset[str], template_name: str) -> None:
    """Layer 1 — load-time AST check.

    Parses subject.txt and verifies that every Jinja variable referenced is
    declared in ``SUBJECT_CONTEXT``.  Any extra variable raises
    ``EmailRenderError("SUBJECT_REFERENCES_DISALLOWED_KEY")``.
    """
    parsed = env.parse(subject_source)
    undeclared = meta.find_undeclared_variables(parsed)
    disallowed = undeclared - subject_context
    if disallowed:
        raise EmailRenderError(
            "SUBJECT_REFERENCES_DISALLOWED_KEY",
            detail=(
                f"subject.txt for {template_name!r} references variables not in "
                f"SUBJECT_CONTEXT; declare them there or remove them from the subject"
            ),
        )


def build_registry(templates_root: Path) -> dict[str, CompiledTemplate]:
    """Walk *templates_root*, compile every manifest directory, return registry.

    This function is called once at module import time and also by tests when
    pointing at a temporary tree.
    """
    env = _build_jinja_env(templates_root)
    registry: dict[str, CompiledTemplate] = {}

    for manifest_path in sorted(templates_root.rglob("manifest.py")):
        tpl_dir = manifest_path.parent
        # Derive name relative to templates_root, using forward slashes
        name = tpl_dir.relative_to(templates_root).as_posix()
        is_auth = name.startswith("auth/")

        # ── Load manifest ──────────────────────────────────────────────────
        try:
            required_context, subject_context = _load_manifest(manifest_path)
        except Exception as exc:
            _handle_manifest_failure(is_auth, name, f"manifest load error: {exc}", registry)
            continue

        # ── Layer 2: auth templates must have static subjects ──────────────
        if is_auth and subject_context != frozenset():
            # Fatal — raises immediately; FastAPI startup fails
            raise EmailRenderError(
                "AUTH_TEMPLATE_DYNAMIC_SUBJECT",
                detail=(
                    f"Auth template {name!r} has SUBJECT_CONTEXT = {subject_context!r}; "
                    "auth templates must have SUBJECT_CONTEXT = frozenset() for static subjects"
                ),
            )

        # ── Load template files ────────────────────────────────────────────
        try:
            subject_path = tpl_dir / "subject.txt"
            text_path = tpl_dir / "body.txt"
            html_path = tpl_dir / "body.html"

            subject_source = subject_path.read_text(encoding="utf-8")
            text_source = text_path.read_text(encoding="utf-8")

            # Layer 1 — AST check before compiling
            _check_subject_ast(env, subject_source, subject_context, name)

            # Compile templates using their path relative to templates_root
            rel_name = tpl_dir.relative_to(templates_root).as_posix()
            subject_tpl = env.from_string(subject_source)
            text_tpl = env.get_template(f"{rel_name}/body.txt")
            html_tpl = env.get_template(f"{rel_name}/body.html") if html_path.exists() else None

        except EmailRenderError:
            # Layer 1 validation failure (SUBJECT_REFERENCES_DISALLOWED_KEY).
            # Auth templates raise fatally; non-auth mark unavailable.
            if is_auth:
                raise
            _handle_manifest_failure(is_auth, name, "subject validation failed", registry)
            continue
        except Exception as exc:
            _handle_manifest_failure(is_auth, name, f"template load error: {exc}", registry)
            continue

        registry[name] = CompiledTemplate(
            name=name,
            required_context=required_context,
            subject_context=subject_context,
            subject_tpl=subject_tpl,
            text_tpl=text_tpl,
            html_tpl=html_tpl,
        )

    return registry


def _handle_manifest_failure(is_auth: bool, name: str, reason: str, registry: dict) -> None:
    """Handle a non-fatal template load failure.

    Auth failures should never reach here (they raise before calling this).
    Non-auth: log event, insert unavailable record.
    """
    logger.error(
        "Email template failed to load",
        extra={
            "event_name": EmailEvent.TEMPLATE_LOAD_FAILED,
            "outcome_code": OutcomeCode.EMAIL_TEMPLATE_LOAD_FAILED,
            "template_name": name,
            "reason": reason,
        },
    )
    registry[name] = CompiledTemplate(
        name=name,
        required_context=frozenset(),
        subject_context=frozenset(),
        subject_tpl=_DUMMY_TPL,  # type: ignore[arg-type]  # never rendered
        text_tpl=_DUMMY_TPL,    # type: ignore[arg-type]
        html_tpl=None,
        available=False,
        load_error_code="TEMPLATE_UNAVAILABLE",
    )


# Sentinel used for unavailable template slots (never rendered)
_DUMMY_TPL: Any = None


# ── Module-level registry ─────────────────────────────────────────────────────

# Build eagerly at import time.  Auth template with dynamic subject raises here
# and propagates through the import chain, failing FastAPI startup.
_REGISTRY: dict[str, CompiledTemplate] = build_registry(_TEMPLATES_ROOT)


# ── render() ─────────────────────────────────────────────────────────────────


def render(
    name: str,
    context: Mapping[str, Any],
) -> tuple[str, str, str | None]:
    """Render a template and return ``(subject, text_body, html_body)``.

    Raises ``EmailRenderError`` with a stable code on any validation failure.
    The error message is always the stable code only — the offending value
    is never included in the exception or its detail string.
    """
    # Step 1 — look up template
    tpl = _REGISTRY.get(name)
    if tpl is None:
        raise EmailRenderError("TEMPLATE_NOT_FOUND", detail=f"No template registered as {name!r}")
    if not tpl.available:
        raise EmailRenderError("TEMPLATE_UNAVAILABLE", detail=f"Template {name!r} failed to load at startup")

    # Step 2 — context key validation
    provided = frozenset(context.keys())
    missing = tpl.required_context - provided
    if missing:
        raise EmailRenderError(
            "MISSING_REQUIRED_CONTEXT",
            detail=f"Template {name!r} requires context keys that were not provided",
        )
    unknown = provided - tpl.required_context
    if unknown:
        raise EmailRenderError(
            "UNKNOWN_CONTEXT_KEY",
            detail=f"Caller passed context keys not declared in REQUIRED_CONTEXT for {name!r}",
        )

    # Step 3 — render subject (pass only SUBJECT_CONTEXT subset — defence in depth)
    subject_ctx = {k: context[k] for k in tpl.subject_context if k in context}
    subject = tpl.subject_tpl.render(subject_ctx)

    # Step 4 — Layer 3 post-render subject safety checks
    _check_rendered_subject(subject)

    # Step 5 — render text body with full context
    text_body = tpl.text_tpl.render(dict(context))

    # Step 6 — render HTML body if available
    html_body: str | None = None
    if tpl.html_tpl is not None:
        html_body = tpl.html_tpl.render(dict(context))

    return subject, text_body, html_body


def _check_rendered_subject(subject: str) -> None:
    """Layer 3 — post-render subject safety checks.

    Raises ``EmailRenderError`` with a stable code on the first failure.
    The offending value is NEVER included in the exception or its detail.
    """
    if _RE_URL.search(subject):
        raise EmailRenderError(
            "SUBJECT_CONTAINS_URL",
            detail="rendered subject failed safety check",
        )
    if _RE_OPAQUE_TOKEN.search(subject):
        raise EmailRenderError(
            "SUBJECT_CONTAINS_OPAQUE_TOKEN",
            detail="rendered subject failed safety check",
        )
    if _RE_CONTROL_CHAR.search(subject):
        raise EmailRenderError(
            "SUBJECT_CONTAINS_CONTROL_CHAR",
            detail="rendered subject failed safety check",
        )
    if len(subject) > _SUBJECT_MAX_LEN:
        raise EmailRenderError(
            "SUBJECT_TOO_LONG",
            detail="rendered subject failed safety check",
        )
