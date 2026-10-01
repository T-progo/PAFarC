from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.crypto import EncryptedDate, EncryptedString
from backend.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Pharmacist(Base):
    __tablename__ = "pharmacists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    crf: Mapped[str] = mapped_column(String(30))
    login: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
    """Record that a document was issued. The PDF itself is never stored."""

    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consultation_id: Mapped[int] = mapped_column(ForeignKey("consultations.id"), index=True)
    pharmacist_id: Mapped[int] = mapped_column(ForeignKey("pharmacists.id"))
    document_type: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    pharmacist: Mapped[Pharmacist] = relationship()
