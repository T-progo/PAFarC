from datetime import date, datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

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
