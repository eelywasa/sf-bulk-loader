"""SFBL-403: an empty ``object_name`` must be rejected on write and survivable on read.

A step whose ``object_name`` is the empty string passes the model's
``nullable=False`` constraint and, before this ticket, every schema check —
then fails only once a run reaches Bulk API job creation, long after the
operator has left the editor. The incident plan behind SFBL-400 carried exactly
such a row.

The two halves of this file pull in opposite directions on purpose:

* **Writes** must be refused, including the writes that never present the field
  (a PATCH that omits it, a plan duplication that clones it).
* **Reads** must keep working, because the row already exists and the operator
  cannot fix what the API will not return. Constraining ``LoadStepBase`` would
  satisfy the first half and break the second — that is what
  ``test_get_plan_with_empty_object_name_step_returns_200`` catches.
"""

import uuid

import pytest

from app.models.connection import Connection
from app.models.load_plan import LoadPlan
from app.models.load_step import LoadStep, Operation
from tests.conftest import _TestSession

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "cid",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----",
    "username": "u@example.com",
    "is_sandbox": False,
}

_STEP = {
    "sequence": 1,
    "object_name": "Account",
    "operation": "insert",
    "csv_file_pattern": "accounts_*.csv",
    "partition_size": 5000,
}


def _plan_id(auth_client) -> str:
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    return auth_client.post(
        "/api/load-plans/", json={"name": "Plan", "connection_id": conn_id}
    ).json()["id"]


async def _seed_plan_with_empty_object_name() -> tuple[str, str]:
    """Insert a plan carrying one legacy step with ``object_name == ""``.

    Written straight through the ORM: the whole point is that this row cannot
    be created through the API any more, and it still has to be readable.
    """
    async with _TestSession() as db:
        conn = Connection(
            id=str(uuid.uuid4()),
            name="Legacy Org",
            instance_url="https://legacy.my.salesforce.com",
            login_url="https://login.salesforce.com",
            client_id="cid",
            private_key="encrypted",
            username="legacy@example.com",
            is_sandbox=False,
        )
        db.add(conn)
        plan = LoadPlan(id=str(uuid.uuid4()), name="Legacy Plan", connection_id=conn.id)
        db.add(plan)
        step = LoadStep(
            id=str(uuid.uuid4()),
            load_plan_id=plan.id,
            sequence=1,
            name="Account v2",
            object_name="",
            operation=Operation.upsert,
            external_id_field="Ext_Id__c",
            csv_file_pattern="accounts_*.csv",
            partition_size=5000,
        )
        db.add(step)
        await db.commit()
        return plan.id, step.id


# ── Writes are refused ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", " ", "\t", "   \n  "])
def test_create_step_rejects_blank_object_name(auth_client, value):
    """Whitespace-only values are rejected, not just the empty string.

    Falsification: drop the ``.strip()`` from ``_validate_object_name`` and keep
    only an emptiness check, and the three whitespace cases here start
    returning 201.
    """
    plan_id = _plan_id(auth_client)
    resp = auth_client.post(
        f"/api/load-plans/{plan_id}/steps", json={**_STEP, "object_name": value}
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_update_step_rejects_blank_object_name(auth_client, value):
    plan_id = _plan_id(auth_client)
    step_id = auth_client.post(f"/api/load-plans/{plan_id}/steps", json=_STEP).json()["id"]

    resp = auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}", json={"object_name": value}
    )
    assert resp.status_code == 422, resp.text

    # The persisted value is untouched by the rejected write.
    steps = auth_client.get(f"/api/load-plans/{plan_id}").json()["load_steps"]
    assert [s["object_name"] for s in steps if s["id"] == step_id] == ["Account"]


def test_update_step_omitting_object_name_stays_valid(auth_client):
    """Partial updates are unaffected: omitting the field is not "clearing" it."""
    plan_id = _plan_id(auth_client)
    step_id = auth_client.post(f"/api/load-plans/{plan_id}/steps", json=_STEP).json()["id"]

    resp = auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}", json={"partition_size": 1000}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["object_name"] == "Account"
    assert resp.json()["partition_size"] == 1000


def test_object_name_is_persisted_trimmed(auth_client):
    plan_id = _plan_id(auth_client)
    created = auth_client.post(
        f"/api/load-plans/{plan_id}/steps", json={**_STEP, "object_name": "  Account  "}
    )
    assert created.status_code == 201, created.text
    assert created.json()["object_name"] == "Account"

    step_id = created.json()["id"]
    updated = auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}", json={"object_name": "\tContact "}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["object_name"] == "Contact"


