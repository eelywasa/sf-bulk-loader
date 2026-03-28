"""Tests for workflow ContextVar propagation and WorkflowContextFilter.

Covers:
- workflow_logging_context() sets and resets ContextVars correctly
- Nested workflow_logging_context() scopes are isolated
- WorkflowContextFilter injects ContextVar values into LogRecords
- WorkflowContextFilter does not overwrite explicitly-set extra fields
- ContextVars are isolated across concurrent asyncio tasks
- JSON log output includes workflow IDs from ContextVars
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock

import pytest

from app.observability.context import (
    input_connection_id_ctx_var,
    job_record_id_ctx_var,
    load_plan_id_ctx_var,
    run_id_ctx_var,
    sf_job_id_ctx_var,
    step_id_ctx_var,
    workflow_logging_context,
)
from app.observability.logging_config import WorkflowContextFilter, _JsonFormatter


# ── workflow_logging_context ──────────────────────────────────────────────────


def test_workflow_logging_context_sets_run_id() -> None:
    assert run_id_ctx_var.get() is None
    with workflow_logging_context(run_id="run-abc"):
        assert run_id_ctx_var.get() == "run-abc"
    assert run_id_ctx_var.get() is None


def test_workflow_logging_context_sets_multiple_vars() -> None:
    with workflow_logging_context(run_id="r1", step_id="s1", load_plan_id="p1"):
        assert run_id_ctx_var.get() == "r1"
        assert step_id_ctx_var.get() == "s1"
        assert load_plan_id_ctx_var.get() == "p1"


def test_workflow_logging_context_resets_on_exception() -> None:
    with pytest.raises(ValueError):
        with workflow_logging_context(run_id="run-xyz"):
            assert run_id_ctx_var.get() == "run-xyz"
            raise ValueError("boom")
    assert run_id_ctx_var.get() is None


def test_workflow_logging_context_nested_scopes() -> None:
    """Inner scope overrides; outer scope is restored when inner exits."""
    with workflow_logging_context(step_id="outer-step"):
        assert step_id_ctx_var.get() == "outer-step"
        with workflow_logging_context(step_id="inner-step"):
            assert step_id_ctx_var.get() == "inner-step"
        assert step_id_ctx_var.get() == "outer-step"
    assert step_id_ctx_var.get() is None


def test_workflow_logging_context_unknown_keys_ignored() -> None:
    """Unrecognised keys are silently ignored (no KeyError)."""
    with workflow_logging_context(run_id="r1", nonexistent_key="x"):
        assert run_id_ctx_var.get() == "r1"


# ── WorkflowContextFilter ─────────────────────────────────────────────────────


def _make_record(name: str = "test", **extra: str) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test message",
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_workflow_filter_injects_run_id() -> None:
    filt = WorkflowContextFilter()
    token = run_id_ctx_var.set("run-999")
    try:
        record = _make_record()
        filt.filter(record)
        assert record.run_id == "run-999"
    finally:
        run_id_ctx_var.reset(token)


def test_workflow_filter_injects_all_vars() -> None:
    filt = WorkflowContextFilter()
    tokens = [
        run_id_ctx_var.set("r1"),
        step_id_ctx_var.set("s1"),
        job_record_id_ctx_var.set("j1"),
        sf_job_id_ctx_var.set("sf1"),
        load_plan_id_ctx_var.set("p1"),
        input_connection_id_ctx_var.set("c1"),
    ]
    try:
        record = _make_record()
        filt.filter(record)
        assert record.run_id == "r1"
        assert record.step_id == "s1"
        assert record.job_record_id == "j1"
        assert record.sf_job_id == "sf1"
        assert record.load_plan_id == "p1"
        assert record.input_connection_id == "c1"
    finally:
        for t, var in zip(tokens, [
            run_id_ctx_var, step_id_ctx_var, job_record_id_ctx_var,
            sf_job_id_ctx_var, load_plan_id_ctx_var, input_connection_id_ctx_var,
        ]):
            var.reset(t)


def test_workflow_filter_does_not_overwrite_explicit_extra() -> None:
    """If extra= already set run_id on the record, filter must not clobber it."""
    filt = WorkflowContextFilter()
    token = run_id_ctx_var.set("ctx-run")
    try:
        record = _make_record(run_id="explicit-run")
        filt.filter(record)
        assert record.run_id == "explicit-run"
    finally:
        run_id_ctx_var.reset(token)


def test_workflow_filter_sets_none_outside_context() -> None:
    filt = WorkflowContextFilter()
    record = _make_record()
    filt.filter(record)
    assert record.run_id is None
    assert record.step_id is None


# ── JSON formatter picks up ContextVar values via filter ──────────────────────


def _emit_json(msg: str, **extra: str) -> dict:
    """Emit a log record through WorkflowContextFilter + _JsonFormatter."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)

    WorkflowContextFilter().filter(record)
    output = _JsonFormatter(service="test-svc", env="test").format(record)
    return json.loads(output)


def test_json_output_includes_run_id_from_contextvar() -> None:
    token = run_id_ctx_var.set("run-json-test")
    try:
        payload = _emit_json("some event")
        assert payload["run_id"] == "run-json-test"
    finally:
        run_id_ctx_var.reset(token)


def test_json_output_includes_step_id_from_contextvar() -> None:
    with workflow_logging_context(run_id="r1", step_id="s1"):
        payload = _emit_json("step event")
    assert payload["run_id"] == "r1"
    assert payload["step_id"] == "s1"


def test_json_output_explicit_extra_takes_precedence() -> None:
    token = run_id_ctx_var.set("ctx-run")
    try:
        payload = _emit_json("override test", run_id="explicit-run")
        assert payload["run_id"] == "explicit-run"
    finally:
        run_id_ctx_var.reset(token)


# ── Async task isolation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_contextvars_isolated_across_tasks() -> None:
    """Each asyncio task should see its own ContextVar values."""
    results: dict[str, str | None] = {}

    async def task_a() -> None:
        run_id_ctx_var.set("task-a")
        await asyncio.sleep(0)
        results["a"] = run_id_ctx_var.get()

    async def task_b() -> None:
        run_id_ctx_var.set("task-b")
        await asyncio.sleep(0)
        results["b"] = run_id_ctx_var.get()

    await asyncio.gather(
        asyncio.create_task(task_a()),
        asyncio.create_task(task_b()),
    )

    assert results["a"] == "task-a"
    assert results["b"] == "task-b"


@pytest.mark.asyncio
async def test_child_tasks_inherit_parent_contextvar() -> None:
    """Tasks created inside a workflow_logging_context inherit its values."""
    captured: list[str | None] = []

    async def child() -> None:
        await asyncio.sleep(0)
        captured.append(run_id_ctx_var.get())

    with workflow_logging_context(run_id="parent-run"):
        await asyncio.gather(
            asyncio.create_task(child()),
            asyncio.create_task(child()),
        )

    assert captured == ["parent-run", "parent-run"]


@pytest.mark.asyncio
async def test_child_task_mutation_does_not_affect_parent() -> None:
    """Child task setting a ContextVar must not bleed into sibling or parent."""
    run_id_ctx_var.set("parent-run")

    async def child() -> None:
        run_id_ctx_var.set("child-run")
        await asyncio.sleep(0)

    await asyncio.create_task(child())
    assert run_id_ctx_var.get() == "parent-run"
