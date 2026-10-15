import re
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.crypto import EncryptedBinary, EncryptedDate, EncryptedString
from backend.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


ROLE_ADMINISTRATOR = "administrator"
ROLE_PHARMACIST = "pharmacist"
ROLES = (ROLE_ADMINISTRATOR, ROLE_PHARMACIST)
# Roles allowed to use the clinical features (patients, SOAP, exams, documents).
# Strict two-role model agreed with the client: administrators manage users and the
# establishment only; pharmacists use the clinical features only.
CLINICAL_ROLES = frozenset({ROLE_PHARMACIST})

UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)
# Legacy single-field CRF values such as "CRF/RJ 16906", "CRF-SP 00001" or "CRF 16906/RJ".
_LEGACY_CRF = (
    re.compile(r"^\s*CRF\s*[-/ ]?\s*(?P<uf>[A-Za-z]{2})\s*[-/ ]?\s*(?P<number>\d[\d.\-]*)\s*$", re.I),
    re.compile(r"^\s*CRF\s*[-/ ]?\s*(?P<number>\d[\d.\-]*)\s*[-/ ]\s*(?P<uf>[A-Za-z]{2})\s*$", re.I),
)


def parse_legacy_crf(text: str) -> tuple[str, str] | None:
    """(number, UF) from a legacy combined CRF text, or None if it is not unambiguous."""
    for pattern in _LEGACY_CRF:
        match = pattern.match(text or "")
        if match and match["uf"].upper() in UFS:
            return match["number"], match["uf"].upper()
    return None


def format_crf(number: str, uf: str | None) -> str:
    """How a CRF is printed (pending the client's preferred format)."""
    return f"CRF/{uf} {number}" if uf else f"CRF {number}"


class Pharmacist(Base):
    __tablename__ = "pharmacists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    crf: Mapped[str] = mapped_column(String(30))
    login: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    role: Mapped[str] = mapped_column(String(20), default=ROLE_PHARMACIST, server_default=ROLE_PHARMACIST)
    # UF of the CRF. Older accounts keep their original combined text in `crf`
    # (e.g. "CRF/RJ 16906"); new/edited accounts store only the number in `crf`.
    crf_uf: Mapped[str | None] = mapped_column(String(2), nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMINISTRATOR

    @property
    def clinical_access(self) -> bool:
        return self.role in CLINICAL_ROLES

    @property
    def crf_number(self) -> str:
        legacy = parse_legacy_crf(self.crf)
        return legacy[0] if legacy else self.crf

    @property
    def crf_display(self) -> str:
        text = (self.crf or "").strip()
        if text.upper().startswith("CRF"):  # legacy combined value: shown exactly as stored
            return text
        return format_crf(text, self.crf_uf)


class Patient(Base):
    """Personal data is encrypted at rest; *_index columns hold keyed HMAC
    digests of the normalized values for exact-match search."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column("full_name_encrypted", EncryptedString)
    full_name_index: Mapped[str] = mapped_column(String(64), index=True)
    date_of_birth: Mapped[date] = mapped_column("date_of_birth_encrypted", EncryptedDate)
    cpf: Mapped[str] = mapped_column("cpf_encrypted", EncryptedString)
    cpf_index: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    name_tokens: Mapped[list["PatientNameToken"]] = relationship(cascade="all, delete-orphan")


class PatientNameToken(Base):
    """Keyed HMAC digest of one name-word prefix (3+ letters), for partial name search
    without storing the name in plaintext."""

    __tablename__ = "patient_name_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    token_index: Mapped[str] = mapped_column(String(64), index=True)


class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    pharmacist_id: Mapped[int] = mapped_column(ForeignKey("pharmacists.id"), index=True)
    consultation_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    pharmacist: Mapped[Pharmacist] = relationship()
    soap: Mapped["SoapRecord | None"] = relationship(
        back_populates="consultation", cascade="all, delete-orphan"
    )
    exam_results: Mapped[list["ExamResult"]] = relationship(
        back_populates="consultation", cascade="all, delete-orphan", order_by="ExamResult.id"
    )
    documents: Mapped[list["GeneratedDocument"]] = relationship(order_by="GeneratedDocument.id")


class SoapRecord(Base):
    """SOAP narrative of one consultation; every section is encrypted at rest."""

    __tablename__ = "soap_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consultation_id: Mapped[int] = mapped_column(ForeignKey("consultations.id"), unique=True)
    subjective: Mapped[str] = mapped_column("subjective_encrypted", EncryptedString)
    objective: Mapped[str] = mapped_column("objective_encrypted", EncryptedString)
    assessment: Mapped[str] = mapped_column("assessment_encrypted", EncryptedString)
    plan: Mapped[str] = mapped_column("plan_encrypted", EncryptedString)

    consultation: Mapped[Consultation] = relationship(back_populates="soap")


class ExamResult(Base):
    """Manually entered laboratory/exam result; all clinical fields encrypted at rest."""

    __tablename__ = "exam_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consultation_id: Mapped[int] = mapped_column(ForeignKey("consultations.id"), index=True)
    exam_name: Mapped[str] = mapped_column("exam_name_encrypted", EncryptedString)
    result: Mapped[str] = mapped_column("result_encrypted", EncryptedString)
    unit: Mapped[str] = mapped_column("unit_encrypted", EncryptedString)
    reference_range: Mapped[str] = mapped_column("reference_range_encrypted", EncryptedString)
    notes: Mapped[str] = mapped_column("notes_encrypted", EncryptedString)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    consultation: Mapped[Consultation] = relationship(back_populates="exam_results")


class GeneratedDocument(Base):
    """An issued document. The PDF exactly as issued is stored encrypted, so it can be
    reprinted unchanged later; it is only loaded when a reprint is requested."""

    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consultation_id: Mapped[int] = mapped_column(ForeignKey("consultations.id"), index=True)
    pharmacist_id: Mapped[int] = mapped_column(ForeignKey("pharmacists.id"))
    document_type: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Null for documents issued before PDFs were stored (not reprintable).
    pdf: Mapped[bytes | None] = mapped_column("pdf_encrypted", EncryptedBinary, nullable=True, deferred=True)
    pdf_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    pharmacist: Mapped[Pharmacist] = relationship()

    @property
    def reprintable(self) -> bool:
        return self.pdf_size is not None


class EstablishmentSettings(Base):
    """The single establishment record (id=1) printed on new documents. Until an
    administrator saves it, the values from the server configuration (.env) are used."""

    __tablename__ = "establishment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str] = mapped_column(String(30), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("pharmacists.id"), nullable=True)
