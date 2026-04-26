"""Tests for the /api/load-plans endpoints."""

import pytest

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "test_client_id",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "test@example.com",
    "is_sandbox": False,
}

_PLAN = {
    "name": "Q1 Migration",
    "description": "Quarterly data load",
    "abort_on_step_failure": True,
    "error_threshold_pct": 5.0,
    "max_parallel_jobs": 3,
}


def _create_connection(auth_client) -> str:
    return auth_client.post("/api/connections/", json=_CONN).json()["id"]


def _create_plan(auth_client, connection_id: str, overrides=None) -> dict:
    payload = {**_PLAN, "connection_id": connection_id, **(overrides or {})}
    return auth_client.post("/api/load-plans/", json=payload).json()


# ── Create ─────────────────────────────────────────────────────────────────────


def test_create_plan_returns_201(auth_client):
    conn_id = _create_connection(auth_client)
    payload = {**_PLAN, "connection_id": conn_id}
    resp = auth_client.post("/api/load-plans/", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == _PLAN["name"]
    assert body["connection_id"] == conn_id
    assert "id" in body
    assert body["load_steps"] == []


def test_create_plan_invalid_connection_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/", json={**_PLAN, "connection_id": "bad-id"})
    assert resp.status_code == 404


def test_create_plan_missing_name_returns_422(auth_client):
    conn_id = _create_connection(auth_client)
    resp = auth_client.post("/api/load-plans/", json={"connection_id": conn_id})
    assert resp.status_code == 422


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_plans_empty(auth_client):
    resp = auth_client.get("/api/load-plans/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_plans_returns_all(auth_client):
    conn_id = _create_connection(auth_client)
    _create_plan(auth_client, conn_id)
    _create_plan(auth_client, conn_id, {"name": "Plan B"})
    resp = auth_client.get("/api/load-plans/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Get ────────────────────────────────────────────────────────────────────────


def test_get_plan_returns_plan_with_steps(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.get(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == plan_id
    assert "load_steps" in body


def test_get_plan_not_found_returns_404(auth_client):
    resp = auth_client.get("/api/load-plans/nonexistent")
    assert resp.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────


def test_update_plan_name(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.put(f"/api/load-plans/{plan_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_plan_invalid_connection_returns_404(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.put(f"/api/load-plans/{plan_id}", json={"connection_id": "bad-id"})
    assert resp.status_code == 404


def test_update_plan_not_found_returns_404(auth_client):
    resp = auth_client.put("/api/load-plans/bad-id", json={"name": "X"})
    assert resp.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────────


def test_delete_plan_returns_204(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.delete(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 204


def test_delete_plan_removes_record(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    auth_client.delete(f"/api/load-plans/{plan_id}")
    assert auth_client.get(f"/api/load-plans/{plan_id}").status_code == 404


def test_delete_plan_not_found_returns_404(auth_client):
    assert auth_client.delete("/api/load-plans/nonexistent").status_code == 404


def test_delete_plan_cascades_to_steps(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    # Add a step
    auth_client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "insert",
            "csv_file_pattern": "accounts*.csv",
        },
    )
    auth_client.delete(f"/api/load-plans/{plan_id}")
    # Plan and its steps should be gone
    assert auth_client.get(f"/api/load-plans/{plan_id}").status_code == 404


# ── Duplicate ──────────────────────────────────────────────────────────────────

_STEP = {
    "sequence": 1,
    "object_name": "Account",
    "operation": "insert",
    "csv_file_pattern": "accounts*.csv",
    "partition_size": 5000,
}


def test_duplicate_plan_returns_201(auth_client):
    conn_id = _create_connection(auth_client)
    source = _create_plan(auth_client, conn_id)
    auth_client.post(f"/api/load-plans/{source['id']}/steps", json=_STEP)

    resp = auth_client.post(f"/api/load-plans/{source['id']}/duplicate")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != source["id"]
    assert body["name"] == f"Copy of {source['name']}"
    assert body["connection_id"] == source["connection_id"]
    assert len(body["load_steps"]) == 1


def test_duplicate_plan_copies_steps(auth_client):
    conn_id = _create_connection(auth_client)
    source = _create_plan(auth_client, conn_id)
    auth_client.post(f"/api/load-plans/{source['id']}/steps", json=_STEP)

    copy = auth_client.post(f"/api/load-plans/{source['id']}/duplicate").json()
    src_step = auth_client.get(f"/api/load-plans/{source['id']}").json()["load_steps"][0]
    copy_step = copy["load_steps"][0]

    assert copy_step["id"] != src_step["id"]
    assert copy_step["load_plan_id"] == copy["id"]
    assert copy_step["object_name"] == src_step["object_name"]
    assert copy_step["operation"] == src_step["operation"]
    assert copy_step["csv_file_pattern"] == src_step["csv_file_pattern"]
    assert copy_step["partition_size"] == src_step["partition_size"]
    assert copy_step["sequence"] == src_step["sequence"]


def test_duplicate_plan_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/nonexistent/duplicate")
    assert resp.status_code == 404


# ── SFBL-260: dynamic field-coverage regression test ─────────────────────────


@pytest.mark.asyncio
async def test_duplicate_plan_copies_all_columns():
    """Every non-identity column on LoadPlan + LoadStep is carried through duplication.

    Asserts dynamically against ``__table__.columns`` so any future column added
    to either model is automatically caught by this test if duplication forgets it.
    Without this test, the bug fixed in SFBL-260 (silently dropped
    ``output_connection_id``, ``consecutive_failure_threshold``, ``soql``,
    ``input_connection_id``) could recur whenever a new field is introduced.
    """
    import uuid

    from app.models.connection import Connection
    from app.models.input_connection import InputConnection
    from app.models.load_plan import LoadPlan
    from app.models.load_step import LoadStep, Operation
    from app.services.load_plan_service import duplicate_plan
    from tests.conftest import _TestSession

    # Expected exclusion sets are hardcoded in the test (not imported from the
    # service) so that a change to the service's exclusion sets MUST be made
    # deliberately in two places. Without this, broadening the service's
    # exclusion list (e.g. accidentally excluding ``output_connection_id``
    # again) would silently make this test pass while ``duplicate_plan``
    # regressed — defeating the purpose of the dynamic regression guard.
    EXPECTED_PLAN_EXCLUDED = {"id", "created_at", "updated_at", "name"}
    EXPECTED_STEP_EXCLUDED = {"id", "created_at", "updated_at", "load_plan_id"}

    async with _TestSession() as db:
        # ── Build a fully-populated plan + steps. ─────────────────────────────
        # Every non-identity column on both models is set to a non-default value
        # so the comparison below is meaningful (default-valued columns would
        # match even if duplication dropped them).
        conn = Connection(
            id=str(uuid.uuid4()),
            name="Test Org",
            instance_url="https://test.salesforce.com",
            login_url="https://login.salesforce.com",
            client_id="cid",
            private_key="encrypted",
            username="user@example.com",
            is_sandbox=True,
        )
        ic = InputConnection(
            id=str(uuid.uuid4()),
            name="S3 Output",
            provider="s3",
            bucket="my-bucket",
            root_prefix="results/",
            region="us-east-1",
            access_key_id="encrypted-ak",
            secret_access_key="encrypted-sk",
        )
        db.add_all([conn, ic])
        await db.flush()

        plan = LoadPlan(
            id=str(uuid.uuid4()),
            connection_id=conn.id,
            name="Source Plan",
            description="full coverage",
            abort_on_step_failure=False,
            error_threshold_pct=42.0,
            max_parallel_jobs=7,
            consecutive_failure_threshold=9,
            output_connection_id=ic.id,
        )
        db.add(plan)
        await db.flush()

        step = LoadStep(
            id=str(uuid.uuid4()),
            load_plan_id=plan.id,
            sequence=1,
            object_name="Account",
            operation=Operation.upsert,
            external_id_field="ExternalId__c",
            csv_file_pattern="accounts*.csv",
            soql="SELECT Id FROM Account",
            partition_size=4321,
            assignment_rule_id="01Q5g000000XYZ1",
            input_connection_id=ic.id,
        )
        db.add(step)
        await db.commit()

        # ── Duplicate via the service. ─────────────────────────────────────────
        copy = await duplicate_plan(db, plan.id)

        # ── Plan-level coverage. ───────────────────────────────────────────────
        plan_cols = {c.name for c in LoadPlan.__table__.columns} - EXPECTED_PLAN_EXCLUDED
        # The 'name' column is intentionally excluded (prefixed "Copy of …");
        # assert that explicitly so a future maintainer doesn't accidentally
        # change the rename rule.
        assert copy.name == f"Copy of {plan.name}"
        for col in plan_cols:
            assert getattr(copy, col) == getattr(plan, col), (
                f"LoadPlan.{col} was not carried through duplicate_plan "
                f"(source={getattr(plan, col)!r}, copy={getattr(copy, col)!r})"
            )

        # ── Step-level coverage. ───────────────────────────────────────────────
        assert len(copy.load_steps) == 1
        copy_step = copy.load_steps[0]
        assert copy_step.id != step.id  # new id
        assert copy_step.load_plan_id == copy.id  # remapped FK
        step_cols = {c.name for c in LoadStep.__table__.columns} - EXPECTED_STEP_EXCLUDED
        for col in step_cols:
            assert getattr(copy_step, col) == getattr(step, col), (
                f"LoadStep.{col} was not carried through duplicate_plan "
                f"(source={getattr(step, col)!r}, copy={getattr(copy_step, col)!r})"
            )


# ── Start run ──────────────────────────────────────────────────────────────────


def test_start_run_returns_201(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.post(f"/api/load-plans/{plan_id}/run")
    assert resp.status_code == 201
    body = resp.json()
    assert body["load_plan_id"] == plan_id
    assert body["status"] == "pending"
    assert body["initiated_by"] == "test-user@example.com"


def test_start_run_plan_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/bad-plan/run", json={})
    assert resp.status_code == 404


# ── SFBL-166: input_from_step_id FK remap on duplicate ───────────────────────


@pytest.mark.asyncio
async def test_duplicate_plan_remaps_input_from_step_id():
    """A duplicated plan's ``input_from_step_id`` references must point at
    the cloned upstream steps, not the source plan's step IDs."""
    import uuid

    from app.models.connection import Connection
    from app.models.load_plan import LoadPlan
    from app.models.load_step import LoadStep, Operation
    from app.services.load_plan_service import duplicate_plan
    from tests.conftest import _TestSession

    async with _TestSession() as db:
        conn = Connection(
            id=str(uuid.uuid4()),
            name="Test Org",
            instance_url="https://test.salesforce.com",
            login_url="https://login.salesforce.com",
            client_id="cid",
            private_key="encrypted",
            username="user@example.com",
            is_sandbox=True,
        )
        db.add(conn)
        await db.flush()

        plan = LoadPlan(
            id=str(uuid.uuid4()),
            connection_id=conn.id,
            name="Source",
        )
        db.add(plan)
        await db.flush()

        upstream = LoadStep(
            id=str(uuid.uuid4()),
            load_plan_id=plan.id,
            sequence=1,
            object_name="Account",
            operation=Operation.query,
            soql="SELECT Id FROM Account",
            name="accounts_q",
        )
        downstream = LoadStep(
            id=str(uuid.uuid4()),
            load_plan_id=plan.id,
            sequence=2,
            object_name="Account",
            operation=Operation.delete,
            input_from_step_id=upstream.id,
        )
        db.add_all([upstream, downstream])
        await db.commit()

        copy = await duplicate_plan(db, plan.id)

        copy_steps = sorted(copy.load_steps, key=lambda s: s.sequence)
        copy_upstream, copy_downstream = copy_steps

        # Remapped: downstream's FK points at the *clone* of upstream, not the source.
        assert copy_downstream.input_from_step_id == copy_upstream.id
        assert copy_downstream.input_from_step_id != upstream.id
        # Upstream clone has no input_from of its own.
        assert copy_upstream.input_from_step_id is None
        # Names are carried through.
        assert copy_upstream.name == "accounts_q"
