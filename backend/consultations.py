from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import get_current_pharmacist, require_clinical
from backend.crypto import get_crypto
from backend.database import get_db
from backend.models import Consultation, ExamResult, Patient, Pharmacist, SoapRecord
from backend.patients import normalize_name
from backend.schemas import (
    ConsultationCreate,
    ConsultationOut,
    ConsultationSummary,
    ExamKit,
    ExamKitQuery,
    ExamResultIn,
    ExamResultOut,
    PreviousExamGroup,
    SoapIn,
    SoapOut,
)

EXAM_NAME_PURPOSE = "exam.name"

CurrentPharmacist = Annotated[Pharmacist, Depends(get_current_pharmacist)]
DB = Annotated[Session, Depends(get_db)]

# Every route is nested under the patient, and every lookup checks that the
# consultation (and exam) belongs to that patient, so records can never be
# read or changed through another patient's context.
router = APIRouter(
    prefix="/patients/{patient_id}/consultations",
    tags=["consultations"],
    dependencies=[Depends(require_clinical)],
)


def _get_patient(db: Session, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paciente não encontrado.")
    return patient


def _get_consultation(db: Session, patient_id: int, consultation_id: int) -> Consultation:
    consultation = db.get(Consultation, consultation_id)
    if consultation is None or consultation.patient_id != patient_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Atendimento não encontrado.")
    return consultation


def _get_editable_consultation(
    db: Session, patient_id: int, consultation_id: int, pharmacist: Pharmacist
) -> Consultation:
    consultation = _get_consultation(db, patient_id, consultation_id)
    if consultation.pharmacist_id != pharmacist.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Somente o farmacêutico responsável pode alterar este atendimento.",
        )
    return consultation


def _get_exam(consultation: Consultation, exam_id: int) -> ExamResult:
    for exam in consultation.exam_results:
        if exam.id == exam_id:
            return exam
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resultado de exame não encontrado.")


def exam_name_index(exam_name: str) -> str:
    """Same exam = same name after trimming, collapsing spaces, removing accents and case
    (the normalization already used for patient names); no approximate matching."""
    return get_crypto().blind_index(EXAM_NAME_PURPOSE, normalize_name(exam_name))


def _clean_exam(data: ExamResultIn) -> dict:
    values = {k: v.strip() if isinstance(v, str) else v for k, v in data.model_dump().items()}
    if not values["exam_name"]:
        raise HTTPException(status_code=422, detail="Informe o nome do exame.")
    if not values["result"]:
        raise HTTPException(status_code=422, detail="Informe o resultado do exame.")
    for key in ("manufacturer", "lot"):  # optional kit fields: blank means not informed
        values[key] = values[key] or None
    values["exam_name_index"] = exam_name_index(values["exam_name"])
    return values


def _touch(consultation: Consultation) -> None:
    consultation.updated_at = datetime.now(timezone.utc)


@router.post("", response_model=ConsultationOut, status_code=status.HTTP_201_CREATED)
def create_consultation(
    patient_id: int, data: ConsultationCreate, db: DB, pharmacist: CurrentPharmacist
) -> Consultation:
    patient = _get_patient(db, patient_id)
    consultation_date = data.consultation_date or date.today()
    if not patient.date_of_birth <= consultation_date <= date.today():
        raise HTTPException(status_code=422, detail="Data do atendimento inválida.")
    consultation = Consultation(
        patient_id=patient.id, pharmacist_id=pharmacist.id, consultation_date=consultation_date
    )
    db.add(consultation)
    db.commit()
    db.refresh(consultation)
    return consultation


@router.get("", response_model=list[ConsultationSummary])
def list_consultations(patient_id: int, db: DB, _pharmacist: CurrentPharmacist) -> list[Consultation]:
    _get_patient(db, patient_id)
    query = (
        select(Consultation)
        .where(Consultation.patient_id == patient_id)
        .order_by(
            Consultation.consultation_date.desc(),
            Consultation.created_at.desc(),
            Consultation.id.desc(),
        )
    )
    return list(db.scalars(query).all())


@router.get("/{consultation_id}", response_model=ConsultationOut)
def get_consultation(
    patient_id: int, consultation_id: int, db: DB, _pharmacist: CurrentPharmacist
) -> Consultation:
    return _get_consultation(db, patient_id, consultation_id)


