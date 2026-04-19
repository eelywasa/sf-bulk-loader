"""Input Connections API — CRUD and S3 connectivity test."""

import asyncio
import logging
from typing import List, Optional

import boto3
import botocore.exceptions
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.input_connection import InputConnection
from app.models.load_plan import LoadPlan
from app.schemas.input_connection import (
    InputConnectionCreate,
    InputConnectionResponse,
    InputConnectionTestResponse,
    InputConnectionUpdate,
)
from app.services.auth import get_current_user
from app.utils.encryption import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/input-connections",
    tags=["input-connections"],
    dependencies=[Depends(get_current_user)],
)


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_or_404(ic_id: str, db: AsyncSession) -> InputConnection:
    ic = await db.get(InputConnection, ic_id)
    if ic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Input connection not found")
    return ic


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("/", response_model=List[InputConnectionResponse])
async def list_input_connections(
    direction: Optional[str] = Query(default=None, description="Filter by direction. 'in' returns in+both; 'out' returns out+both; other values match exactly."),
    db: AsyncSession = Depends(get_db),
) -> List[InputConnection]:
    stmt = select(InputConnection).order_by(InputConnection.created_at.desc())
    if direction is not None:
        if direction == "out":
            stmt = stmt.where(InputConnection.direction.in_(["out", "both"]))
        elif direction == "in":
            stmt = stmt.where(InputConnection.direction.in_(["in", "both"]))
        else:
            stmt = stmt.where(InputConnection.direction == direction)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/", response_model=InputConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_input_connection(
    data: InputConnectionCreate, db: AsyncSession = Depends(get_db)
) -> InputConnection:
    ic = InputConnection(
        name=data.name,
        provider=data.provider,
        bucket=data.bucket,
        root_prefix=data.root_prefix,
        region=data.region,
        direction=data.direction,
        access_key_id=encrypt_secret(data.access_key_id),
        secret_access_key=encrypt_secret(data.secret_access_key),
        session_token=encrypt_secret(data.session_token) if data.session_token else None,
    )
    db.add(ic)
    await db.commit()
    await db.refresh(ic)
    return ic


@router.get("/{ic_id}", response_model=InputConnectionResponse)
async def get_input_connection(ic_id: str, db: AsyncSession = Depends(get_db)) -> InputConnection:
    return await _get_or_404(ic_id, db)


@router.put("/{ic_id}", response_model=InputConnectionResponse)
async def update_input_connection(
    ic_id: str,
    data: InputConnectionUpdate,
    db: AsyncSession = Depends(get_db),
) -> InputConnection:
    ic = await _get_or_404(ic_id, db)
    update_data = data.model_dump(exclude_unset=True)
    for secret_field in ("access_key_id", "secret_access_key", "session_token"):
        if secret_field in update_data and update_data[secret_field] is not None:
            update_data[secret_field] = encrypt_secret(update_data[secret_field])
    for field, value in update_data.items():
        setattr(ic, field, value)
    await db.commit()
    await db.refresh(ic)
    return ic


@router.delete("/{ic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_input_connection(ic_id: str, db: AsyncSession = Depends(get_db)) -> None:
    ic = await _get_or_404(ic_id, db)

    # Check if any load plans reference this connection as output_connection_id
    plan_result = await db.execute(
        select(LoadPlan).where(LoadPlan.output_connection_id == ic_id).limit(1)
    )
    if plan_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Storage connection is used as an output destination by one or more load plans",
        )

    try:
        await db.delete(ic)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Input connection is referenced by one or more load steps",
        )


@router.post("/{ic_id}/test", response_model=InputConnectionTestResponse)
async def test_input_connection(
    ic_id: str, db: AsyncSession = Depends(get_db)
) -> InputConnectionTestResponse:
    """Verify S3 credentials: always checks read access; also checks write access for output connections."""
    ic = await _get_or_404(ic_id, db)
    test_write = ic.direction in ("out", "both")
    try:
        access_key_id = decrypt_secret(ic.access_key_id)
        secret_access_key = decrypt_secret(ic.secret_access_key)
        session_token = decrypt_secret(ic.session_token) if ic.session_token else None

        client_kwargs = dict(
            service_name="s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=ic.region,
        )
        if session_token:
            client_kwargs["aws_session_token"] = session_token

        prefix = ic.root_prefix or ""
        normalized_prefix = prefix.rstrip("/") + "/" if prefix else ""

        def _test():
            client = boto3.client(**client_kwargs)
            client.list_objects_v2(Bucket=ic.bucket, Prefix=normalized_prefix, MaxKeys=1)
            if test_write:
                test_key = f"{normalized_prefix}.sfbl-write-test"
                client.put_object(Bucket=ic.bucket, Key=test_key, Body=b"")
                client.delete_object(Bucket=ic.bucket, Key=test_key)

        await asyncio.to_thread(_test)
        msg = (
            "S3 connection successful (read and write access verified)"
            if test_write
            else "S3 connection successful (read access verified)"
        )
        return InputConnectionTestResponse(success=True, message=msg)
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return InputConnectionTestResponse(success=False, message=f"S3 error [{code}]: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error testing input connection %s", ic_id)
        return InputConnectionTestResponse(success=False, message=f"Connection failed: {exc}")
