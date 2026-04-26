"""Tests for app.services.step_reference_resolver (SFBL-262).

All tests run synchronously using ``asyncio.new_event_loop()`` so they integrate
cleanly with the existing ``_TestSession`` fixture from conftest.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

# conftest.py must be processed first (sets ENCRYPTION_KEY etc.) — it is at the
# tests/ root and is automatically picked up by pytest before this module.

# Imports of app code are intentionally deferred to here (after conftest sets env).
from tests.conftest import _TestSession  # noqa: E402  (conftest is in parent package)

from app.models.connection import Connection
from app.models.input_connection import InputConnection
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun
from app.models.load_step import LoadStep
from app.services.input_storage import InputStorageError, LocalInputStorage, S3InputStorage
from app.services.step_reference_resolver import (
    StepReferenceResolutionError,
    resolve_step_input,
)
from app.utils.encryption import encrypt_secret


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(coro):
    """Execute a coroutine in a temporary event loop (safe outside any running loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_id() -> str:
    return str(uuid.uuid4())


def _make_connection_row() -> Connection:
    return Connection(
        id=_make_id(),
        name="Test Org",
        instance_url="https://example.my.salesforce.com",
        login_url="https://login.salesforce.com",
        client_id="cid",
        private_key=encrypt_secret(
            "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----"
        ),
        username="u@example.com",
        is_sandbox=False,
    )


def _make_input_connection(bucket: str = "my-bucket") -> InputConnection:
    return InputConnection(
        id=_make_id(),
        name="S3 Conn",
        provider="s3",
        bucket=bucket,
        root_prefix="outputs/",
        region="us-east-1",
        access_key_id=encrypt_secret("AKID"),
        secret_access_key=encrypt_secret("SECRET"),
        session_token=None,
        direction="out",
    )


async def _seed_local_scenario(
    session,
    *,
    success_file_path: str | None = "run-output/accounts.csv",
) -> tuple[LoadStep, str, LoadPlan]:
    """Seed a minimal local-backend plan + upstream query step + JobRecord.

    Returns ``(downstream_step, run_id, plan)``.
    """
    conn = _make_connection_row()
    session.add(conn)
    await session.flush()

    plan = LoadPlan(
        id=_make_id(),
        connection_id=conn.id,
        output_connection_id=None,  # local
        name="Test Plan",
    )
    session.add(plan)
    await session.flush()

    upstream_step = LoadStep(
        id=_make_id(),
        load_plan_id=plan.id,
        sequence=1,
        object_name="Account",
        operation="query",
        soql="SELECT Id FROM Account",
    )
    session.add(upstream_step)
    await session.flush()

    downstream_step = LoadStep(
        id=_make_id(),
        load_plan_id=plan.id,
        sequence=2,
        object_name="Contact",
        operation="insert",
        input_from_step_id=upstream_step.id,
    )
    session.add(downstream_step)
    await session.flush()

    run = LoadRun(
        id=_make_id(),
        load_plan_id=plan.id,
        status="running",
    )
    session.add(run)
    await session.flush()

    job = JobRecord(
        id=_make_id(),
        load_run_id=run.id,
        load_step_id=upstream_step.id,
        partition_index=0,
        status=JobStatus.job_complete,
        success_file_path=success_file_path,
    )
    session.add(job)
    await session.commit()

    return downstream_step, run.id, plan


async def _seed_s3_scenario(
    session,
    *,
    bucket: str = "my-bucket",
    success_file_path: str | None = "s3://my-bucket/outputs/run-abc/accounts.csv",
) -> tuple[LoadStep, str, LoadPlan]:
    """Seed a minimal S3-backend plan + upstream query step + JobRecord.

    Returns ``(downstream_step, run_id, plan)``.
    """
    conn = _make_connection_row()
    session.add(conn)
    await session.flush()

    ic = _make_input_connection(bucket=bucket)
    session.add(ic)
    await session.flush()

    plan = LoadPlan(
        id=_make_id(),
        connection_id=conn.id,
        output_connection_id=ic.id,
        name="Test Plan S3",
    )
    session.add(plan)
    await session.flush()

    upstream_step = LoadStep(
        id=_make_id(),
        load_plan_id=plan.id,
        sequence=1,
        object_name="Account",
        operation="query",
        soql="SELECT Id FROM Account",
    )
    session.add(upstream_step)
    await session.flush()

    downstream_step = LoadStep(
        id=_make_id(),
        load_plan_id=plan.id,
        sequence=2,
        object_name="Contact",
        operation="insert",
        input_from_step_id=upstream_step.id,
    )
    session.add(downstream_step)
    await session.flush()

    run = LoadRun(
        id=_make_id(),
        load_plan_id=plan.id,
        status="running",
    )
    session.add(run)
    await session.flush()

    job = JobRecord(
        id=_make_id(),
        load_run_id=run.id,
        load_step_id=upstream_step.id,
        partition_index=0,
        status=JobStatus.job_complete,
        success_file_path=success_file_path,
    )
    session.add(job)
    await session.commit()

    return downstream_step, run.id, plan


# ── Tests: inheritance ────────────────────────────────────────────────────────


def test_step_reference_resolution_error_is_input_storage_error():
    """StepReferenceResolutionError must subclass InputStorageError."""
    assert isinstance(StepReferenceResolutionError("x"), InputStorageError)
    assert issubclass(StepReferenceResolutionError, InputStorageError)


# ── Tests: local backend ──────────────────────────────────────────────────────


def test_local_backend_returns_local_input_storage():
    """Resolving a local-backend step returns LocalInputStorage and rel path."""
    from app.config import settings

    async def _test():
        async with _TestSession() as session:
            step, run_id, plan = await _seed_local_scenario(session)

        async with _TestSession() as session:
            # Re-fetch to get a clean session-bound objects
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()
            storage, paths = await resolve_step_input(step, run_id, plan, session)

        return storage, paths

    storage, paths = _run(_test())
    assert isinstance(storage, LocalInputStorage)
    # The storage root must be settings.output_dir
    import pathlib
    assert pathlib.Path(storage._base) == pathlib.Path(settings.output_dir).resolve()
    assert paths == ["run-output/accounts.csv"]


# ── Tests: S3 backend ─────────────────────────────────────────────────────────


class _FakeS3Client:
    """Minimal boto3-compatible S3 client stub."""
    def __init__(self):
        pass

    def get_paginator(self, _name):
        return self

    def paginate(self, **_kw):
        return []

    def get_object(self, *, Bucket, Key):
        raise FileNotFoundError("stub — not called in resolver tests")

    def list_objects_v2(self, **_kw):
        return {"Contents": [], "CommonPrefixes": []}


def test_s3_backend_returns_s3_input_storage_and_full_key():
    """Resolving an S3-backend step returns S3InputStorage with root_prefix='' and full key."""
    async def _test():
        async with _TestSession() as session:
            step, run_id, plan = await _seed_s3_scenario(
                session,
                bucket="my-bucket",
                success_file_path="s3://my-bucket/outputs/run-abc/accounts.csv",
            )

        async with _TestSession() as session:
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()

            # boto3.client is called inside S3InputStorage.__init__ which lives in
            # app.services.input_storage — patch it there.
            with patch("app.services.input_storage.boto3.client", return_value=_FakeS3Client()):
                storage, paths = await resolve_step_input(step, run_id, plan, session)

        return storage, paths

    storage, paths = _run(_test())
    assert isinstance(storage, S3InputStorage)
    # root_prefix must be empty — full_key already includes the original prefix
    assert storage._root_prefix == ""
    # bucket from URI
    assert storage._bucket == "my-bucket"
    # rel_path is the full object key (path without leading slash)
    assert paths == ["outputs/run-abc/accounts.csv"]


def test_s3_bucket_mismatch_raises_resolution_error():
    """URI bucket != InputConnection.bucket → StepReferenceResolutionError."""
    async def _test():
        async with _TestSession() as session:
            # IC has bucket "my-bucket" but URI says "other-bucket"
            step, run_id, plan = await _seed_s3_scenario(
                session,
                bucket="my-bucket",
                success_file_path="s3://other-bucket/outputs/accounts.csv",
            )

        async with _TestSession() as session:
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()

            with pytest.raises(StepReferenceResolutionError, match="does not match"):
                await resolve_step_input(step, run_id, plan, session)

    _run(_test())


@pytest.mark.parametrize("bad_uri", [
    "http://my-bucket/key",     # wrong scheme
    "ftp://my-bucket/key",      # wrong scheme
    "not-a-uri",                # no scheme at all
    "s3://",                    # no bucket or path
    "s3:///key-only",           # no bucket
])
def test_malformed_s3_uri_raises_resolution_error(bad_uri):
    """Various malformed URIs → StepReferenceResolutionError."""
    async def _test():
        async with _TestSession() as session:
            step, run_id, plan = await _seed_s3_scenario(
                session,
                bucket="my-bucket",
                success_file_path=bad_uri,
            )

        async with _TestSession() as session:
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()

            with pytest.raises(StepReferenceResolutionError):
                await resolve_step_input(step, run_id, plan, session)

    _run(_test())


# ── Tests: error paths ────────────────────────────────────────────────────────


def test_missing_upstream_job_record_raises():
    """No JobRecord for run → StepReferenceResolutionError."""
    async def _test():
        async with _TestSession() as session:
            step, run_id, plan = await _seed_local_scenario(session)

        # Use a different run_id — the seeded JobRecord won't match.
        async with _TestSession() as session:
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()

            with pytest.raises(StepReferenceResolutionError, match="No JobRecord found"):
                await resolve_step_input(step, _make_id(), plan, session)

    _run(_test())


def test_null_success_file_path_raises():
    """JobRecord exists but success_file_path is NULL → StepReferenceResolutionError."""
    async def _test():
        async with _TestSession() as session:
            step, run_id, plan = await _seed_local_scenario(
                session, success_file_path=None
            )

        async with _TestSession() as session:
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()

            with pytest.raises(StepReferenceResolutionError, match="no success_file_path"):
                await resolve_step_input(step, run_id, plan, session)

    _run(_test())


def test_cross_run_isolation():
    """JobRecord from a different run is not picked up."""
    async def _test():
        async with _TestSession() as session:
            # Seed scenario for run_A
            step, run_a_id, plan = await _seed_local_scenario(session)

        # Call resolve with a brand-new run_B ID — should not find run_A's record.
        async with _TestSession() as session:
            from sqlalchemy import select as sa_select
            from app.models.load_step import LoadStep as LS
            from app.models.load_plan import LoadPlan as LP

            step = (await session.execute(sa_select(LS).where(LS.id == step.id))).scalar_one()
            plan = (await session.execute(sa_select(LP).where(LP.id == plan.id))).scalar_one()

            run_b_id = _make_id()
            assert run_b_id != run_a_id

            with pytest.raises(StepReferenceResolutionError, match="No JobRecord found"):
                await resolve_step_input(step, run_b_id, plan, session)

    _run(_test())
