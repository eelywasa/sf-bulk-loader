"""SFBL-402: every ``error_summary`` key must be declared on RunErrorSummary.

``RunErrorSummary`` uses ``extra="ignore"``, so an undeclared key is written to
the database and then **silently discarded** before the API response — the run
shows as failed with no visible reason. Three keys drifted that way before this
test existed (``output_storage_error``, ``unexpected_exception``,
``unknown_exit``), one of them the last-resort ``finally`` backstop.

Two complementary checks, because neither alone is sufficient:

1. **Runtime, via the choke point.** Every write funnels through
   ``_merge_run_error_summary`` — including the ``error_summary=`` kwarg path,
   which delegates to it. Wrapping that one function catches keys from
   *dynamically built* dicts, which static analysis cannot see. It only fires
   on paths a test actually exercises.

2. **Static, over the source.** A scan of ``run_coordinator.py`` for
   string-literal keys covers write sites no test happens to reach. It cannot
   see dynamic dicts, which is why check 1 exists.
"""

import ast
import pathlib

import pytest

from app.schemas.load_run import RunErrorSummary
from app.services import run_coordinator

_COORDINATOR_SRC = pathlib.Path(run_coordinator.__file__)


def _declared_fields() -> set[str]:
    return set(RunErrorSummary.model_fields)


# ── 1. Runtime check at the choke point ───────────────────────────────────────


@pytest.fixture
def assert_keys_declared(monkeypatch):
    """Fail any test that writes an error_summary key RunErrorSummary lacks.

    Returns a list of every key observed, so a test can additionally assert
    that the path it intended to exercise actually ran.
    """
    observed: list[str] = []
    original = run_coordinator._merge_run_error_summary

    def _guarded(run, updates: dict) -> None:
        undeclared = set(updates) - _declared_fields()
        assert not undeclared, (
            f"error_summary key(s) {sorted(undeclared)} are written to the "
            f"database but not declared on RunErrorSummary, so they will be "
            f"silently dropped from the API response. Declare them in "
            f"backend/app/schemas/load_run.py."
        )
        observed.extend(updates)
        return original(run, updates)

    monkeypatch.setattr(run_coordinator, "_merge_run_error_summary", _guarded)
    return observed


def test_choke_point_catches_a_dynamically_built_dict(assert_keys_declared):
    """The guard must catch keys a static scan cannot see.

    Falsification: this key never appears as a literal in run_coordinator.py,
    so a pure AST implementation of this contract passes while the runtime
    guard fails. That is the whole reason both checks exist.
    """

    class _FakeRun:
        error_summary = None

    built_at_runtime = {"".join(["not", "_a_", "real_key"]): "boom"}

    with pytest.raises(AssertionError, match="not_a_real_key"):
        run_coordinator._merge_run_error_summary(_FakeRun(), built_at_runtime)


def test_choke_point_allows_declared_keys(assert_keys_declared):
    class _FakeRun:
        error_summary = None

    run = _FakeRun()
    run_coordinator._merge_run_error_summary(run, {"storage_error": "unreachable"})

    assert "storage_error" in assert_keys_declared
    assert "storage_error" in run.error_summary


def test_kwarg_path_routes_through_the_choke_point():
    """_mark_run_failed(error_summary=...) must delegate, not write directly.

    If it ever stopped delegating, the runtime guard above would go blind to
    every kwarg-supplied key — which is most of them.
    """
    source = _COORDINATOR_SRC.read_text()
    tree = ast.parse(source)

    func = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_mark_run_failed"
    )
    called = {
        n.func.id
        for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_merge_run_error_summary" in called


# ── 2. Static check over the source ───────────────────────────────────────────


def _literal_keys_written_in_source() -> set[str]:
    """Collect string-literal error_summary keys written in run_coordinator.

    Covers both write forms: the ``error_summary={...}`` keyword argument and
    positional ``_merge_run_error_summary(run, {...})`` calls.
    """
    tree = ast.parse(_COORDINATOR_SRC.read_text())
    keys: set[str] = set()

    def _harvest(node: ast.AST) -> None:
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "error_summary":
                _harvest(kw.value)
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name == "_merge_run_error_summary" and len(node.args) >= 2:
            _harvest(node.args[1])

    return keys


def test_every_literal_key_in_source_is_declared():
    written = _literal_keys_written_in_source()

    # Guard against the scan silently matching nothing and passing vacuously.
    assert len(written) >= 6, (
        f"expected to find at least the six known error_summary keys, found "
        f"{sorted(written)} — the scan has probably stopped matching the real "
        f"write sites"
    )

    undeclared = written - _declared_fields()
    assert not undeclared, (
        f"error_summary key(s) {sorted(undeclared)} are written in "
        f"run_coordinator.py but not declared on RunErrorSummary. They would be "
        f"persisted and then silently dropped from the API response."
    )


def test_the_three_keys_that_drifted_are_declared():
    """Regression guard for the specific keys SFBL-402 found missing."""
    for key in ("output_storage_error", "unexpected_exception", "unknown_exit"):
        assert key in _declared_fields(), f"{key} must stay declared"
