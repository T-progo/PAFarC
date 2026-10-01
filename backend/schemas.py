from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


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


class PatientCreate(BaseModel):
    full_name: str = Field(max_length=200)
    date_of_birth: date
    cpf: str = Field(max_length=20)


class PatientSearch(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    date_of_birth: date
    cpf: str
    created_at: datetime
    updated_at: datetime
