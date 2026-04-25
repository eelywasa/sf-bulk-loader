"""Run-scoped in-memory state for the orchestrator (SFBL-259).

This module owns a single dataclass — :class:`RunContext` — that holds
mutable state which lives for the duration of one ``LoadRun`` execution and
must be visible to all concurrent partition tasks spawned within that run.

The instance is set on a ContextVar at run start, and is then accessible
from anywhere in the orchestrator stack via :func:`get_run_context` without
threading it through function signatures. Python 3.11+ asyncio inherits the
parent task's context per child task, so partitions spawned via
``asyncio.gather()`` see the same instance automatically.

This ticket (SFBL-259) deliberately delivers a minimal seam:
- Identity fields only (``run_id``, ``plan_id``, ``started_at``)
- An ``asyncio.Lock`` reserved for follow-up tickets that add mutable state

It does NOT replace existing run-scoped accounting:
- Per-step record-level aggregation continues to flow via the
  ``(step_success, step_errors)`` return values from
  :func:`step_executor.execute_step`.
- Abort state remains DB-backed (``LoadRun.status``); future in-process
  abort triggers (e.g. SFBL-121's circuit breaker) will flow through a DB
  write rather than a ContextVar flag.

Follow-up tickets (SFBL-121 etc.) will add domain-specific fields and
methods to this class as the need for in-process run-scoped state arises.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RunContext:
    """Run-scoped in-memory state for one ``LoadRun`` execution.

    Constructed by :func:`run_coordinator.execute_run` (and
    :func:`run_coordinator.execute_retry_run`) immediately after loading the
    ``LoadRun`` row, then set on :data:`run_context_ctx_var` for the lifetime
    of the run. Partition tasks spawned via ``asyncio.gather`` inherit the
    ContextVar and see the same instance.

    Attributes:
        run_id: The ``LoadRun.id`` this context is bound to.
        plan_id: The ``LoadPlan.id`` the run is executing.
        started_at: Timestamp captured when the context was constructed.
        _lock: Reserved for follow-up tickets that add mutable state. Holders
            must use ``async with run_ctx._lock:`` to mutate fields safely
            from concurrent partition tasks.
    """

    run_id: str
    plan_id: str
    started_at: datetime
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


run_context_ctx_var: ContextVar["RunContext | None"] = ContextVar(
    "run_context", default=None
)


def get_run_context() -> RunContext:
    """Return the active :class:`RunContext` for the current async task.

    Raises:
        RuntimeError: when called outside an executing run (i.e. when no
            :class:`RunContext` has been set on :data:`run_context_ctx_var`).
            This is a programmer error — callers that may legitimately run
            outside a run scope should call ``run_context_ctx_var.get()``
            directly and handle ``None``.
    """
    ctx = run_context_ctx_var.get()
    if ctx is None:
        raise RuntimeError(
            "No RunContext set; get_run_context() must be called inside an executing run"
        )
    return ctx
