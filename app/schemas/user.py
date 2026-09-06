import uuid

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str


class LoginResponse(BaseModel):
    username: str
    authenticated: bool = True
