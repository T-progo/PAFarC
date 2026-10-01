from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PharmacistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    crf: str
    login: str
    active: bool
    created_at: datetime
