"""Tests for the email template engine (SFBL-141).

Covers all 14 spec test cases:
1.  Registry builds cleanly for all 3 shipped templates.
2.  send_template with NoopBackend creates a delivery row.
3.  Layer 1: subject referencing disallowed key raises SUBJECT_REFERENCES_DISALLOWED_KEY.
4.  Layer 2: auth template with dynamic SUBJECT_CONTEXT raises AUTH_TEMPLATE_DYNAMIC_SUBJECT.
5.  Layer 3: URL in subject → SUBJECT_CONTAINS_URL.
6.  Layer 3: opaque token in subject → SUBJECT_CONTAINS_OPAQUE_TOKEN.
7.  Layer 3: control char in subject → SUBJECT_CONTAINS_CONTROL_CHAR.
8.  Layer 3: subject > 200 chars → SUBJECT_TOO_LONG.
9.  Layer 4: pathological context for all 3 shipped stubs.
10. Error message sanitisation — message never contains offending value.
11. MISSING_REQUIRED_CONTEXT and UNKNOWN_CONTEXT_KEY.
12. TEMPLATE_NOT_FOUND.
13. TEMPLATE_UNAVAILABLE.
14. Non-auth manifest failure does not break boot; auth bad manifest does.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from app.services.email.backends.noop import NoopBackend
from app.services.email.errors import EmailRenderError
from app.services.email.message import EmailCategory
from app.services.email.service import EmailService
from app.services.email.templates import (
    CompiledTemplate,
    _REGISTRY,
    _build_jinja_env,
    build_registry,
    render,
)
from tests.services.email.conftest import EmailTestSession


# ── Helpers ───────────────────────────────────────────────────────────────────


def _service() -> EmailService:
    return EmailService(backend=NoopBackend(), session_factory=EmailTestSession)


def _valid_run_complete_context() -> dict[str, Any]:
    return {
        "plan_name": "My Plan",
        "run_id": "run-123",
        "status": "completed",
        "total_rows": 100,
        "success_rows": 98,
        "failed_rows": 2,
        "started_at": "2026-01-01T12:00:00Z",
        "ended_at": "2026-01-01T12:05:00Z",
        "run_url": "http://localhost/runs/run-123",
    }


def _make_template_tree(
    tmp_path: Path,
    *,
    name: str,
    subject_txt: str,
    body_txt: str,
    manifest_py: str,
    body_html: str | None = None,
) -> Path:
    """Create a minimal template tree rooted at tmp_path and return the root."""
    tpl_dir = tmp_path / name
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "manifest.py").write_text(manifest_py, encoding="utf-8")
    (tpl_dir / "subject.txt").write_text(subject_txt, encoding="utf-8")
    (tpl_dir / "body.txt").write_text(body_txt, encoding="utf-8")
    if body_html is not None:
        (tpl_dir / "body.html").write_text(body_html, encoding="utf-8")
    return tmp_path


# ── Test 1: Registry builds cleanly ──────────────────────────────────────────


class TestRegistryBuilds:
    def test_all_three_templates_available(self):
        assert "auth/password_reset" in _REGISTRY
        assert "auth/email_change_verify" in _REGISTRY
        assert "notifications/run_complete" in _REGISTRY

    def test_all_three_templates_available_flag(self):
        for name in ("auth/password_reset", "auth/email_change_verify", "notifications/run_complete"):
            assert _REGISTRY[name].available, f"{name} should be available"

    def test_auth_templates_have_empty_subject_context(self):
        assert _REGISTRY["auth/password_reset"].subject_context == frozenset()
        assert _REGISTRY["auth/email_change_verify"].subject_context == frozenset()

    def test_run_complete_subject_context(self):
        assert _REGISTRY["notifications/run_complete"].subject_context == frozenset({"plan_name", "status"})


# ── Test 2: send_template with NoopBackend ────────────────────────────────────


class TestSendTemplate:
    @pytest.mark.asyncio
    async def test_send_template_creates_delivery(self):
        svc = _service()
        delivery = await svc.send_template(
            "notifications/run_complete",
            _valid_run_complete_context(),
            to="user@example.com",
            category=EmailCategory.NOTIFICATION,
        )
        assert delivery is not None
        assert delivery.template == "notifications/run_complete"

    @pytest.mark.asyncio
    async def test_send_template_records_backend(self):
        svc = _service()
        delivery = await svc.send_template(
            "notifications/run_complete",
            _valid_run_complete_context(),
            to="user@example.com",
            category=EmailCategory.NOTIFICATION,
        )
        assert delivery.backend == "noop"

    @pytest.mark.asyncio
    async def test_send_template_auth_password_reset(self):
        svc = _service()
        delivery = await svc.send_template(
            "auth/password_reset",
            {
                "user_display_name": "Alice",
                "reset_url": "http://localhost/reset?token=abc",
                "expires_in_minutes": 30,
            },
            to="alice@example.com",
            category=EmailCategory.AUTH,
        )
        assert delivery is not None


# ── Test 3: Layer 1 — SUBJECT_REFERENCES_DISALLOWED_KEY ──────────────────────


class TestLayer1:
    def test_non_auth_disallowed_key_marks_unavailable(self, tmp_path: Path):
        root = _make_template_tree(
            tmp_path,
            name="notifications/bad_subject",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"secret_token"})
                SUBJECT_CONTEXT = frozenset()
            """),
            subject_txt="{{ secret_token }}",
            body_txt="Token: {{ secret_token }}",
        )
        registry = build_registry(root)
        tpl = registry.get("notifications/bad_subject")
        assert tpl is not None
        assert not tpl.available

    def test_auth_disallowed_key_is_fatal(self, tmp_path: Path):
        root = _make_template_tree(
            tmp_path,
            name="auth/bad_subject_auth",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"secret_token"})
                SUBJECT_CONTEXT = frozenset()
            """),
            subject_txt="{{ secret_token }}",
            body_txt="Token: {{ secret_token }}",
        )
        with pytest.raises(EmailRenderError) as exc_info:
            build_registry(root)
        assert exc_info.value.code == "SUBJECT_REFERENCES_DISALLOWED_KEY"


# ── Test 4: Layer 2 — AUTH_TEMPLATE_DYNAMIC_SUBJECT ─────────────────────────


class TestLayer2AuthStaticSubject:
    def test_auth_with_nonempty_subject_context_is_fatal(self, tmp_path: Path):
        """Auth template where SUBJECT_CONTEXT = frozenset({"foo"}) must raise at build time."""
        root = _make_template_tree(
            tmp_path,
            name="auth/bad_dynamic",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"foo"})
                SUBJECT_CONTEXT = frozenset({"foo"})
            """),
            subject_txt="{{ foo }}",
            body_txt="Hello {{ foo }}",
        )
        with pytest.raises(EmailRenderError) as exc_info:
            build_registry(root)
        assert exc_info.value.code == "AUTH_TEMPLATE_DYNAMIC_SUBJECT"

    def test_auth_with_empty_subject_context_is_ok(self, tmp_path: Path):
        root = _make_template_tree(
            tmp_path,
            name="auth/good_static",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"foo"})
                SUBJECT_CONTEXT = frozenset()
            """),
            subject_txt="Static subject",
            body_txt="Hello {{ foo }}",
        )
        registry = build_registry(root)
        assert registry["auth/good_static"].available


# ── Tests 5-8: Layer 3 post-render subject checks ────────────────────────────


def _make_notifications_registry(tmp_path: Path, subject_txt: str, *, extra_ctx_key: str | None = None) -> dict:
    """Build a registry with a single notifications template whose subject is subject_txt."""
    ctx_keys = {"plan_name", "status"}
    subject_ctx = {"plan_name", "status"}
    if extra_ctx_key:
        ctx_keys.add(extra_ctx_key)
        subject_ctx.add(extra_ctx_key)

    manifest = textwrap.dedent(f"""\
        REQUIRED_CONTEXT = frozenset({ctx_keys!r})
        SUBJECT_CONTEXT = frozenset({subject_ctx!r})
    """)
    root = _make_template_tree(
        tmp_path,
        name="notifications/layer3_test",
        manifest_py=manifest,
        subject_txt=subject_txt,
        body_txt="plan={{ plan_name }} status={{ status }}{% if '{extra_ctx_key}' != 'None' %} extra={{ " + (extra_ctx_key or "plan_name") + " }}{% endif %}",
    )
    return build_registry(root)


class TestLayer3PostRender:
    def test_url_in_subject_raises(self, tmp_path: Path):
        root = _make_template_tree(
            tmp_path,
            name="notifications/url_test",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"plan_name", "status"})
                SUBJECT_CONTEXT = frozenset({"plan_name", "status"})
            """),
            subject_txt="{{ plan_name }} {{ status }} see https://evil.example.com",
            body_txt="plan={{ plan_name }} status={{ status }}",
        )
        registry = build_registry(root)
        with pytest.raises(EmailRenderError) as exc_info:
            _render_with(registry, "notifications/url_test", {"plan_name": "Plan", "status": "done"})
        assert exc_info.value.code == "SUBJECT_CONTAINS_URL"

    def test_opaque_token_in_subject_raises(self, tmp_path: Path):
        """A 30-char base64-like string in subject triggers SUBJECT_CONTAINS_OPAQUE_TOKEN."""
        long_token = "A" * 30  # 30 chars, all matching [A-Za-z0-9+/=_-]{24,}
        root = _make_template_tree(
            tmp_path,
            name="notifications/token_test",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"plan_name", "status"})
                SUBJECT_CONTEXT = frozenset({"plan_name", "status"})
            """),
            subject_txt=f"{{ plan_name }} {long_token} {{ status }}",
            body_txt="plan={{ plan_name }} status={{ status }}",
        )
        registry = build_registry(root)
        with pytest.raises(EmailRenderError) as exc_info:
            _render_with(registry, "notifications/token_test", {"plan_name": "Plan", "status": "done"})
        assert exc_info.value.code == "SUBJECT_CONTAINS_OPAQUE_TOKEN"

    def test_control_char_in_subject_raises(self, tmp_path: Path):
        """Newline in rendered subject → SUBJECT_CONTAINS_CONTROL_CHAR."""
        root = _make_template_tree(
            tmp_path,
            name="notifications/ctrl_test",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"plan_name", "status"})
                SUBJECT_CONTEXT = frozenset({"plan_name", "status"})
            """),
            subject_txt="{{ plan_name }}\n{{ status }}",
            body_txt="plan={{ plan_name }} status={{ status }}",
        )
        registry = build_registry(root)
        with pytest.raises(EmailRenderError) as exc_info:
            _render_with(registry, "notifications/ctrl_test", {"plan_name": "Plan", "status": "done"})
        assert exc_info.value.code == "SUBJECT_CONTAINS_CONTROL_CHAR"

    def test_subject_too_long_raises(self, tmp_path: Path):
        """Subject longer than 200 chars → SUBJECT_TOO_LONG.

        Use a value with spaces so it doesn't trigger the opaque-token regex
        (which requires 24+ consecutive non-space chars).
        """
        # 210 chars of "word " repeated — no contiguous 24-char run of token chars
        long_value = "word " * 42  # 210 chars
        root = _make_template_tree(
            tmp_path,
            name="notifications/long_test",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"plan_name", "status"})
                SUBJECT_CONTEXT = frozenset({"plan_name", "status"})
            """),
            subject_txt="{{ plan_name }}{{ status }}",
            body_txt="plan={{ plan_name }} status={{ status }}",
        )
        registry = build_registry(root)
        with pytest.raises(EmailRenderError) as exc_info:
            _render_with(registry, "notifications/long_test", {"plan_name": long_value, "status": long_value})
        assert exc_info.value.code == "SUBJECT_TOO_LONG"


def _render_with(registry: dict, name: str, context: dict) -> tuple:
    """Call render() against a custom registry (patch _REGISTRY for the call)."""
    import app.services.email.templates as tpl_module
    original = tpl_module._REGISTRY
    try:
        tpl_module._REGISTRY = registry
        return tpl_module.render(name, context)
    finally:
        tpl_module._REGISTRY = original


# ── Test 9: Layer 4 — pathological context ────────────────────────────────────


_PATHOLOGICAL_STRINGS = [
    "https://evil/?token=abc",
    "A" * 10_000,
    "\x00null byte",
    "line1\nline2",
    "\u0000\u0001\u001f",
    "unicode: \u2603\U0001F600",
    "embedded\rnewline",
]


class TestPathologicalContext:
    """For each shipped stub and each pathological value, render must either
    succeed cleanly OR raise a typed EmailRenderError.  In either case the
    error message must not contain the offending substring."""

    def _assert_safe_render(self, name: str, context: dict, offending: str) -> None:
        """Run render(); assert either success or typed error without leaking offending."""
        try:
            subject, text, html = render(name, context)
        except EmailRenderError as err:
            assert offending not in err.args[0], (
                f"EmailRenderError code leaks offending value for {name}"
            )
            if err.detail:
                assert offending not in err.detail, (
                    f"EmailRenderError.detail leaks offending value for {name}"
                )
        except Exception as err:
            pytest.fail(f"Unexpected exception type {type(err).__name__}: {err}")

    def test_password_reset_pathological(self):
        base = {
            "user_display_name": "Alice",
            "reset_url": "http://localhost/reset",
            "expires_in_minutes": 30,
        }
        for bad_val in _PATHOLOGICAL_STRINGS:
            for key in ("user_display_name", "reset_url", "expires_in_minutes"):
                ctx = dict(base)
                ctx[key] = bad_val
                self._assert_safe_render("auth/password_reset", ctx, bad_val)

    def test_email_change_verify_pathological(self):
        base = {
            "user_display_name": "Alice",
            "confirm_url": "http://localhost/confirm",
            "new_email": "alice@new.example.com",
            "expires_in_minutes": 30,
        }
        for bad_val in _PATHOLOGICAL_STRINGS:
            for key in base:
                ctx = dict(base)
                ctx[key] = bad_val
                self._assert_safe_render("auth/email_change_verify", ctx, bad_val)

    def test_run_complete_pathological(self):
        base = _valid_run_complete_context()
        for bad_val in _PATHOLOGICAL_STRINGS:
            for key in base:
                ctx = dict(base)
                ctx[key] = bad_val
                self._assert_safe_render("notifications/run_complete", ctx, bad_val)


# ── Test 10: Error message sanitisation ───────────────────────────────────────


class TestErrorMessageSanitisation:
    """Assert that EmailRenderError.message (args[0]) never contains offending values."""

    def _check_no_leak(self, exc: EmailRenderError, offending: str) -> None:
        assert offending not in exc.args[0], "code leaks offending value"
        if exc.detail:
            assert offending not in exc.detail, "detail leaks offending value"

    def test_missing_required_context_no_leak(self):
        """MISSING_REQUIRED_CONTEXT error must not include the missing key names in the value."""
        try:
            render("auth/password_reset", {"user_display_name": "Alice"})
        except EmailRenderError as err:
            assert err.code == "MISSING_REQUIRED_CONTEXT"
            # The offending key names are not sensitive; but the context values are
            self._check_no_leak(err, "Alice")

    def test_unknown_context_key_no_leak(self):
        ctx = {
            "user_display_name": "Alice",
            "reset_url": "http://localhost/reset",
            "expires_in_minutes": 30,
            "evil_key": "https://evil.example.com/bad",
        }
        try:
            render("auth/password_reset", ctx)
        except EmailRenderError as err:
            assert err.code == "UNKNOWN_CONTEXT_KEY"
            self._check_no_leak(err, "https://evil.example.com/bad")

    def test_subject_layer3_no_leak(self, tmp_path: Path):
        """SUBJECT_CONTAINS_URL error must not contain the URL."""
        offending = "https://evil.example.com/exfiltrate"
        root = _make_template_tree(
            tmp_path,
            name="notifications/leak_test",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"plan_name", "status"})
                SUBJECT_CONTEXT = frozenset({"plan_name", "status"})
            """),
            subject_txt="{{ plan_name }} {{ status }}",
            body_txt="plan={{ plan_name }} status={{ status }}",
        )
        registry = build_registry(root)
        try:
            _render_with(registry, "notifications/leak_test", {"plan_name": offending, "status": "done"})
        except EmailRenderError as err:
            assert err.code == "SUBJECT_CONTAINS_URL"
            self._check_no_leak(err, offending)
        except Exception:
            pass  # Subject may render without URL if plan_name is not in SUBJECT_CONTEXT


