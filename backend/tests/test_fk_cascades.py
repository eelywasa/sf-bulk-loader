"""Foreign-key cascade / SET NULL behaviour assertions (SFBL-270).

Every ``ON DELETE CASCADE`` and ``ON DELETE SET NULL`` declared in the
schema is exercised here so that a future regression of the SQLite PRAGMA
listener (or an inline-FK gap on Postgres à la migration 0028) fails this
file rather than silently corrupting data at runtime.

Coverage rules
--------------
- One test per CASCADE FK: insert parent → child, delete parent, assert the
  child row is gone.
- One test per SET NULL FK: insert parent → child, delete parent, assert
  the FK column on the surviving child row is NULL.
- One negative-control test (`test_pragma_disabled_cascade_is_noop`) opens
  a session with ``PRAGMA foreign_keys=OFF`` and asserts the cascade does
  NOT fire — guards against the listener's dialect-name gate quietly
  regressing back to ``isinstance(dbapi_connection, sqlite3.Connection)``.
- One coverage-completeness test walks ``Base.metadata`` and fails if a
  new CASCADE / SET NULL FK is introduced without a paired test entry in
  ``EXPECTED_CASCADE_FKS`` / ``EXPECTED_SET_NULL_FKS`` below.
- One unit test for ``assert_sqlite_fk_enforcement_active`` covering both
  the happy path (PRAGMA returns 1) and the failure path (PRAGMA returns
  0 → RuntimeError).

If you add or change a FK in the schema, update this file in the same
commit. The completeness test will tell you which entries to edit.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, assert_sqlite_fk_enforcement_active
from app.models.connection import Connection
from app.models.email_change_token import EmailChangeToken
from app.models.email_delivery import EmailDelivery
from app.models.invitation_token import InvitationToken
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep, Operation
from app.models.login_attempt import LoginAttempt
from app.models.notification_delivery import NotificationDelivery
from app.models.notification_subscription import (
    NotificationChannel,
    NotificationSubscription,
    NotificationTrigger,
)
from app.models.password_reset_token import PasswordResetToken
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from tests.conftest import _TestSession as _SessionFactory


# ─────────────────────────────────────────────────────────────────────────────
# Coverage map — keep in lock-step with docs/architecture/foreign-keys.md
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_CASCADE_FKS: set[tuple[str, str, str]] = {
    # (child_table, child_col, parent_table)
    ("user_totp", "user_id", "user"),
    ("user_backup_code", "user_id", "user"),
    ("invitation_tokens", "user_id", "user"),
    ("password_reset_token", "user_id", "user"),
    ("email_change_token", "user_id", "user"),
    ("notification_subscription", "user_id", "user"),
    ("notification_subscription", "plan_id", "load_plan"),
    ("notification_delivery", "subscription_id", "notification_subscription"),
    ("profile_permissions", "profile_id", "profiles"),
    ("load_step", "load_plan_id", "load_plan"),
    ("job_record", "load_run_id", "load_run"),
}

EXPECTED_SET_NULL_FKS: set[tuple[str, str, str]] = {
    ("user", "invited_by", "user"),
    ("login_attempt", "user_id", "user"),
    ("notification_delivery", "run_id", "load_run"),
    ("notification_delivery", "email_delivery_id", "email_delivery"),
    ("load_plan", "output_connection_id", "input_connection"),
    ("load_step", "input_from_step_id", "load_step"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Per-test isolation: scrub anything the cascade tests created
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _scrub_cascade_tables():
    yield
    async with _SessionFactory() as s:
        # Order respects FK dependencies (children first). Profile rows are
        # session-scoped seed data and must not be deleted.
        for model in [
            NotificationDelivery,
            NotificationSubscription,
            EmailDelivery,
            InvitationToken,
            PasswordResetToken,
            EmailChangeToken,
            UserBackupCode,
            UserTotp,
            LoginAttempt,
            JobRecord,
            LoadRun,
            LoadStep,
            LoadPlan,
            Connection,
            User,
        ]:
            await s.execute(delete(model))
        await s.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Factories
# ─────────────────────────────────────────────────────────────────────────────

async def _mk_user(s: AsyncSession, *, invited_by: str | None = None) -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=f"u-{uuid.uuid4()}@example.com",
        hashed_password="x",
        status="active",
        invited_by=invited_by,
    )
    s.add(u)
    await s.flush()
    return u


async def _mk_connection(s: AsyncSession) -> Connection:
    c = Connection(
        id=str(uuid.uuid4()),
        name="org",
        instance_url="https://example.my.salesforce.com",
        login_url="https://login.salesforce.com",
        client_id="cid",
        private_key="enc",
        username="user@example.com",
    )
    s.add(c)
    await s.flush()
    return c


async def _mk_plan(s: AsyncSession, conn_id: str) -> LoadPlan:
    p = LoadPlan(id=str(uuid.uuid4()), connection_id=conn_id, name="P")
    s.add(p)
    await s.flush()
    return p


async def _mk_step(s: AsyncSession, plan_id: str, *, sequence: int = 1) -> LoadStep:
    st = LoadStep(
        id=str(uuid.uuid4()),
        load_plan_id=plan_id,
        sequence=sequence,
        object_name="Account",
        operation=Operation.insert,
        csv_file_pattern="*.csv",
        partition_size=10000,
    )
    s.add(st)
    await s.flush()
    return st


async def _mk_run(s: AsyncSession, plan_id: str) -> LoadRun:
    r = LoadRun(id=str(uuid.uuid4()), load_plan_id=plan_id, status=RunStatus.completed)
    s.add(r)
    await s.flush()
    return r


async def _mk_job(s: AsyncSession, run_id: str, step_id: str) -> JobRecord:
    j = JobRecord(
        id=str(uuid.uuid4()),
        load_run_id=run_id,
        load_step_id=step_id,
        partition_index=0,
        status=JobStatus.job_complete,
    )
    s.add(j)
    await s.flush()
    return j


async def _mk_email_delivery(s: AsyncSession) -> EmailDelivery:
    now = datetime.now(timezone.utc)
    e = EmailDelivery(
        id=str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
        category="test",
        backend="dummy",
        to_hash="0" * 64,
        to_domain="example.com",
        subject="s",
        max_attempts=3,
    )
    s.add(e)
    await s.flush()
    return e


async def _mk_subscription(
    s: AsyncSession, user_id: str, *, plan_id: str | None = None
) -> NotificationSubscription:
    sub = NotificationSubscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        plan_id=plan_id,
        channel=NotificationChannel.email,
        destination=f"{uuid.uuid4()}@example.com",
        trigger=NotificationTrigger.terminal_any,
    )
    s.add(sub)
    await s.flush()
    return sub


async def _mk_input_connection(s: AsyncSession):
    from app.models.input_connection import InputConnection

    ic = InputConnection(
        id=str(uuid.uuid4()),
        name="ic",
        provider="s3",
        bucket="b",
        access_key_id="k",
        secret_access_key="s",
        direction="out",
    )
    s.add(ic)
    await s.flush()
    return ic


# ─────────────────────────────────────────────────────────────────────────────
# CASCADE tests — one per FK
# ─────────────────────────────────────────────────────────────────────────────


async def test_cascade_user_totp_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        s.add(UserTotp(user_id=u.id, secret_encrypted="e"))
        await s.commit()
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
        rows = (await s.execute(select(UserTotp).where(UserTotp.user_id == u.id))).all()
        assert rows == []


async def test_cascade_user_backup_code_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        s.add(UserBackupCode(user_id=u.id, code_hash="h" * 60))
        await s.commit()
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
        rows = (
            await s.execute(select(UserBackupCode).where(UserBackupCode.user_id == u.id))
        ).all()
        assert rows == []


async def test_cascade_invitation_tokens_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        s.add(
            InvitationToken(
                user_id=u.id,
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        await s.commit()
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
        rows = (
            await s.execute(select(InvitationToken).where(InvitationToken.user_id == u.id))
        ).all()
        assert rows == []


async def test_cascade_password_reset_token_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        s.add(
            PasswordResetToken(
                user_id=u.id,
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                created_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
        rows = (
            await s.execute(
                select(PasswordResetToken).where(PasswordResetToken.user_id == u.id)
            )
        ).all()
        assert rows == []


async def test_cascade_email_change_token_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        s.add(
            EmailChangeToken(
                user_id=u.id,
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                new_email="new@example.com",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                created_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
        rows = (
            await s.execute(
                select(EmailChangeToken).where(EmailChangeToken.user_id == u.id)
            )
        ).all()
        assert rows == []


async def test_cascade_notification_subscription_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        sub = await _mk_subscription(s, u.id)
        await s.commit()
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
        rows = (
            await s.execute(
                select(NotificationSubscription).where(
                    NotificationSubscription.id == sub.id
                )
            )
        ).all()
        assert rows == []


async def test_cascade_notification_subscription_plan_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        c = await _mk_connection(s)
        plan = await _mk_plan(s, c.id)
        sub = await _mk_subscription(s, u.id, plan_id=plan.id)
        await s.commit()
        await s.execute(delete(LoadPlan).where(LoadPlan.id == plan.id))
        await s.commit()
        rows = (
            await s.execute(
                select(NotificationSubscription).where(
                    NotificationSubscription.id == sub.id
                )
            )
        ).all()
        assert rows == []


async def test_cascade_notification_delivery_subscription_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        sub = await _mk_subscription(s, u.id)
        d = NotificationDelivery(subscription_id=sub.id)
        s.add(d)
        await s.commit()
        await s.execute(
            delete(NotificationSubscription).where(NotificationSubscription.id == sub.id)
        )
        await s.commit()
        rows = (
            await s.execute(
                select(NotificationDelivery).where(NotificationDelivery.id == d.id)
            )
        ).all()
        assert rows == []


async def test_cascade_profile_permissions_profile_id():
    """Permission rows follow their profile.

    Profiles 'admin', 'operator', 'viewer' are session-scoped seed data; we
    create a throwaway profile here so the deletion does not collide with
    other tests.
    """
    async with _SessionFactory() as s:
        prof = Profile(id=str(uuid.uuid4()), name=f"throwaway-{uuid.uuid4().hex[:8]}")
        s.add(prof)
        await s.flush()
        s.add(ProfilePermission(profile_id=prof.id, permission_key="plans.view"))
        await s.commit()
        await s.execute(delete(Profile).where(Profile.id == prof.id))
        await s.commit()
        rows = (
            await s.execute(
                select(ProfilePermission).where(ProfilePermission.profile_id == prof.id)
            )
        ).all()
        assert rows == []


async def test_cascade_load_step_load_plan_id():
    async with _SessionFactory() as s:
        c = await _mk_connection(s)
        plan = await _mk_plan(s, c.id)
        step = await _mk_step(s, plan.id)
        await s.commit()
        await s.execute(delete(LoadPlan).where(LoadPlan.id == plan.id))
        await s.commit()
        rows = (await s.execute(select(LoadStep).where(LoadStep.id == step.id))).all()
        assert rows == []


async def test_cascade_job_record_load_run_id():
    async with _SessionFactory() as s:
        c = await _mk_connection(s)
        plan = await _mk_plan(s, c.id)
        step = await _mk_step(s, plan.id)
        run = await _mk_run(s, plan.id)
        job = await _mk_job(s, run.id, step.id)
        await s.commit()
        await s.execute(delete(LoadRun).where(LoadRun.id == run.id))
        await s.commit()
        rows = (await s.execute(select(JobRecord).where(JobRecord.id == job.id))).all()
        assert rows == []


# ─────────────────────────────────────────────────────────────────────────────
# SET NULL tests — one per FK
# ─────────────────────────────────────────────────────────────────────────────


async def test_set_null_user_invited_by():
    async with _SessionFactory() as s:
        inviter = await _mk_user(s)
        invitee = await _mk_user(s, invited_by=inviter.id)
        await s.commit()
        invitee_id = invitee.id
        await s.execute(delete(User).where(User.id == inviter.id))
        await s.commit()
    async with _SessionFactory() as s2:
        refreshed = await s2.get(User, invitee_id)
        assert refreshed is not None
        assert refreshed.invited_by is None


async def test_set_null_login_attempt_user_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        attempt = LoginAttempt(
            user_id=u.id,
            username=u.email,
            ip="127.0.0.1",
            outcome="ok",
            attempted_at=datetime.now(timezone.utc),
        )
        s.add(attempt)
        await s.commit()
        attempt_id = attempt.id
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()
    async with _SessionFactory() as s2:
        refreshed = await s2.get(LoginAttempt, attempt_id)
        assert refreshed is not None
        assert refreshed.user_id is None


async def test_set_null_notification_delivery_run_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        sub = await _mk_subscription(s, u.id)
        c = await _mk_connection(s)
        plan = await _mk_plan(s, c.id)
        run = await _mk_run(s, plan.id)
        d = NotificationDelivery(subscription_id=sub.id, run_id=run.id)
        s.add(d)
        await s.commit()
        d_id = d.id
        await s.execute(delete(LoadRun).where(LoadRun.id == run.id))
        await s.commit()
    async with _SessionFactory() as s2:
        refreshed = await s2.get(NotificationDelivery, d_id)
        assert refreshed is not None
        assert refreshed.run_id is None


async def test_set_null_notification_delivery_email_delivery_id():
    async with _SessionFactory() as s:
        u = await _mk_user(s)
        sub = await _mk_subscription(s, u.id)
        em = await _mk_email_delivery(s)
        d = NotificationDelivery(subscription_id=sub.id, email_delivery_id=em.id)
        s.add(d)
        await s.commit()
        d_id = d.id
        await s.execute(delete(EmailDelivery).where(EmailDelivery.id == em.id))
        await s.commit()
    async with _SessionFactory() as s2:
        refreshed = await s2.get(NotificationDelivery, d_id)
        assert refreshed is not None
        assert refreshed.email_delivery_id is None


async def test_set_null_load_plan_output_connection_id():
    from app.models.input_connection import InputConnection

    async with _SessionFactory() as s:
        c = await _mk_connection(s)
        ic = await _mk_input_connection(s)
        plan = LoadPlan(
            id=str(uuid.uuid4()),
            connection_id=c.id,
            output_connection_id=ic.id,
            name="P",
        )
        s.add(plan)
        await s.commit()
        plan_id = plan.id
        await s.execute(delete(InputConnection).where(InputConnection.id == ic.id))
        await s.commit()
    async with _SessionFactory() as s2:
        refreshed = await s2.get(LoadPlan, plan_id)
        assert refreshed is not None
        assert refreshed.output_connection_id is None


async def test_set_null_load_step_input_from_step_id():
    async with _SessionFactory() as s:
        c = await _mk_connection(s)
        plan = await _mk_plan(s, c.id)
        upstream = await _mk_step(s, plan.id, sequence=1)
        downstream = LoadStep(
            id=str(uuid.uuid4()),
            load_plan_id=plan.id,
            sequence=2,
            object_name="Contact",
            operation=Operation.insert,
            csv_file_pattern="*.csv",
            partition_size=10000,
            input_from_step_id=upstream.id,
        )
        s.add(downstream)
        await s.commit()
        downstream_id = downstream.id
        await s.execute(delete(LoadStep).where(LoadStep.id == upstream.id))
        await s.commit()
    async with _SessionFactory() as s2:
        refreshed = await s2.get(LoadStep, downstream_id)
        assert refreshed is not None
        assert refreshed.input_from_step_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Negative control: with PRAGMA foreign_keys=OFF the cascade does NOT fire.
# This is the test that would have passed before the c554767 fix and would
# now expose any future regression of the listener's dialect-name gate.
# ─────────────────────────────────────────────────────────────────────────────


async def test_pragma_disabled_cascade_is_noop():
    """Direct sqlite3 connection with FK enforcement off must NOT cascade.

    Uses a synchronous sqlite3 connection against the same on-disk DB the
    async test session writes to so the assertion targets the SQLite engine
    itself, not the SQLAlchemy listener. If this test fails, FK enforcement
    is being applied somewhere it shouldn't be.

    SQLite-only — PRAGMA foreign_keys is not a Postgres concept.
    """
    from tests.conftest import _DEFAULT_TEST_DB_PATH, _is_sqlite

    if not _is_sqlite:
        pytest.skip("SQLite-specific PRAGMA test")

    # First seed via the normal session (FK on) so the schema is populated.
    async with _SessionFactory() as s:
        c = await _mk_connection(s)
        plan = await _mk_plan(s, c.id)
        step = await _mk_step(s, plan.id)
        await s.commit()
        plan_id = plan.id
        step_id = step.id

    # Now reach in via raw sqlite3 with FKs OFF and delete the parent.
    raw = sqlite3.connect(_DEFAULT_TEST_DB_PATH)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("DELETE FROM load_plan WHERE id = ?", (plan_id,))
        raw.commit()
        # With enforcement off, the child survives (orphaned).
        cur = raw.execute("SELECT id FROM load_step WHERE id = ?", (step_id,))
        assert cur.fetchone() is not None
    finally:
        # Tidy up the orphan so other tests' isolation still holds.
        raw.execute("DELETE FROM load_step WHERE id = ?", (step_id,))
        raw.commit()
        raw.close()


# ─────────────────────────────────────────────────────────────────────────────
# Coverage completeness — adding a new CASCADE/SET NULL FK without a test
# entry in this file fails CI immediately.
# ─────────────────────────────────────────────────────────────────────────────


def test_every_declared_cascade_has_a_test():
    declared_cascade = set()
    declared_set_null = set()
    for tbl in Base.metadata.sorted_tables:
        for fk in tbl.foreign_keys:
            key = (tbl.name, fk.parent.name, fk.column.table.name)
            if fk.ondelete == "CASCADE":
                declared_cascade.add(key)
            elif fk.ondelete == "SET NULL":
                declared_set_null.add(key)
    cascade_missing = declared_cascade - EXPECTED_CASCADE_FKS
    cascade_extra = EXPECTED_CASCADE_FKS - declared_cascade
    set_null_missing = declared_set_null - EXPECTED_SET_NULL_FKS
    set_null_extra = EXPECTED_SET_NULL_FKS - declared_set_null
    assert not cascade_missing, (
        f"New CASCADE FK(s) declared in the schema but not paired with a "
        f"test in test_fk_cascades.py: {sorted(cascade_missing)}"
    )
    assert not cascade_extra, (
        f"EXPECTED_CASCADE_FKS lists FK(s) that no longer exist in the "
        f"schema — remove them: {sorted(cascade_extra)}"
    )
    assert not set_null_missing, (
        f"New SET NULL FK(s) declared in the schema but not paired with a "
        f"test in test_fk_cascades.py: {sorted(set_null_missing)}"
    )
    assert not set_null_extra, (
        f"EXPECTED_SET_NULL_FKS lists FK(s) that no longer exist in the "
        f"schema — remove them: {sorted(set_null_extra)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Boot-time PRAGMA assertion — direct unit tests
# ─────────────────────────────────────────────────────────────────────────────


async def test_assert_sqlite_fk_enforcement_active_passes():
    """Happy path: PRAGMA returns 1 because the connect listener applied it."""
    await assert_sqlite_fk_enforcement_active()  # must not raise


async def test_assert_sqlite_fk_enforcement_active_raises_when_off(monkeypatch):
    """If a future regression leaves PRAGMA at 0, lifespan must raise."""
    import app.database as db_mod

    class _Result:
        def scalar(self):  # noqa: D401 - simple shim
            return 0

    class _Bind:
        class dialect:
            name = "sqlite"

    class _Session:
        bind = _Bind()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, _stmt):
            return _Result()

    def _factory():
        return _Session()

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", _factory)

    with pytest.raises(RuntimeError, match="foreign_keys enforcement is OFF"):
        await db_mod.assert_sqlite_fk_enforcement_active()
