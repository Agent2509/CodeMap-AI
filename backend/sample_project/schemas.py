from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr


class TaskCreate(BaseModel):
    title: str
    owner_id: int


class TaskUpdate(BaseModel):
    title: str | None = None
    done: bool | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    done: bool
    owner_id: int


class UserCreate(BaseModel):
    email: EmailStr