@router.get("/{consultation_id}/previous-exams", response_model=list[PreviousExamGroup])
def previous_exams(
    patient_id: int, consultation_id: int, db: DB, _pharmacist: CurrentPharmacist
) -> list[dict]:
    """Exam results of this patient's earlier consultations, newest first (read-only)."""
    current = _get_consultation(db, patient_id, consultation_id)
    current_key = (current.consultation_date, current.created_at, current.id)
    earlier = db.scalars(
        select(Consultation)
        .where(Consultation.patient_id == patient_id, Consultation.id != current.id)
        .order_by(
            Consultation.consultation_date.desc(),
            Consultation.created_at.desc(),
            Consultation.id.desc(),
        )
    ).all()
    return [
        {
            "consultation_id": c.id,
            "consultation_date": c.consultation_date,
            "pharmacist": c.pharmacist,
            "exam_results": c.exam_results,
        }
        for c in earlier
        if (c.consultation_date, c.created_at, c.id) < current_key and c.exam_results
    ]


@router.put("/{consultation_id}/soap", response_model=SoapOut)
def save_soap(
    patient_id: int, consultation_id: int, data: SoapIn, db: DB, pharmacist: CurrentPharmacist
) -> SoapRecord:
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    values = {k: v.strip() for k, v in data.model_dump().items()}
    if consultation.soap is None:
        consultation.soap = SoapRecord(**values)
    else:
        for key, value in values.items():
            setattr(consultation.soap, key, value)
    _touch(consultation)
    db.commit()
    return consultation.soap


@router.post(
    "/{consultation_id}/exams", response_model=ExamResultOut, status_code=status.HTTP_201_CREATED
)
def add_exam_result(
    patient_id: int, consultation_id: int, data: ExamResultIn, db: DB, pharmacist: CurrentPharmacist
) -> ExamResult:
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    exam = ExamResult(**_clean_exam(data))
    consultation.exam_results.append(exam)
    _touch(consultation)
    db.commit()
    db.refresh(exam)
    return exam


@router.put("/{consultation_id}/exams/{exam_id}", response_model=ExamResultOut)
def update_exam_result(
    patient_id: int,
    consultation_id: int,
    exam_id: int,
    data: ExamResultIn,
    db: DB,
    pharmacist: CurrentPharmacist,
) -> ExamResult:
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    exam = _get_exam(consultation, exam_id)
    for key, value in _clean_exam(data).items():
        setattr(exam, key, value)
    _touch(consultation)
    db.commit()
    return exam


@router.delete("/{consultation_id}/exams/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exam_result(
    patient_id: int, consultation_id: int, exam_id: int, db: DB, pharmacist: CurrentPharmacist
) -> Response:
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    consultation.exam_results.remove(_get_exam(consultation, exam_id))
    _touch(consultation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Kit suggestion: last test/kit identification the pharmacist used for the same exam.
kit_router = APIRouter(prefix="/exam-kit-suggestion", tags=["consultations"],
                       dependencies=[Depends(require_clinical)])


@kit_router.post("", response_model=ExamKit)
def exam_kit_suggestion(data: ExamKitQuery, db: DB, pharmacist: CurrentPharmacist) -> ExamKit:
    """Fabricante/Lote/Validade of the most recently recorded result of the same exam, among
    this pharmacist's own consultations, that has all three filled in. Only those three
    values are returned (empty when there is none). POST so exam names stay out of URLs/logs."""
    latest = db.scalars(
        select(ExamResult)
        .join(Consultation, ExamResult.consultation_id == Consultation.id)
        .where(
            Consultation.pharmacist_id == pharmacist.id,
            ExamResult.exam_name_index == exam_name_index(data.exam_name),
            ExamResult.manufacturer.is_not(None),
            ExamResult.lot.is_not(None),
            ExamResult.expiry_date.is_not(None),
        )
        .order_by(ExamResult.created_at.desc(), ExamResult.id.desc())
        .limit(1)
    ).first()
    if latest is None:
        return ExamKit()
    return ExamKit(manufacturer=latest.manufacturer, lot=latest.lot, expiry_date=latest.expiry_date)

