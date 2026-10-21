import re
import unicodedata
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import get_current_pharmacist, require_clinical
from backend.crypto import DecryptionError, get_crypto
from backend.database import get_db
from backend.establishments import clinical_establishment_id
from backend.models import Patient, PatientNameToken, Pharmacist
from backend.schemas import PatientCreate, PatientOut, PatientSearch

CPF_INDEX_PURPOSE = "patient.cpf"
NAME_INDEX_PURPOSE = "patient.full_name"
NAME_PREFIX_PURPOSE = "patient.name_prefix"
MIN_PREFIX = 3   # shortest searchable word fragment
MAX_PREFIX = 20  # longer words/queries are matched on their first 20 letters
MIN_DATE_OF_BIRTH = date(1900, 1, 1)
SEARCH_LIMIT = 50

# Digits plus the usual CPF punctuation; anything else is malformed.
_CPF_ALLOWED = re.compile(r"^[0-9.\-\s]+$")


class DuplicateCPFError(ValueError):
    pass


def normalize_cpf(value: str) -> str:
    """Return the 11 CPF digits, or raise ValueError if the CPF is malformed."""
    if not _CPF_ALLOWED.fullmatch(value.strip() or "x"):
        raise ValueError("CPF inválido.")
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        raise ValueError("CPF inválido.")
    for length in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits[:length], range(length + 1, 1, -1)))
        if (total * 10) % 11 % 10 != int(digits[length]):
            raise ValueError("CPF inválido.")
    return digits


def clean_full_name(value: str) -> str:
    """Display form: Unicode NFC, trimmed, single spaces. Case and accents kept."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def normalize_name(value: str) -> str:
    """Search form: trimmed, single spaces, case-folded and without accents."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_marks.casefold().split())


def name_words(value: str) -> list[str]:
    """Searchable words: normalized (no accents, case-folded), letters/digits only."""
    return re.findall(r"\w+", normalize_name(value))


def name_prefix_indexes(full_name: str) -> set[str]:
    """Keyed digests of every 3+ letter prefix of every word of the name."""
    crypto = get_crypto()
    return {
        crypto.blind_index(NAME_PREFIX_PURPOSE, word[:length])
        for word in name_words(full_name)
        for length in range(MIN_PREFIX, min(len(word), MAX_PREFIX) + 1)
    }


def create_patient(db: Session, *, establishment_id: int, full_name: str, date_of_birth: date, cpf: str) -> Patient:
    """A new patient record of the given establishment (the caller's own, never from the request).
    A CPF is unique within the establishment only; other establishments are never consulted."""
    full_name = clean_full_name(full_name)
    if not full_name:
        raise ValueError("Nome completo é obrigatório.")
    if not MIN_DATE_OF_BIRTH <= date_of_birth <= date.today():
        raise ValueError("Data de nascimento inválida.")
    cpf_digits = normalize_cpf(cpf)

    crypto = get_crypto()
    cpf_index = crypto.blind_index(CPF_INDEX_PURPOSE, cpf_digits)
    if db.scalar(select(Patient.id).where(Patient.establishment_id == establishment_id,
                                          Patient.cpf_index == cpf_index)) is not None:
        raise DuplicateCPFError("Já existe um paciente cadastrado com este CPF.")

    patient = Patient(
        establishment_id=establishment_id,
        full_name=full_name,
        full_name_index=crypto.blind_index(NAME_INDEX_PURPOSE, normalize_name(full_name)),
        date_of_birth=date_of_birth,
        cpf=cpf_digits,
        cpf_index=cpf_index,
        name_tokens=[PatientNameToken(token_index=t) for t in name_prefix_indexes(full_name)],
    )
    db.add(patient)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateCPFError("Já existe um paciente cadastrado com este CPF.") from None
    db.refresh(patient)
    return patient