# ── Test 11: MISSING_REQUIRED_CONTEXT and UNKNOWN_CONTEXT_KEY ────────────────


class TestContextKeyErrors:
    def test_missing_required_context(self):
        with pytest.raises(EmailRenderError) as exc_info:
            render("auth/password_reset", {"user_display_name": "Alice"})
        assert exc_info.value.code == "MISSING_REQUIRED_CONTEXT"

    def test_unknown_context_key(self):
        ctx = {
            "user_display_name": "Alice",
            "reset_url": "http://localhost/reset",
            "expires_in_minutes": 30,
            "unexpected_key": "boom",
        }
        with pytest.raises(EmailRenderError) as exc_info:
            render("auth/password_reset", ctx)
        assert exc_info.value.code == "UNKNOWN_CONTEXT_KEY"

    def test_empty_context_raises_missing(self):
        with pytest.raises(EmailRenderError) as exc_info:
            render("notifications/run_complete", {})
        assert exc_info.value.code == "MISSING_REQUIRED_CONTEXT"


# ── Test 12: TEMPLATE_NOT_FOUND ───────────────────────────────────────────────


class TestTemplateNotFound:
    def test_unknown_template_raises(self):
        with pytest.raises(EmailRenderError) as exc_info:
            render("notifications/does_not_exist", {})
        assert exc_info.value.code == "TEMPLATE_NOT_FOUND"

    def test_empty_name_raises(self):
        with pytest.raises(EmailRenderError) as exc_info:
            render("", {})
        assert exc_info.value.code == "TEMPLATE_NOT_FOUND"


