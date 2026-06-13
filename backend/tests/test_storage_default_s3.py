"""Tests for first-party S3 default storage resolution (SFBL-385 / SFBL-386).

On ``input_storage_mode == "s3"`` (the aws_hosted default) the implicit/default
Input and Output source must resolve to the deployment's first-party S3 buckets
via the *keyless* ECS task-role chain — never the ephemeral container filesystem.
Desktop / self_hosted (``input_storage_mode == "local"``) must be unchanged, and
explicit BYO-keys connections must keep decrypting and passing their keys on
every profile.

Each test is falsifiable: the keyless proofs fail if any AWS key kwarg is passed
or ``decrypt_secret`` is called; the routing proofs fail if the resolver returns
the filesystem provider; the no-regression proofs fail if the s3 branch leaks
onto a filesystem profile.
"""

from __future__ import annotations

import json
import uuid
import logging
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.models.input_connection import InputConnection
from app.observability.events import OutcomeCode
from app.services import input_storage, output_storage
from app.services.input_storage import (
    LOCAL_OUTPUT_SOURCE,
    LocalInputStorage,
    S3InputStorage,
    _s3_outcome_code,
    get_storage,
    resolve_storage_locations,
)
from app.services.output_storage import (
    LocalOutputStorage,
    S3OutputStorage,
    get_output_storage,
)
from app.utils.encryption import encrypt_secret
from tests.conftest import _TestSession


# ── Helpers ────────────────────────────────────────────────────────────────────


def _enable_s3_mode(monkeypatch, *, input_prefix=None, output_prefix=None) -> None:
    """Flip the shared settings singleton into aws_hosted-style s3 storage mode.

    ``input_storage`` and ``output_storage`` import the same settings object, so
    patching it once affects both resolvers. monkeypatch restores after the test.
    """
    monkeypatch.setattr(input_storage.settings, "input_storage_mode", "s3")
    monkeypatch.setattr(input_storage.settings, "s3_input_bucket", "first-party-input")
    monkeypatch.setattr(input_storage.settings, "s3_output_bucket", "first-party-output")
    monkeypatch.setattr(input_storage.settings, "s3_bucket_region", "eu-west-1")
    monkeypatch.setattr(input_storage.settings, "s3_input_prefix", input_prefix)
    monkeypatch.setattr(input_storage.settings, "s3_output_prefix", output_prefix)