# ── Legacy rows stay readable and repairable ─────────────────────────────────


async def test_get_plan_with_empty_object_name_step_returns_200(auth_client):
    """Reads of the offending row must keep working.

    Falsification: move the constraint onto ``LoadStepBase`` — which
    ``LoadStepResponse`` inherits — and this returns 500, because Pydantic
    rejects the persisted row on the way *out*. The operator would then have no
    way to see the bad step, let alone fix it.
    """
    plan_id, step_id = await _seed_plan_with_empty_object_name()

    resp = auth_client.get(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 200, resp.text

    steps = resp.json()["load_steps"]
    assert [s["id"] for s in steps] == [step_id]
    assert steps[0]["object_name"] == ""

    # The list endpoint must survive it too — it serialises the same schema.
    assert auth_client.get("/api/load-plans/").status_code == 200


async def test_patch_omitting_object_name_on_a_blank_row_returns_422(auth_client):
    """The merged-effective-state check (D3.1a).

    Falsification: delete the ``effective_object_name`` block from
    ``update_step`` and this returns 200 — the invalid row survives an edit
    while every other criterion in this file still passes, because the schema
    validator only ever sees what the client sent.
    """
    plan_id, step_id = await _seed_plan_with_empty_object_name()

    resp = auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}", json={"partition_size": 2000}
    )
    assert resp.status_code == 422, resp.text
    assert "object_name" in resp.text


async def test_operator_can_repair_the_blank_row(auth_client):
    """Supplying the object in the same PATCH is the documented remedy."""
    plan_id, step_id = await _seed_plan_with_empty_object_name()

    resp = auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}",
        json={"object_name": "Account", "partition_size": 2000},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["object_name"] == "Account"
    assert resp.json()["partition_size"] == 2000


async def test_duplicate_plan_refuses_to_clone_a_blank_object_name(auth_client):
    """Duplication copies columns straight into the ORM, bypassing Pydantic.

    It is the one creation path a schema constraint cannot reach, so it gets an
    explicit guard rather than silently minting a second invalid plan.
    """
    plan_id, step_id = await _seed_plan_with_empty_object_name()

    resp = auth_client.post(f"/api/load-plans/{plan_id}/duplicate")
    assert resp.status_code == 422, resp.text
    assert step_id in resp.text  # names the offending step

    # And nothing was created.
    plans = auth_client.get("/api/load-plans/").json()
    assert [p["id"] for p in plans] == [plan_id]


async def test_duplicate_plan_still_works_once_repaired(auth_client):
    plan_id, step_id = await _seed_plan_with_empty_object_name()
    auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}", json={"object_name": "Account"}
    )

    resp = auth_client.post(f"/api/load-plans/{plan_id}/duplicate")
    assert resp.status_code == 201, resp.text
    assert resp.json()["load_steps"][0]["object_name"] == "Account"


# ── Startup surfacing (D3.2) ─────────────────────────────────────────────────


async def test_startup_scan_names_affected_step_ids(caplog):
    """The row is never backfilled or deleted, so the log is the only signal."""
    import logging

    from app.main import _log_steps_with_empty_object_name

    _, step_id = await _seed_plan_with_empty_object_name()

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _log_steps_with_empty_object_name()

    assert any(step_id in record.getMessage() for record in caplog.records), (
        f"startup scan did not name the affected step: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


async def test_startup_scan_leaves_the_row_untouched():
    """D3.2: surface, never migrate."""
    from app.main import _log_steps_with_empty_object_name

    _, step_id = await _seed_plan_with_empty_object_name()
    await _log_steps_with_empty_object_name()

    async with _TestSession() as db:
        step = await db.get(LoadStep, step_id)
        assert step is not None, "the scan must not delete the row"
        assert step.object_name == "", "the scan must not backfill the row"


async def test_startup_scan_is_silent_when_every_step_is_valid(auth_client, caplog):
    import logging

    from app.main import _log_steps_with_empty_object_name

    plan_id = _plan_id(auth_client)
    auth_client.post(f"/api/load-plans/{plan_id}/steps", json=_STEP)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _log_steps_with_empty_object_name()

    assert not [r for r in caplog.records if "object_name" in r.getMessage()]
