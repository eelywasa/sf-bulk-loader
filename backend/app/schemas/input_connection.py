from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class InputConnectionBase(BaseModel):
    name: str
    provider: str  # 's3'
    bucket: str
    root_prefix: Optional[str] = None
    region: Optional[str] = None
    direction: Literal["in", "out", "both"] = "in"


class InputConnectionCreate(InputConnectionBase):
    access_key_id: str        # plain; encrypted before DB storage
    secret_access_key: str    # plain; encrypted before DB storage
    session_token: Optional[str] = None  # plain; encrypted if provided


class InputConnectionUpdate(BaseModel):
    name: Optional[str] = None
    bucket: Optional[str] = None
    root_prefix: Optional[str] = None
    region: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    direction: Optional[Literal["in", "out", "both"]] = None


class InputConnectionResponse(InputConnectionBase):
    """Secrets (access_key_id, secret_access_key, session_token) intentionally omitted."""

    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InputConnectionTestResponse(BaseModel):
    success: bool
    message: str