def search_patients(db: Session, query: str, establishment_id: int) -> list[Patient]:
    """Patients of the given establishment only. CPF (if the query has no letters): exact match.
    Name: every typed word (3+ letters) must be the start of a word of the patient's name, case-
    and accent-insensitive. Other establishments' records never enter the query or the limit."""
    crypto = get_crypto()
    if any(c.isalpha() for c in query):
        words = [w[:MAX_PREFIX] for w in name_words(query) if len(w) >= MIN_PREFIX]
        if not words:
            raise ValueError("Informe ao menos 3 letras do nome.")
        digests = {crypto.blind_index(NAME_PREFIX_PURPOSE, w) for w in words}
        matching_ids = (
            select(PatientNameToken.patient_id)
            .join(Patient, Patient.id == PatientNameToken.patient_id)
            .where(Patient.establishment_id == establishment_id, PatientNameToken.token_index.in_(digests))
            .group_by(PatientNameToken.patient_id)
            .having(func.count(func.distinct(PatientNameToken.token_index)) == len(digests))
        )
        condition = Patient.id.in_(matching_ids)
    else:
        cpf_digits = normalize_cpf(query)
        condition = Patient.cpf_index == crypto.blind_index(CPF_INDEX_PURPOSE, cpf_digits)
    patients = db.scalars(
        select(Patient).where(Patient.establishment_id == establishment_id, condition).limit(SEARCH_LIMIT)
    ).all()
    return sorted(patients, key=lambda p: normalize_name(p.full_name))


def get_patient(db: Session, patient_id: int, pharmacist: Pharmacist) -> Patient:
    """A patient of the pharmacist's current establishment; any other answers 404 (no leak)."""
    patient = db.scalar(select(Patient).where(
        Patient.id == patient_id, Patient.establishment_id == clinical_establishment_id(pharmacist)))
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado.")
    return patient


def backfill_name_tokens(db: Session) -> int:
    """Create partial-search tokens for patients registered before they existed."""
    missing = db.scalars(
        select(Patient).where(~Patient.id.in_(select(PatientNameToken.patient_id)))
    ).all()
    for patient in missing:
        patient.name_tokens = [PatientNameToken(token_index=t) for t in name_prefix_indexes(patient.full_name)]
    db.commit()
    return len(missing)


def verify_keys_match_existing_data(db: Session) -> None:
    """Refuse to run with keys that do not belong to the existing database.

    A different encryption key would make stored data unreadable (and new rows
    would be encrypted with the new key, mixing keys in one database); a
    different blind-index key would silently break search and duplicate-CPF
    detection. Checked once at startup against the first stored patient.
    """
    try:
        patient = db.scalar(select(Patient).order_by(Patient.id).limit(1))
    except DecryptionError:
        raise RuntimeError(
            "PHARMATECH_DATA_ENCRYPTION_KEY does not match the existing database. "
            "Restore the original key; do not replace keys on a database with data."
        ) from None
    if patient is None:
        return
    if get_crypto().blind_index(CPF_INDEX_PURPOSE, patient.cpf) != patient.cpf_index:
        raise RuntimeError(
            "PHARMATECH_BLIND_INDEX_KEY does not match the existing database. "
            "Restore the original key; do not replace keys on a database with data."
        )


router = APIRouter(
    prefix="/patients",
    tags=["patients"],
    dependencies=[Depends(require_clinical)],
)


CurrentPharmacist = Annotated[Pharmacist, Depends(get_current_pharmacist)]


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
def create_patient_endpoint(
    data: PatientCreate, db: Annotated[Session, Depends(get_db)], pharmacist: CurrentPharmacist
) -> Patient:
    establishment_id = clinical_establishment_id(pharmacist)
    try:
        return create_patient(
            db, establishment_id=establishment_id, full_name=data.full_name,
            date_of_birth=data.date_of_birth, cpf=data.cpf,
        )
    except DuplicateCPFError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# POST rather than GET so CPFs and names never appear in URLs or access logs.
@router.post("/search", response_model=list[PatientOut])
def search_patients_endpoint(
    data: PatientSearch, db: Annotated[Session, Depends(get_db)], pharmacist: CurrentPharmacist
) -> list[Patient]:
    establishment_id = clinical_establishment_id(pharmacist)
    try:
        return search_patients(db, data.query, establishment_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient_endpoint(
    patient_id: int, db: Annotated[Session, Depends(get_db)], pharmacist: CurrentPharmacist
) -> Patient:
    return get_patient(db, patient_id, pharmacist)