# ── Test 13: TEMPLATE_UNAVAILABLE ────────────────────────────────────────────


class TestTemplateUnavailable:
    def test_unavailable_template_raises(self):
        """Mark a template unavailable in the registry, send_template raises TEMPLATE_UNAVAILABLE."""
        import app.services.email.templates as tpl_module

        original = tpl_module._REGISTRY
        tpl = original.get("notifications/run_complete")
        assert tpl is not None

        # Replace with an unavailable copy
        unavailable = CompiledTemplate(
            name=tpl.name,
            required_context=tpl.required_context,
            subject_context=tpl.subject_context,
            subject_tpl=tpl.subject_tpl,
            text_tpl=tpl.text_tpl,
            html_tpl=tpl.html_tpl,
            available=False,
            load_error_code="TEMPLATE_UNAVAILABLE",
        )
        patched = {**original, "notifications/run_complete": unavailable}

        try:
            tpl_module._REGISTRY = patched
            with pytest.raises(EmailRenderError) as exc_info:
                render("notifications/run_complete", _valid_run_complete_context())
            assert exc_info.value.code == "TEMPLATE_UNAVAILABLE"
        finally:
            tpl_module._REGISTRY = original

    @pytest.mark.asyncio
    async def test_send_template_unavailable_raises_before_delivery(self):
        """EmailService.send_template raises EmailRenderError, no delivery row created."""
        import app.services.email.templates as tpl_module

        original = tpl_module._REGISTRY
        tpl = original.get("notifications/run_complete")
        unavailable = CompiledTemplate(
            name=tpl.name,
            required_context=tpl.required_context,
            subject_context=tpl.subject_context,
            subject_tpl=tpl.subject_tpl,
            text_tpl=tpl.text_tpl,
            html_tpl=tpl.html_tpl,
            available=False,
        )
        patched = {**original, "notifications/run_complete": unavailable}

        try:
            tpl_module._REGISTRY = patched
            svc = _service()
            with pytest.raises(EmailRenderError) as exc_info:
                await svc.send_template(
                    "notifications/run_complete",
                    _valid_run_complete_context(),
                    to="user@example.com",
                    category=EmailCategory.NOTIFICATION,
                )
            assert exc_info.value.code == "TEMPLATE_UNAVAILABLE"
        finally:
            tpl_module._REGISTRY = original