def _capture_boto3_client(monkeypatch) -> dict:
    """Patch ``boto3.client`` (used by build_s3_client) to record its kwargs."""
    captured: dict = {}

    def fake_client(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(input_storage.boto3, "client", fake_client)
    return captured


def _guard_decrypt(monkeypatch) -> MagicMock:
    """Replace ``decrypt_secret`` so any call is an immediate, visible failure."""
    spy = MagicMock(side_effect=AssertionError("decrypt_secret must not be called on the keyless path"))
    monkeypatch.setattr(input_storage, "decrypt_secret", spy)
    monkeypatch.setattr(output_storage, "decrypt_secret", spy)
    return spy


# ── Keyless proof: input (QA #5) ────────────────────────────────────────────────


@pytest.mark.parametrize("source", [None, "", "local"])
async def test_default_input_source_resolves_keyless_s3(monkeypatch, source):
    captured = _capture_boto3_client(monkeypatch)
    decrypt = _guard_decrypt(monkeypatch)
    _enable_s3_mode(monkeypatch)

    storage = await get_storage(source, db=None)

    assert isinstance(storage, S3InputStorage)
    assert storage._bucket == "first-party-input"
    # Keyless: region only, no credential kwargs.
    assert captured.get("region_name") == "eu-west-1"
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured
    assert "aws_session_token" not in captured
    decrypt.assert_not_called()


async def test_local_output_source_resolves_keyless_s3_output_bucket(monkeypatch):
    captured = _capture_boto3_client(monkeypatch)
    decrypt = _guard_decrypt(monkeypatch)
    _enable_s3_mode(monkeypatch)

    storage = await get_storage(LOCAL_OUTPUT_SOURCE, db=None)

    assert isinstance(storage, S3InputStorage)
    assert storage._bucket == "first-party-output"
    assert captured.get("region_name") == "eu-west-1"
    assert "aws_access_key_id" not in captured
    decrypt.assert_not_called()


# ── Keyless proof: output / run-output durability (QA #2) ────────────────────────


async def test_default_run_output_resolves_keyless_s3(monkeypatch):
    """get_output_storage(None) under s3 mode → keyless S3, never LocalOutputStorage."""
    captured = _capture_boto3_client(monkeypatch)
    decrypt = _guard_decrypt(monkeypatch)
    _enable_s3_mode(monkeypatch)

    storage = await get_output_storage(None, db=None)

    assert isinstance(storage, S3OutputStorage)
    assert not isinstance(storage, LocalOutputStorage)
    # Artifact refs are durable s3:// URIs on the first-party output bucket.
    assert storage.resolve_uri("run/step/partition_0.csv").startswith(
        "s3://first-party-output/"
    )
    assert captured.get("region_name") == "eu-west-1"
    assert "aws_access_key_id" not in captured
    decrypt.assert_not_called()


async def test_empty_string_output_connection_also_resolves_s3(monkeypatch):
    _capture_boto3_client(monkeypatch)
    _enable_s3_mode(monkeypatch)
    storage = await get_output_storage("", db=None)
    assert isinstance(storage, S3OutputStorage)


# ── No-regression on filesystem profiles, all sentinels (QA #7) ──────────────────


@pytest.mark.parametrize("source", [None, "", "local"])
async def test_default_input_source_stays_local_on_filesystem_profile(source):
    """Default profile is self_hosted (input_storage_mode=local) — unchanged."""
    assert input_storage.settings.input_storage_mode == "local"
    storage = await get_storage(source, db=None)
    assert isinstance(storage, LocalInputStorage)
    assert storage.provider == "local"


async def test_local_output_sentinel_stays_local_on_filesystem_profile():
    storage = await get_storage(LOCAL_OUTPUT_SOURCE, db=None)
    assert isinstance(storage, LocalInputStorage)


@pytest.mark.parametrize("conn_id", [None, ""])
async def test_default_run_output_stays_local_on_filesystem_profile(conn_id):
    storage = await get_output_storage(conn_id, db=None)
    assert isinstance(storage, LocalOutputStorage)


# ── No-regression: explicit BYO connections still decrypt + pass keys ────────────


async def test_explicit_input_connection_uses_byo_keys_even_in_s3_mode(monkeypatch):
    """An explicit connection id is the non-default path — unchanged under s3 mode.

    Falsification: if the s3-default branch hijacked explicit connection ids, the
    keyless client would be built and decrypt_secret would not be called.
    """
    captured = _capture_boto3_client(monkeypatch)
    _enable_s3_mode(monkeypatch)

    conn_id = str(uuid.uuid4())
    async with _TestSession() as session:
        session.add(
            InputConnection(
                id=conn_id,
                name="External cross-account bucket",
                provider="s3",
                direction="in",
                bucket="external-bucket",
                region="us-east-1",
                access_key_id=encrypt_secret("AKIAEXAMPLE"),
                secret_access_key=encrypt_secret("super-secret"),
            )
        )
        await session.commit()

        storage = await get_storage(conn_id, db=session)

    assert isinstance(storage, S3InputStorage)
    assert storage._bucket == "external-bucket"
    # BYO path: the decrypted keys were passed to boto3.
    assert captured.get("aws_access_key_id") == "AKIAEXAMPLE"
    assert captured.get("aws_secret_access_key") == "super-secret"


# ── Prefix scoping (QA #8) ───────────────────────────────────────────────────────


async def test_input_prefix_scopes_listing(monkeypatch):
    list_calls: dict = {}

    class _PrefixCaptureClient:
        def list_objects_v2(self, **kwargs):
            list_calls.update(kwargs)
            return {"CommonPrefixes": [], "Contents": []}

    monkeypatch.setattr(
        input_storage.boto3, "client", lambda **_kw: _PrefixCaptureClient()
    )
    _enable_s3_mode(monkeypatch, input_prefix="loads/2026")

    storage = await get_storage("local", db=None)
    storage.list_entries()

    assert list_calls["Prefix"].startswith("loads/2026/")


async def test_output_prefix_scopes_resolve_uri(monkeypatch):
    _capture_boto3_client(monkeypatch)
    _enable_s3_mode(monkeypatch, output_prefix="results")
    storage = await get_output_storage(None, db=None)
    assert storage.resolve_uri("a/b.csv") == "s3://first-party-output/results/a/b.csv"


# ── S3 failure → canonical outcome codes (QA #8) ─────────────────────────────────


@pytest.mark.parametrize(
    "code,expected",
    [
        ("AccessDenied", OutcomeCode.STORAGE_ERROR),
        ("NoSuchBucket", OutcomeCode.STORAGE_ERROR),
        ("NoSuchKey", OutcomeCode.STORAGE_ERROR),
        ("SlowDown", OutcomeCode.RATE_LIMITED),
        ("Throttling", OutcomeCode.RATE_LIMITED),
    ],
)
def test_s3_outcome_code_mapping(code, expected):
    exc = ClientError({"Error": {"Code": code, "Message": code}}, "ListObjectsV2")
    assert _s3_outcome_code(exc) == expected


async def test_s3_list_access_denied_logs_outcome_code(monkeypatch, caplog):
    class _DeniedClient:
        def list_objects_v2(self, **_kwargs):
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "ListObjectsV2",
            )

    monkeypatch.setattr(input_storage.boto3, "client", lambda **_kw: _DeniedClient())
    _enable_s3_mode(monkeypatch)
    storage = await get_storage("local", db=None)

    with caplog.at_level(logging.WARNING, logger="app.services.input_storage"):
        with pytest.raises(input_storage.InputStorageError):
            storage.list_entries()

    assert any(
        getattr(r, "outcome_code", None) == OutcomeCode.STORAGE_ERROR
        for r in caplog.records
    )


# ── Storage-location metadata, no credentials (QA #4) ────────────────────────────


async def test_storage_locations_s3_mode_no_credentials(monkeypatch):
    _enable_s3_mode(monkeypatch, input_prefix="in", output_prefix="out")
    locs = await resolve_storage_locations()

    assert locs["input"].provider == "s3"
    assert locs["input"].bucket == "first-party-input"
    assert locs["input"].region == "eu-west-1"
    assert locs["input"].prefix == "in"
    assert locs["input"].uri == "s3://first-party-input/in/"
    assert locs["output"].uri == "s3://first-party-output/out/"

    # No credentials, tokens, or presigned URLs anywhere in the payload.
    blob = json.dumps({k: vars(v) for k, v in locs.items()}).lower()
    for needle in ("access_key", "secret", "session_token", "aws_", "akia", "x-amz", "signature"):
        assert needle not in blob


async def test_storage_locations_local_mode():
    """Default self_hosted profile → local provider, no bucket/region/prefix."""
    locs = await resolve_storage_locations()
    assert locs["input"].provider == "local"
    assert locs["output"].provider == "local"
    assert locs["input"].bucket is None
    assert locs["input"].region is None
