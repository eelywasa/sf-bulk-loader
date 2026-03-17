"""Connections API — CRUD and connectivity test for Salesforce org credentials."""

import logging
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.connection import Connection
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionTestResponse,
    ConnectionUpdate,
)
from app.services.auth import get_current_user
from app.services.salesforce_auth import AuthError, decrypt_private_key, encrypt_private_key, get_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connections", tags=["connections"], dependencies=[Depends(get_current_user)])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_or_404(connection_id: str, db: AsyncSession) -> Connection:
    conn = await db.get(Connection, connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return conn


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("/", response_model=List[ConnectionResponse])
async def list_connections(db: AsyncSession = Depends(get_db)) -> List[Connection]:
    result = await db.execute(select(Connection).order_by(Connection.created_at.desc()))
    return list(result.scalars().all())


@router.post("/", response_model=ConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_connection(data: ConnectionCreate, db: AsyncSession = Depends(get_db)) -> Connection:
    conn = Connection(
        name=data.name,
        instance_url=data.instance_url,
        login_url=data.login_url,
        client_id=data.client_id,
        private_key=encrypt_private_key(data.private_key),
        username=data.username,
        is_sandbox=data.is_sandbox,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(connection_id: str, db: AsyncSession = Depends(get_db)) -> Connection:
    return await _get_or_404(connection_id, db)


@router.put("/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: str,
    data: ConnectionUpdate,
    db: AsyncSession = Depends(get_db),
) -> Connection:
    conn = await _get_or_404(connection_id, db)
    update_data = data.model_dump(exclude_unset=True)
    if "private_key" in update_data:
        update_data["private_key"] = encrypt_private_key(update_data["private_key"])
    for field, value in update_data.items():
        setattr(conn, field, value)
    await db.commit()
    await db.refresh(conn)
    return conn


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: str, db: AsyncSession = Depends(get_db)) -> None:
    conn = await _get_or_404(connection_id, db)
    await db.delete(conn)
    await db.commit()


@router.get("/{connection_id}/objects", response_model=List[str])
async def list_connection_objects(
    connection_id: str, db: AsyncSession = Depends(get_db)
) -> List[str]:
    """Return sorted SObject API names that can be used as load targets."""
    conn = await _get_or_404(connection_id, db)
    try:
        token = await get_access_token(db, conn)
        url = f"{conn.instance_url.rstrip('/')}/services/data/{settings.sf_api_version}/sobjects/"
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        sobjects = resp.json().get("sobjects", [])
        return sorted(
            obj["name"]
            for obj in sobjects
            if obj.get("createable") or obj.get("updateable") or obj.get("deletable")
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to list objects for connection %s", connection_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Salesforce API error: {exc}",
        )


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
async def test_connection(connection_id: str, db: AsyncSession = Depends(get_db)) -> ConnectionTestResponse:
    """Attempt authentication and a lightweight Salesforce API call to verify credentials."""
    conn = await _get_or_404(connection_id, db)
    try:
        token = await get_access_token(db, conn)
        url = f"{conn.instance_url.rstrip('/')}/services/data/{settings.sf_api_version}/sobjects/"
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            return ConnectionTestResponse(
                success=True,
                message="Connection successful",
                instance_url=conn.instance_url,
            )
        return ConnectionTestResponse(
            success=False,
            message=f"Salesforce API returned HTTP {resp.status_code}",
        )
    except AuthError as exc:
        return ConnectionTestResponse(success=False, message=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error testing connection %s", connection_id)
        return ConnectionTestResponse(success=False, message=f"Connection failed: {exc}")
