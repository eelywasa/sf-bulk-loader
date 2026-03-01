from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ConnectionBase(BaseModel):
    name: str
    instance_url: str
    login_url: str
    client_id: str
    username: str
    is_sandbox: bool = False


class ConnectionCreate(ConnectionBase):
    private_key: str  # Plain PEM; encrypted at rest before DB storage


class ConnectionUpdate(BaseModel):
    name: Optional[str] = None
    instance_url: Optional[str] = None
    login_url: Optional[str] = None
    client_id: Optional[str] = None
    private_key: Optional[str] = None  # Plain PEM; encrypted at rest
    username: Optional[str] = None
    is_sandbox: Optional[bool] = None


class ConnectionResponse(ConnectionBase):
    """Secrets (private_key, access_token) are intentionally omitted."""

    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    instance_url: Optional[str] = None
