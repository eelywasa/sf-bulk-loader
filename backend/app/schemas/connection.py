from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class ConnectionBase(BaseModel):
    name: str
    instance_url: str
    login_url: str

    @field_validator("instance_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    client_id: str
    username: str
    is_sandbox: bool = False


class ConnectionCreate(ConnectionBase):
    private_key: str  # Plain PEM; encrypted at rest before DB storage


class ConnectionUpdate(BaseModel):
    name: Optional[str] = None
    instance_url: Optional[str] = None

    @field_validator("instance_url")
    @classmethod
    def strip_trailing_slash(cls, v: Optional[str]) -> Optional[str]:
        return v.rstrip("/") if v is not None else v

    login_url: Optional[str] = None
    client_id: Optional[str] = None
    private_key: Optional[str] = None  # Plain PEM; encrypted at rest
    username: Optional[str] = None
    is_sandbox: Optional[bool] = None


class ConnectionPublic(BaseModel):
    """Public connection shape — no credential fields.

    Returned to users holding ``connections.view`` but not ``connections.view_credentials``.
    Contains only the non-sensitive identification fields.
    """

    id: str
    name: str
    instance_url: str
    login_url: str
    username: str
    is_sandbox: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionResponse(ConnectionBase):
    """Full connection shape — credential fields included (minus private_key / access_token).

    Returned to users holding ``connections.view_credentials``.
    Secrets (private_key, access_token) are intentionally omitted from both shapes.
    """

    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    instance_url: Optional[str] = None
