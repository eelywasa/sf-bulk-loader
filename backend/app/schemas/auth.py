from typing import Optional

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    username: Optional[str]
    email: Optional[str]
    display_name: Optional[str]
    role: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AuthConfigResponse(BaseModel):
    saml_enabled: bool
