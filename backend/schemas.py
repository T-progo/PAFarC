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


SOAP_SECTION_MAX = 20000


class PharmacistSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    crf: str


class ConsultationCreate(BaseModel):
    consultation_date: date | None = None  # defaults to today


class SoapIn(BaseModel):
    subjective: str = Field(default="", max_length=SOAP_SECTION_MAX)
    objective: str = Field(default="", max_length=SOAP_SECTION_MAX)
    assessment: str = Field(default="", max_length=SOAP_SECTION_MAX)
    plan: str = Field(default="", max_length=SOAP_SECTION_MAX)


class SoapOut(SoapIn):
    model_config = ConfigDict(from_attributes=True)


class ExamResultIn(BaseModel):
    exam_name: str = Field(max_length=200)
    result: str = Field(max_length=200)
    unit: str = Field(default="", max_length=50)
    reference_range: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)


class ExamResultOut(ExamResultIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    consultation_id: int
    created_at: datetime


class ConsultationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    consultation_date: date
    pharmacist: PharmacistSummary
    created_at: datetime
    updated_at: datetime


class GeneratedDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_type: str
    created_at: datetime
    pharmacist: PharmacistSummary


class ConsultationOut(ConsultationSummary):
    soap: SoapOut | None
    exam_results: list[ExamResultOut]
    documents: list[GeneratedDocumentOut]


class PrescriptionItem(BaseModel):
    medication: str = Field(max_length=200)
    dosage: str = Field(default="", max_length=100)
    route: str = Field(default="", max_length=100)
    posology: str = Field(default="", max_length=500)
    duration: str = Field(default="", max_length=100)
    guidance: str = Field(default="", max_length=3000)


class PrescriptionIn(BaseModel):
    items: list[PrescriptionItem] = Field(min_length=1, max_length=30)


class ExamRequestIn(BaseModel):
    exams: list[str] = Field(min_length=1, max_length=50)
    clinical_justification: str = Field(max_length=5000)
    follow_up_context: str = Field(default="", max_length=5000)


class ReferralIn(BaseModel):
    destination: str = Field(max_length=200)
    case_summary: str = Field(max_length=10000)
    prm: str = Field(max_length=5000)
    suggested_conduct: str = Field(max_length=5000)
