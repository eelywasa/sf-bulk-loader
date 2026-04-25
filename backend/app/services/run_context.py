"""Run-scoped in-memory state for the orchestrator (SFBL-259).

This module owns a single dataclass — :class:`RunContext` — that holds
mutable state which lives for the duration of one ``LoadRun`` execution and
must be visible to all concurrent partition tasks spawned within that run.

The instance is set on a ContextVar at run start, and is then accessible
from anywhere in the orchestrator stack via :func:`get_run_context` without
threading it through function signatures. Python 3.11+ asyncio inherits the
parent task's context per child task, so partitions spawned via
``asyncio.gather()`` see the same instance automatically.

SFBL-121 adds the circuit-breaker fields:
- ``circuit_breaker_threshold`` — copied from ``LoadPlan.consecutive_failure_threshold``
  at run start; ``None`` disables the feature.
- ``consecutive_failures`` — counts consecutive partition-level failures since
  the last success; reset to 0 on any ``JobComplete`` partition.
- ``circuit_breaker_tripped`` — set to ``True`` by ``partition_executor`` when
  ``consecutive_failures >= circuit_breaker_threshold``; read by
  ``run_coordinator`` after each step to abort the run.

All three fields are mutated under ``_lock`` by :func:`update_circuit_breaker`
so concurrent partition coroutines see consistent state.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


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
        circuit_breaker_threshold: Copied from ``LoadPlan.consecutive_failure_threshold``.
            ``None`` or ``0`` disables the circuit breaker.
        consecutive_failures: Running count of consecutive partition-level
            failures (SF job ``Failed``, ``BulkAPIError``).  Reset to 0 on
            any ``JobComplete`` outcome.
        circuit_breaker_tripped: Set to ``True`` when ``consecutive_failures``
            reaches ``circuit_breaker_threshold``.  Once tripped it stays
            ``True`` for the remainder of the run.
        _lock: Guards all mutable fields above. Callers must use
            ``async with run_ctx._lock:`` before reading/writing.
    """

    run_id: str
    plan_id: str
    started_at: datetime
    circuit_breaker_threshold: Optional[int] = None
    consecutive_failures: int = 0
    circuit_breaker_tripped: bool = False
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


async def update_circuit_breaker(success: bool) -> None:
    """Atomically update the circuit-breaker counter in the active RunContext.

    Call this after every partition resolves:
    - ``success=True``  → ``JobComplete`` terminal state; resets ``consecutive_failures``.
    - ``success=False`` → any terminal partition failure (``BulkAPIError``,
      SF ``Failed`` state); increments ``consecutive_failures`` and trips the
      breaker when the threshold is reached.

    Safe to call when no ``RunContext`` is set (e.g. in retry runs that
    pre-date SFBL-121) — in that case it is a no-op.
    """
    ctx = run_context_ctx_var.get()
    if ctx is None:
        return
    async with ctx._lock:
        if success:
            ctx.consecutive_failures = 0
        else:
            threshold = ctx.circuit_breaker_threshold
            if threshold and threshold > 0:
                ctx.consecutive_failures += 1
                if ctx.consecutive_failures >= threshold:
                    ctx.circuit_breaker_tripped = True
