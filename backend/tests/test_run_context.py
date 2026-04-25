"""Tests for the run-scoped RunContext + ContextVar helper (SFBL-259, SFBL-121)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services.run_context import (
    RunContext,
    get_run_context,
    run_context_ctx_var,
    update_circuit_breaker,
)


@pytest.fixture(autouse=True)
def _reset_run_context_ctx_var():
    """Ensure each test starts with no RunContext set.

    asyncio inherits ContextVars per-task, so a leak from one test would not
    normally bleed into another, but resetting explicitly removes ambiguity
    when running tests in xdist or in any other concurrent harness.
    """
    token = run_context_ctx_var.set(None)
    try:
        yield
    finally:
        run_context_ctx_var.reset(token)


def test_run_context_construction_records_identity_fields():
    started = datetime.now(timezone.utc)
    ctx = RunContext(run_id="run-1", plan_id="plan-1", started_at=started)

    assert ctx.run_id == "run-1"
    assert ctx.plan_id == "plan-1"
    assert ctx.started_at == started
    # Lock is reserved for follow-up tickets; just confirm it's an asyncio.Lock.
    assert isinstance(ctx._lock, asyncio.Lock)


def test_get_run_context_raises_when_unset():
    """Calling outside a run scope is a programmer error — should raise."""
    with pytest.raises(RuntimeError, match="No RunContext set"):
        get_run_context()


def test_get_run_context_returns_set_instance():
    ctx = RunContext(
        run_id="run-1", plan_id="plan-1", started_at=datetime.now(timezone.utc)
    )
    run_context_ctx_var.set(ctx)
    assert get_run_context() is ctx


@pytest.mark.asyncio
async def test_run_context_visible_in_child_task_via_create_task():
    """asyncio inherits the parent's context per child task (Python 3.11+).

    This is the behaviour partition tasks rely on — they spawn from
    ``asyncio.gather`` (which itself wraps ``ensure_future`` /
    ``create_task``) and must see the same RunContext instance the run
    coordinator set at run start.
    """
    parent_ctx = RunContext(
        run_id="run-1", plan_id="plan-1", started_at=datetime.now(timezone.utc)
    )
    run_context_ctx_var.set(parent_ctx)

    async def _child_reads_context() -> RunContext:
        return get_run_context()

    child_ctx = await asyncio.create_task(_child_reads_context())
    assert child_ctx is parent_ctx


@pytest.mark.asyncio
async def test_run_context_visible_across_concurrent_gather_tasks():
    """All concurrent partition-style tasks see the same RunContext instance."""
    parent_ctx = RunContext(
        run_id="run-1", plan_id="plan-1", started_at=datetime.now(timezone.utc)
    )
    run_context_ctx_var.set(parent_ctx)

    async def _partition_like(idx: int) -> tuple[int, RunContext]:
        # Yield to give the scheduler a chance to interleave.
        await asyncio.sleep(0)
        return idx, get_run_context()

    results = await asyncio.gather(*(_partition_like(i) for i in range(8)))

    for _idx, observed in results:
        assert observed is parent_ctx


@pytest.mark.asyncio
async def test_run_context_lock_is_reentrant_safe_under_gather():
    """The lock is reserved for follow-up tickets; smoke-test it serialises."""
    ctx = RunContext(
        run_id="run-1", plan_id="plan-1", started_at=datetime.now(timezone.utc)
    )
    run_context_ctx_var.set(ctx)

    counter = {"value": 0}

    async def _increment() -> None:
        run_ctx = get_run_context()
        async with run_ctx._lock:
            current = counter["value"]
            await asyncio.sleep(0)  # force interleaving without the lock
            counter["value"] = current + 1

    await asyncio.gather(*(_increment() for _ in range(50)))
    assert counter["value"] == 50


# ── SFBL-121: circuit-breaker field defaults and update_circuit_breaker ───────


def test_run_context_circuit_breaker_defaults():
    ctx = RunContext(run_id="r", plan_id="p", started_at=datetime.now(timezone.utc))
    assert ctx.circuit_breaker_threshold is None
    assert ctx.consecutive_failures == 0
    assert ctx.circuit_breaker_tripped is False


@pytest.mark.asyncio
async def test_update_circuit_breaker_increments_on_failure():
    ctx = RunContext(
        run_id="r", plan_id="p", started_at=datetime.now(timezone.utc),
        circuit_breaker_threshold=3,
    )
    run_context_ctx_var.set(ctx)

    await update_circuit_breaker(success=False)
    assert ctx.consecutive_failures == 1
    assert ctx.circuit_breaker_tripped is False

    await update_circuit_breaker(success=False)
    assert ctx.consecutive_failures == 2
    assert ctx.circuit_breaker_tripped is False


@pytest.mark.asyncio
async def test_update_circuit_breaker_trips_at_threshold():
    ctx = RunContext(
        run_id="r", plan_id="p", started_at=datetime.now(timezone.utc),
        circuit_breaker_threshold=3,
    )
    run_context_ctx_var.set(ctx)

    for _ in range(3):
        await update_circuit_breaker(success=False)

    assert ctx.consecutive_failures == 3
    assert ctx.circuit_breaker_tripped is True


@pytest.mark.asyncio
async def test_update_circuit_breaker_resets_on_success():
    ctx = RunContext(
        run_id="r", plan_id="p", started_at=datetime.now(timezone.utc),
        circuit_breaker_threshold=3,
    )
    run_context_ctx_var.set(ctx)

    await update_circuit_breaker(success=False)
    await update_circuit_breaker(success=False)
    assert ctx.consecutive_failures == 2

    await update_circuit_breaker(success=True)
    assert ctx.consecutive_failures == 0
    assert ctx.circuit_breaker_tripped is False


@pytest.mark.asyncio
async def test_update_circuit_breaker_noop_when_threshold_none():
    ctx = RunContext(
        run_id="r", plan_id="p", started_at=datetime.now(timezone.utc),
        circuit_breaker_threshold=None,
    )
    run_context_ctx_var.set(ctx)

    for _ in range(100):
        await update_circuit_breaker(success=False)

    assert ctx.consecutive_failures == 0
    assert ctx.circuit_breaker_tripped is False


@pytest.mark.asyncio
async def test_update_circuit_breaker_noop_when_no_context():
    # Calling outside a run should not raise.
    await update_circuit_breaker(success=False)


@pytest.mark.asyncio
async def test_update_circuit_breaker_serialises_under_concurrent_tasks():
    """Concurrent failures from N partition tasks all increment the counter safely."""
    threshold = 20
    ctx = RunContext(
        run_id="r", plan_id="p", started_at=datetime.now(timezone.utc),
        circuit_breaker_threshold=threshold,
    )
    run_context_ctx_var.set(ctx)

    await asyncio.gather(*(update_circuit_breaker(success=False) for _ in range(threshold)))

    assert ctx.consecutive_failures == threshold
    assert ctx.circuit_breaker_tripped is True
