from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import get_current_pharmacist
from backend.database import get_db
from backend.models import Consultation, ExamResult, Patient, Pharmacist, SoapRecord
from backend.schemas import (
    ConsultationCreate,
    ConsultationOut,
    ConsultationSummary,
    ExamResultIn,
    ExamResultOut,
    PreviousExamGroup,
    SoapIn,
    SoapOut,
)

CurrentPharmacist = Annotated[Pharmacist, Depends(get_current_pharmacist)]
DB = Annotated[Session, Depends(get_db)]

# Every route is nested under the patient, and every lookup checks that the
# consultation (and exam) belongs to that patient, so records can never be
# read or changed through another patient's context.
router = APIRouter(prefix="/patients/{patient_id}/consultations", tags=["consultations"])


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


def _clean_exam(data: ExamResultIn) -> dict[str, str]:
    values = {k: v.strip() for k, v in data.model_dump().items()}
    if not values["exam_name"]:
        raise HTTPException(status_code=422, detail="Informe o nome do exame.")
    if not values["result"]:
        raise HTTPException(status_code=422, detail="Informe o resultado do exame.")
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
