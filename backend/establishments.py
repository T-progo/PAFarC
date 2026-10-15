"""Establishments: managed by administrators; each pharmacist is linked to exactly one.

Used only for user administration and the header of new documents. Patients,
consultations and documents are NOT separated by establishment.
"""

import re

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import ROLE_PHARMACIST, Establishment, EstablishmentSettings, Pharmacist
from backend.pdf import Establishment as DocumentEstablishment
from backend.pdf import clean_text

LEGACY_SETTINGS_ID = 1


class NoEstablishmentError(ValueError):
    """A pharmacist without an establishment cannot issue documents."""


class DuplicateCNPJError(ValueError):
    pass


def cnpj_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_cnpj(value: str | None) -> str:
    """Empty, or the 14 digits formatted as 00.000.000/0000-00."""
    value = clean_text(value or "")
    if not value:
        return ""
    if re.search(r"[^0-9./\-\s]", value):
        raise ValueError("CNPJ inválido.")
    digits = cnpj_digits(value)
    if len(digits) != 14:
        raise ValueError("O CNPJ deve ter 14 dígitos.")
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _check_unique_cnpj(db: Session, cnpj: str, exclude_id: int | None = None) -> None:
    digits = cnpj_digits(cnpj)
    if not digits:
        return  # several establishments may still lack a CNPJ
    for other in db.scalars(select(Establishment)).all():
        if other.id != exclude_id and cnpj_digits(other.cnpj) == digits:
            raise DuplicateCNPJError("Já existe um estabelecimento com este CNPJ.")


def _required_name(name: str | None) -> str:
    name = clean_text(name or "")
    if not name:
        raise ValueError("Informe o nome do estabelecimento.")
    return name


def create_establishment(db: Session, *, name: str, cnpj: str = "", address: str = "", phone: str = "") -> Establishment:
    cnpj = normalize_cnpj(cnpj)
    _check_unique_cnpj(db, cnpj)
    establishment = Establishment(name=_required_name(name), cnpj=cnpj, address=clean_text(address),
                                  phone=clean_text(phone))
    db.add(establishment)
    db.commit()
    db.refresh(establishment)
    return establishment


def update_establishment(db: Session, establishment: Establishment, changes: dict) -> Establishment:
    if "name" in changes:
        establishment.name = _required_name(changes["name"])
    if "cnpj" in changes:
        cnpj = normalize_cnpj(changes["cnpj"])
        _check_unique_cnpj(db, cnpj, exclude_id=establishment.id)
        establishment.cnpj = cnpj
    for key in ("address", "phone"):
        if key in changes:
            setattr(establishment, key, clean_text(changes[key] or ""))
    db.commit()
    db.refresh(establishment)
    return establishment


def migrate_legacy_establishment(db: Session) -> Establishment | None:
    """One-time move from the single-establishment model. Only when no establishment exists
    yet: create one from the establishment currently in effect (the saved record, else the
    server configuration, copied as-is) and link every pharmacist to it. Administrators stay
    unlinked. Safe to call on every startup (does nothing once establishments exist)."""
    if db.scalar(select(func.count(Establishment.id))):
        return None
    legacy = db.get(EstablishmentSettings, LEGACY_SETTINGS_ID)
    settings = get_settings()
    source = (
        (legacy.name, legacy.cnpj, legacy.address, legacy.phone) if legacy is not None
        else (settings.establishment_name, settings.establishment_cnpj,
              settings.establishment_address, settings.establishment_phone)
    )
    name, cnpj, address, phone = (value.strip() for value in source)
    establishment = Establishment(name=name or settings.establishment_name, cnpj=cnpj, address=address, phone=phone)
    db.add(establishment)
    db.flush()
    db.execute(
        update(Pharmacist)
        .where(Pharmacist.role == ROLE_PHARMACIST, Pharmacist.establishment_id.is_(None))
        .values(establishment_id=establishment.id)
    )
    db.commit()
    return establishment


def establishment_for_new_pharmacist(db: Session, establishment_id: int | None) -> int:
    """Validate the establishment of a new pharmacist. Without an explicit choice (CLI and
    internal use only; the API requires one), the single existing establishment is used."""
    if establishment_id is not None:
        if db.get(Establishment, establishment_id) is None:
            raise ValueError("Estabelecimento não encontrado.")
        return establishment_id
    ids = list(db.scalars(select(Establishment.id)).all())
    if not ids and migrate_legacy_establishment(db) is not None:
        ids = list(db.scalars(select(Establishment.id)).all())
    if len(ids) == 1:
        return ids[0]
    raise ValueError("Selecione o estabelecimento do farmacêutico.")


def document_header_for(pharmacist: Pharmacist) -> DocumentEstablishment:
    """Header of a new document: the establishment linked to the authenticated pharmacist.
    Never falls back to another establishment."""
    establishment = pharmacist.establishment
    if establishment is None:
        raise NoEstablishmentError(
            "Seu usuário não está vinculado a um estabelecimento. Procure o administrador."
        )
    return DocumentEstablishment(
        name=establishment.name, cnpj=establishment.cnpj, address=establishment.address,
        phone=establishment.phone,
    )
