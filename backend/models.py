import re
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String
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
    # Establishment printed on this pharmacist's new documents (administrators: none).
    establishment_id: Mapped[int | None] = mapped_column(ForeignKey("establishments.id"), nullable=True)

    establishment: Mapped["Establishment | None"] = relationship()

    @property
    def establishment_name(self) -> str | None:
        return self.establishment.name if self.establishment is not None else None

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
        if not text:  # administrators may have no CRF
            return ""
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
    # Set when the consultation is closed (a later consultation was started for the patient);
    # a closed consultation's SOAP and exam results can no longer be changed. Never reopened.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    # Identification of the test/kit used (optional; empty on results recorded before it existed).
    manufacturer: Mapped[str | None] = mapped_column("manufacturer_encrypted", EncryptedString, nullable=True)
    lot: Mapped[str | None] = mapped_column("lot_encrypted", EncryptedString, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column("expiry_date_encrypted", EncryptedDate, nullable=True)
    # Keyed digest of the normalized exam name, to find the same exam's last kit without
    # decrypting names (set whenever a result is saved).
    exam_name_index: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    consultation: Mapped[Consultation] = relationship(back_populates="exam_results")

    @property
    def has_kit(self) -> bool:
        return bool(self.manufacturer and self.lot and self.expiry_date)


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


class Establishment(Base):
    """An establishment managed by administrators. Each pharmacist is linked to one; it is
    used for the header of that pharmacist's new documents (clinical data is not split by
    establishment)."""

    __tablename__ = "establishments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str] = mapped_column(String(30), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class EstablishmentSettings(Base):
    """Legacy single establishment record (before multiple establishments). Kept unchanged
    as the migration source and for rollback; no longer used for new documents."""

    __tablename__ = "establishment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str] = mapped_column(String(30), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("pharmacists.id"), nullable=True)


class QualityControl(Base):
    """A CIQ/CEQ quality-control execution, owned by the establishment where it was created (no
    patient). Editable by its creator while a draft; once finalized it is read-only and its PDF is
    stored exactly as issued; a cancelled draft is kept read-only in the history. Technical values
    are kept as entered (free text) and encrypted."""

    __tablename__ = "quality_controls"
    __table_args__ = (Index("ix_quality_controls_establishment_execution", "establishment_id", "execution_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    establishment_id: Mapped[int] = mapped_column(ForeignKey("establishments.id"))
    pharmacist_id: Mapped[int] = mapped_column(ForeignKey("pharmacists.id"), index=True)
    # Responsible professional as at the creator's last change (frozen at finalization).
    responsible_name: Mapped[str] = mapped_column(String(200))
    responsible_crf: Mapped[str] = mapped_column(String(40))
    control_type: Mapped[str] = mapped_column(String(3))  # CIQ / CEQ
    execution_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    exam_analyte: Mapped[str | None] = mapped_column("exam_analyte_encrypted", EncryptedString, nullable=True)
    equipment_methodology: Mapped[str | None] = mapped_column("equipment_methodology_encrypted", EncryptedString,
                                                              nullable=True)
    provider_manufacturer: Mapped[str | None] = mapped_column("provider_manufacturer_encrypted", EncryptedString,
                                                              nullable=True)
    control_lot: Mapped[str | None] = mapped_column("control_lot_encrypted", EncryptedString, nullable=True)
    control_expiry: Mapped[str | None] = mapped_column("control_expiry_encrypted", EncryptedString,
                                                       nullable=True)  # "YYYY-MM"
    value_found: Mapped[str | None] = mapped_column("value_found_encrypted", EncryptedString, nullable=True)
    unit: Mapped[str | None] = mapped_column("unit_encrypted", EncryptedString, nullable=True)
    target_mean: Mapped[str | None] = mapped_column("target_mean_encrypted", EncryptedString, nullable=True)
    standard_deviation: Mapped[str | None] = mapped_column("standard_deviation_encrypted", EncryptedString,
                                                           nullable=True)
    acceptable_range_min: Mapped[str | None] = mapped_column("acceptable_range_min_encrypted", EncryptedString,
                                                             nullable=True)
    acceptable_range_max: Mapped[str | None] = mapped_column("acceptable_range_max_encrypted", EncryptedString,
                                                             nullable=True)
    evaluation_status: Mapped[str | None] = mapped_column(String(10), nullable=True)  # APPROVED / REJECTED
    # Non-conformity / corrective action (required to finalize a REJECTED record).
    nc_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nc_root_cause: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nc_root_cause_other: Mapped[str | None] = mapped_column("nc_root_cause_other_encrypted", EncryptedString,
                                                            nullable=True)
    nc_corrective_action: Mapped[str | None] = mapped_column("nc_corrective_action_encrypted", EncryptedString,
                                                             nullable=True)
    nc_repeat_value: Mapped[str | None] = mapped_column("nc_repeat_value_encrypted", EncryptedString,
                                                        nullable=True)
    nc_repeat_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nc_patient_decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "Cancelar lançamento": a draft given up on is kept (read-only, no PDF) as Cancelado. A record
    # is never both finalized and cancelled.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_pharmacist_id: Mapped[int | None] = mapped_column(ForeignKey("pharmacists.id"), nullable=True)
    pdf: Mapped[bytes | None] = mapped_column("pdf_encrypted", EncryptedBinary, nullable=True, deferred=True)
    pdf_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    establishment: Mapped["Establishment"] = relationship()

    @property
    def control_expiry_display(self) -> str:
        """Stored as YYYY-MM, shown as MM/YYYY."""
        if not self.control_expiry:
            return ""
        year, month = self.control_expiry.split("-")
        return f"{month}/{year}"
