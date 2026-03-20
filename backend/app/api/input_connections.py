"""Input Connections API — CRUD and S3 connectivity test."""

import asyncio
import logging
from typing import List

import boto3
import botocore.exceptions
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.input_connection import InputConnection
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
async def list_input_connections(db: AsyncSession = Depends(get_db)) -> List[InputConnection]:
    result = await db.execute(select(InputConnection).order_by(InputConnection.created_at.desc()))
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
    """Attempt an S3 ListObjectsV2 call to verify stored credentials."""
    ic = await _get_or_404(ic_id, db)
    try:
        access_key_id = decrypt_secret(ic.access_key_id)
        secret_access_key = decrypt_secret(ic.secret_access_key)
        session_token = decrypt_secret(ic.session_token) if ic.session_token else None

        kwargs = dict(
            service_name="s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=ic.region,
        )
        if session_token:
            kwargs["aws_session_token"] = session_token

        def _list():
            client = boto3.client(**kwargs)
            return client.list_objects_v2(
                Bucket=ic.bucket,
                Prefix=ic.root_prefix or "",
                MaxKeys=1,
            )

        await asyncio.to_thread(_list)
        return InputConnectionTestResponse(success=True, message="S3 connection successful")
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return InputConnectionTestResponse(success=False, message=f"S3 error [{code}]: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error testing input connection %s", ic_id)
        return InputConnectionTestResponse(success=False, message=f"Connection failed: {exc}")
