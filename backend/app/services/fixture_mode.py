"""Salesforce describe fixture mode — startup-only, process-lifetime.

Controls whether the backend serves Salesforce metadata (object lists,
per-SObject describes) from disk-based JSON fixtures or from the live
Salesforce REST API.

Mode is determined **once at process startup** from the env var
``SF_DESCRIBE_FIXTURES_DIR`` (a colon-separated list of directories,
PATH-like). Once resolved, mode is immutable for the process lifetime.
Runtime flips are explicitly refused — callers can rely on the mode
being stable.

Usage
-----
Import the module-level singletons:

    from app.services.fixture_mode import fixture_mode

Then call:

    fixture_mode.is_fixture_mode()       # -> bool
    fixture_mode.resolve_fixture(name)   # -> Optional[Path]  (first-match)
    fixture_mode.list_union_for(name)    # -> Optional[list]  (union across all dirs)
    fixture_mode.mode                    # -> "fixture" | "live"

The ``_object_list.json`` sentinel is the only file that receives
list-union semantics; all other fixture files use first-match-wins
(complete-replacement) semantics per D5.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Literal, Optional

_log = logging.getLogger(__name__)

# The sentinel filename for the SObject list fixture (list-union semantics).
OBJECT_LIST_FIXTURE = "_object_list.json"


class FixtureMode:
    """Startup-resolved fixture mode singleton.

    Instantiation reads ``SF_DESCRIBE_FIXTURES_DIR`` from the environment
    and resolves the list of fixture directories.  After construction, the
    mode is immutable.

    Parameters
    ----------
    env_value:
        The raw value of ``SF_DESCRIBE_FIXTURES_DIR``.  If ``None`` or an
        empty string, the instance is in ``live`` mode.  Normally supplied
        from ``settings.sf_describe_fixtures_dir`` (which reads it from the
        environment at Settings construction time).
    """

    def __init__(self, env_value: Optional[str]) -> None:
        self._dirs: List[Path] = self._resolve_dirs(env_value)
        self._mode: Literal["fixture", "live"] = (
            "fixture" if self._dirs else "live"
        )
        self._log_startup()

    # ── Public interface ────────────────────────────────────────────────────────

    @property
    def mode(self) -> Literal["fixture", "live"]:
        """The resolved mode: ``'fixture'`` or ``'live'``."""
        return self._mode

    def is_fixture_mode(self) -> bool:
        """Return ``True`` when the backend is running in fixture mode."""
        return self._mode == "fixture"

    def resolve_fixture(self, filename: str) -> Optional[Path]:
        """Return the first directory that contains *filename*, or ``None``.

        Uses PATH-like first-match-wins semantics.  Suitable for per-SObject
        describe files (e.g. ``Account.json``) where the first match is the
        authoritative replacement.

        Parameters
        ----------
        filename:
            The bare filename to search for (e.g. ``"Account.json"``).

        Returns
        -------
        ``Path`` pointing at the existing file, or ``None`` if not found in
        any configured directory.
        """
        if not self._dirs:
            return None
        for d in self._dirs:
            candidate = d / filename
            if candidate.exists():
                return candidate
        return None

    def list_union_for(self, filename: str) -> Optional[list]:
        """Return the list-union of *filename* across all fixture directories.

        Intended for ``_object_list.json`` where multiple overlays should
        contribute entries rather than having one shadow the other.

        Reads every directory that contains *filename*, parses each as a
        JSON array, and returns the deduplicated, sorted union.

        Returns ``None`` if *filename* is absent from **all** configured
        directories (i.e. fixture mode is active but no list fixture exists).
        Returns an empty list ``[]`` only if every present file is an empty
        JSON array.

        Parameters
        ----------
        filename:
            The bare filename (e.g. ``"_object_list.json"``).
        """
        if not self._dirs:
            return None

        combined: list[str] = []
        found_any = False
        for d in self._dirs:
            candidate = d / filename
            if not candidate.exists():
                continue
            found_any = True
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                _log.warning(
                    "fixture_mode: failed to read %s: %s", candidate, exc,
                    extra={"event_name": "fixture_mode.read_error"},
                )
                continue
            if isinstance(data, list):
                combined.extend(str(v) for v in data)
            else:
                _log.warning(
                    "fixture_mode: %s is not a JSON array — skipped", candidate,
                    extra={"event_name": "fixture_mode.unexpected_format"},
                )

        if not found_any:
            return None

        return sorted(set(combined))

    # ── Private helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_dirs(env_value: Optional[str]) -> List[Path]:
        """Split *env_value* on ``':'`` and return existing Path objects.

        Paths that do not exist are silently skipped with a warning so that a
        typo in one entry does not silently enable live mode.
        """
        if not env_value or not env_value.strip():
            return []

        raw_dirs = [part.strip() for part in env_value.split(":") if part.strip()]
        resolved: List[Path] = []
        for raw in raw_dirs:
            p = Path(raw)
            if p.is_dir():
                resolved.append(p)
            else:
                _log.warning(
                    "fixture_mode: SF_DESCRIBE_FIXTURES_DIR entry %r does not exist "
                    "or is not a directory — skipped",
                    raw,
                    extra={"event_name": "fixture_mode.invalid_dir"},
                )
        return resolved

    def _log_startup(self) -> None:
        """Emit startup log and ambiguous-overlay warnings."""
        if self._mode == "live":
            _log.info(
                "fixture_mode: SF_DESCRIBE_FIXTURES_DIR not set — running in LIVE mode",
                extra={"event_name": "fixture_mode.startup", "mode": "live"},
            )
            return

        _log.info(
            "fixture_mode: running in FIXTURE mode; dirs=%s",
            [str(d) for d in self._dirs],
            extra={"event_name": "fixture_mode.startup", "mode": "fixture"},
        )
        self._warn_ambiguous_overlays()

    def _warn_ambiguous_overlays(self) -> None:
        """Warn when two or more dirs contain the same fixture filename.

        First-match still wins; the warning exists purely to surface operator
        surprises (e.g. an accidentally committed baseline override that
        silently wins because it appears first on the path).

        Only warns for filenames that are **not** ``_object_list.json`` — that
        file has list-union semantics and multi-dir presence is expected.
        """
        if len(self._dirs) < 2:
            return

        # Collect filenames present in each dir
        dir_files: list[set[str]] = []
        for d in self._dirs:
            try:
                dir_files.append({f.name for f in d.iterdir() if f.is_file()})
            except OSError:
                dir_files.append(set())

        # Find filenames appearing in more than one dir (excluding the list
        # fixture which intentionally appears in multiple dirs)
        seen: dict[str, list[int]] = {}
        for idx, files in enumerate(dir_files):
            for fname in files:
                if fname == OBJECT_LIST_FIXTURE:
                    continue
                seen.setdefault(fname, []).append(idx)

        ambiguous = {fname: idxs for fname, idxs in seen.items() if len(idxs) > 1}
        for fname, idxs in sorted(ambiguous.items()):
            winning_dir = self._dirs[idxs[0]]
            other_dirs = [str(self._dirs[i]) for i in idxs[1:]]
            _log.warning(
                "fixture_mode: ambiguous overlay — %r found in multiple fixture "
                "dirs; first-match wins (%s); also in: %s",
                fname,
                winning_dir,
                ", ".join(other_dirs),
                extra={
                    "event_name": "fixture_mode.ambiguous_overlay",
                    "fixture_file": fname,
                    "winning_dir": str(winning_dir),
                },
            )


def _build_from_env() -> FixtureMode:
    """Build the module-level singleton from the current environment."""
    return FixtureMode(os.getenv("SF_DESCRIBE_FIXTURES_DIR"))


# Module-level singleton — resolved once at import time (process startup).
# Tests that need to exercise different modes should construct their own
# ``FixtureMode`` instances rather than patching this singleton.
fixture_mode: FixtureMode = _build_from_env()
