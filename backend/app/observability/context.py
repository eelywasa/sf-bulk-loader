"""Shared observability context variables.

This module owns the ContextVar instances used to propagate per-request and
per-workflow correlation identifiers across the async call stack. It has no
framework dependencies so it can be imported safely by both middleware and
logging modules without risk of circular imports.

Usage — request context (set/reset via middleware):
    token = request_id_ctx_var.set(request_id)
    try:
        ...
    finally:
        request_id_ctx_var.reset(token)

Usage — workflow context (set at the start of a background task):
    with workflow_logging_context(run_id=run_id, load_plan_id=plan_id):
        await execute_steps(...)   # all log calls inside inherit the context

    # Or in a long-lived background task where token management is cleaner:
    run_id_ctx_var.set(run_id)   # task-scoped; no reset needed

Anywhere in the call stack:
    rid = get_request_id()   # None outside a request context
    rid = get_run_id()       # None outside a workflow context
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

# ── Per-request context ───────────────────────────────────────────────────────

request_id_ctx_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the request ID for the current async context, or None."""
    return request_id_ctx_var.get()


# ── Per-workflow context ──────────────────────────────────────────────────────

run_id_ctx_var: ContextVar[str | None] = ContextVar("run_id", default=None)
step_id_ctx_var: ContextVar[str | None] = ContextVar("step_id", default=None)
job_record_id_ctx_var: ContextVar[str | None] = ContextVar("job_record_id", default=None)
sf_job_id_ctx_var: ContextVar[str | None] = ContextVar("sf_job_id", default=None)
load_plan_id_ctx_var: ContextVar[str | None] = ContextVar("load_plan_id", default=None)
input_connection_id_ctx_var: ContextVar[str | None] = ContextVar("input_connection_id", default=None)


def get_run_id() -> str | None:
    return run_id_ctx_var.get()


def get_step_id() -> str | None:
    return step_id_ctx_var.get()


def get_job_record_id() -> str | None:
    return job_record_id_ctx_var.get()


def get_sf_job_id() -> str | None:
    return sf_job_id_ctx_var.get()


_WORKFLOW_VAR_MAP = {
    "run_id": run_id_ctx_var,
    "step_id": step_id_ctx_var,
    "job_record_id": job_record_id_ctx_var,
    "sf_job_id": sf_job_id_ctx_var,
    "load_plan_id": load_plan_id_ctx_var,
    "input_connection_id": input_connection_id_ctx_var,
}


@contextmanager
def workflow_logging_context(**kwargs: str | None) -> Generator[None, None, None]:
    """Set one or more workflow ContextVars for the current scope, then reset.

    Designed for use in step-scoped or retry-scoped blocks where the IDs
    should be isolated from outer or sibling scopes. For long-lived background
    tasks (where the entire async task IS the scope), calling .set() directly
    without a context manager is also acceptable.

    Example:
        with workflow_logging_context(step_id=step.id, input_connection_id=cid):
            await execute_step(...)
    """
    tokens = {
        k: _WORKFLOW_VAR_MAP[k].set(v)
        for k, v in kwargs.items()
        if k in _WORKFLOW_VAR_MAP
    }
    try:
        yield
    finally:
        for k, token in tokens.items():
            _WORKFLOW_VAR_MAP[k].reset(token)
