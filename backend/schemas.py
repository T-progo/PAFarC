from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PharmacistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    crf: str = Field(validation_alias="crf_display")  # printable "CRF/UF number"
    crf_number: str
    crf_uf: str | None
    login: str
    role: str
    is_admin: bool
    clinical_access: bool
    establishment_id: int | None
    establishment_name: str | None
    active: bool
    created_at: datetime


class UserCreateIn(BaseModel):
    full_name: str = Field(max_length=200)
    crf: str = Field(default="", max_length=30)  # required for pharmacists only
    crf_uf: str | None = Field(default=None, max_length=2)
    login: str = Field(max_length=50)
    password: str = Field(max_length=200)
    role: str = "pharmacist"
    establishment_id: int | None = None  # required for pharmacists


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    crf: str | None = Field(default=None, max_length=30)
    crf_uf: str | None = Field(default=None, max_length=2)
    login: str | None = Field(default=None, max_length=50)
    role: str | None = None
    establishment_id: int | None = None


class PasswordResetIn(BaseModel):
    password: str = Field(max_length=200)


class EstablishmentIn(BaseModel):
    name: str = Field(max_length=200)
    cnpj: str = Field(default="", max_length=30)
    address: str = Field(default="", max_length=300)
    phone: str = Field(default="", max_length=40)


class EstablishmentUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    cnpj: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)


class EstablishmentOut(EstablishmentIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


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
    crf: str = Field(validation_alias="crf_display")


class ConsultationCreate(BaseModel):
    consultation_date: date | None = None  # defaults to today


class SoapIn(BaseModel):
    subjective: str = Field(default="", max_length=SOAP_SECTION_MAX)
    objective: str = Field(default="", max_length=SOAP_SECTION_MAX)
    assessment: str = Field(default="", max_length=SOAP_SECTION_MAX)
    plan: str = Field(default="", max_length=SOAP_SECTION_MAX)


class SoapOut(SoapIn):
    model_config = ConfigDict(from_attributes=True)


class ExamKit(BaseModel):
    """Identification of the test/kit used for an exam result (all optional)."""

    manufacturer: str | None = Field(default=None, max_length=200)
    lot: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None


class ExamResultIn(ExamKit):
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


class ExamKitQuery(BaseModel):
    exam_name: str = Field(min_length=1, max_length=200)


class ConsultationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    consultation_date: date
    pharmacist: PharmacistSummary
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class GeneratedDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_type: str
    created_at: datetime
    pharmacist: PharmacistSummary
    reprintable: bool


class PreviousExamGroup(BaseModel):
    """Exam results of one earlier consultation of the same patient (read-only)."""

    model_config = ConfigDict(from_attributes=True)

    consultation_id: int
    consultation_date: date
    pharmacist: PharmacistSummary
    exam_results: list["ExamResultOut"]


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


# --- quality control (CIQ / CEQ) ---------------------------------------------------

QCStatus = Literal["APPROVED", "REJECTED"]
QCRootCause = Literal["EXPIRED", "STORAGE", "CALIBRATION", "DEFECTIVE_LOT", "OTHER"]
QCPatientDecision = Literal["APPROVED_FOR_PATIENT_TESTING", "BLOCKED_UNTIL_TECHNICAL_MAINTENANCE"]


class QualityControlIn(BaseModel):
    """Content entered by the pharmacist (a draft may be incomplete). Execution time, responsible
    professional and establishment are always set by the server, never taken from the request."""

    control_type: Literal["CIQ", "CEQ"]
    exam_analyte: str = Field(default="", max_length=200)
    equipment_methodology: str = Field(default="", max_length=200)
    provider_manufacturer: str = Field(default="", max_length=200)
    control_lot: str = Field(default="", max_length=100)
    control_expiry: str = Field(default="", max_length=10)  # MM/YYYY (surrounding spaces ignored)
    value_found: str = Field(default="", max_length=50)
    unit: str = Field(default="", max_length=50)
    target_mean: str = Field(default="", max_length=50)
    standard_deviation: str = Field(default="", max_length=50)
    acceptable_range_min: str = Field(default="", max_length=50)
    acceptable_range_max: str = Field(default="", max_length=50)
    evaluation_status: QCStatus | None = None
    nc_root_cause: QCRootCause | None = None
    nc_root_cause_other: str = Field(default="", max_length=1000)
    nc_corrective_action: str = Field(default="", max_length=4000)
    nc_repeat_value: str = Field(default="", max_length=50)
    nc_repeat_status: QCStatus | None = None
    nc_patient_decision: QCPatientDecision | None = None


class QualityControlSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    control_type: str
    execution_at: datetime
    exam_analyte: str | None
    evaluation_status: str | None
    pharmacist_id: int
    responsible_name: str
    responsible_crf: str
    finalized_at: datetime | None


class QualityControlOut(QualityControlSummary):
    establishment_id: int
    equipment_methodology: str | None
    provider_manufacturer: str | None
    control_lot: str | None
    control_expiry: str = Field(validation_alias="control_expiry_display")
    value_found: str | None
    unit: str | None
    target_mean: str | None
    standard_deviation: str | None
    acceptable_range_min: str | None
    acceptable_range_max: str | None
    nc_event_at: datetime | None
    nc_root_cause: str | None
    nc_root_cause_other: str | None
    nc_corrective_action: str | None
    nc_repeat_value: str | None
    nc_repeat_status: str | None
    nc_patient_decision: str | None
    created_at: datetime
    updated_at: datetime
