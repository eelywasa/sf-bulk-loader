from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator


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

    @field_validator("access_key_id", "secret_access_key")
    @classmethod
    def _reject_blank_credentials(cls, v: str, info) -> str:
        """Reject blank credentials (SFBL-385, Codex P2).

        A blank stored key would otherwise flow into ``build_s3_client``; the
        fail-closed fix there now passes it to boto3 (which rejects it), but
        rejecting at the boundary is the primary guard so a misconfigured BYO
        connection never reaches the keyless path on aws_hosted.
        """
        if v is None or not v.strip():
            raise ValueError(f"{info.field_name} must not be blank for an S3 connection")
        return v


class InputConnectionUpdate(BaseModel):
    name: Optional[str] = None
    bucket: Optional[str] = None
    root_prefix: Optional[str] = None
    region: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    direction: Optional[Literal["in", "out", "both"]] = None

    @field_validator("access_key_id", "secret_access_key")
    @classmethod
    def _reject_blank_credentials(cls, v: Optional[str], info) -> Optional[str]:
        """``None`` leaves the field unchanged; an explicit blank string is rejected."""
        if v is not None and not v.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return v


class InputConnectionResponse(InputConnectionBase):
    """Secrets (access_key_id, secret_access_key, session_token) intentionally omitted."""

    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InputConnectionTestResponse(BaseModel):
    success: bool
    message: str
