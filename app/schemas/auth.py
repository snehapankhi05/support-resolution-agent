"""Request bodies for login. Unknown fields are rejected."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class MeResponse(BaseModel):
    actor: str
    id: UUID
    email: str
    name: str
    role: str