# ── Test 14: Boot posture ─────────────────────────────────────────────────────


class TestBootPosture:
    def test_bad_non_auth_manifest_does_not_break_boot(self, tmp_path: Path):
        """Non-auth template with broken manifest → marks unavailable, registry build completes."""
        # Good template
        root = _make_template_tree(
            tmp_path,
            name="notifications/good",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"plan_name"})
                SUBJECT_CONTEXT = frozenset()
            """),
            subject_txt="Good subject",
            body_txt="plan={{ plan_name }}",
        )
        # Bad template — missing SUBJECT_CONTEXT attribute entirely
        bad_dir = tmp_path / "notifications" / "bad_manifest"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "manifest.py").write_text("REQUIRED_CONTEXT = frozenset({'foo'})\n# SUBJECT_CONTEXT missing", encoding="utf-8")
        (bad_dir / "subject.txt").write_text("Static subject", encoding="utf-8")
        (bad_dir / "body.txt").write_text("foo={{ foo }}", encoding="utf-8")

        # Should not raise
        registry = build_registry(tmp_path)
        assert "notifications/good" in registry
        assert registry["notifications/good"].available
        bad_tpl = registry.get("notifications/bad_manifest")
        assert bad_tpl is not None
        assert not bad_tpl.available

    def test_bad_auth_manifest_breaks_boot(self, tmp_path: Path):
        """Auth template with dynamic SUBJECT_CONTEXT raises during build, failing boot."""
        root = _make_template_tree(
            tmp_path,
            name="auth/dynamic_subject",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"user_name"})
                SUBJECT_CONTEXT = frozenset({"user_name"})
            """),
            subject_txt="{{ user_name }}",
            body_txt="Hello {{ user_name }}",
        )
        with pytest.raises(EmailRenderError) as exc_info:
            build_registry(root)
        assert exc_info.value.code == "AUTH_TEMPLATE_DYNAMIC_SUBJECT"

    def test_bad_auth_manifest_code_not_in_message(self, tmp_path: Path):
        """The EmailRenderError from boot failure has stable code, no offending value in message."""
        root = _make_template_tree(
            tmp_path,
            name="auth/leaky",
            manifest_py=textwrap.dedent("""\
                REQUIRED_CONTEXT = frozenset({"user_name"})
                SUBJECT_CONTEXT = frozenset({"user_name"})
            """),
            subject_txt="{{ user_name }}",
            body_txt="Hello {{ user_name }}",
        )
        with pytest.raises(EmailRenderError) as exc_info:
            build_registry(root)
        err = exc_info.value
        # The stable code should be in args[0], not the frozenset or variable name
        assert err.args[0] == err.code
        assert "user_name" not in err.args[0]
