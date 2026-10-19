from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, update
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


def _begin_write(db: Session) -> None:
    """Start this request's write transaction now, taking SQLite's write lock (BEGIN IMMEDIATE)
    before any state is checked. Every clinical change (SOAP, exam results) and every closing
    (finalize, new consultation) goes through here, so they run one at a time: each sees what
    the previous one committed, and the lock is held until this request commits or rolls back.
    A waiting request blocks for up to the driver's busy timeout (pysqlite: 5 s)."""
    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _get_mutable_consultation(
    db: Session, patient_id: int, consultation_id: int, pharmacist: Pharmacist
) -> Consultation:
    """The single check for changing a consultation's clinical content (SOAP, exam results) or
    closing it: the responsible pharmacist, and only while the consultation is open. Access
    (404/403) is checked first; the open/closed state is then re-checked under the write lock,
    in the same transaction as the change, so a closing committed by another request is seen."""
    _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    _begin_write(db)
    db.expire_all()  # re-read everything under the lock
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    if consultation.closed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Atendimento encerrado. Os dados clínicos deste atendimento são somente para consulta.",
        )
    return consultation


def _close_other_consultations(db: Session, patient_id: int, keep_id: int, when: datetime) -> None:
    """Close the patient's other open consultations (status only; updated_at is left as is)."""
    db.execute(
        update(Consultation)
        .where(Consultation.patient_id == patient_id, Consultation.id != keep_id, Consultation.closed_at.is_(None))
        .values(closed_at=when, updated_at=Consultation.updated_at)
        .execution_options(synchronize_session=False)
    )


def close_superseded_consultations(db: Session) -> int:
    """Startup, idempotent: close every open consultation that is not its patient's most
    recently started one (the state the rule above produces), dated when the next one was
    started. Only the status is written; clinical content is untouched."""
    latest = dict(db.execute(
        select(Consultation.patient_id, func.max(Consultation.id)).group_by(Consultation.patient_id)
    ).all())
    open_ones = db.execute(
        select(Consultation.id, Consultation.patient_id).where(Consultation.closed_at.is_(None))
    ).all()
    closed = 0
    for consultation_id, patient_id in open_ones:
        if consultation_id == latest[patient_id]:
            continue
        next_started = db.scalar(select(func.min(Consultation.created_at)).where(
            Consultation.patient_id == patient_id, Consultation.id > consultation_id))
        db.execute(
            update(Consultation).where(Consultation.id == consultation_id)
            .values(closed_at=next_started, updated_at=Consultation.updated_at)
            .execution_options(synchronize_session=False)
        )
        closed += 1
    db.commit()
    return closed


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
    _begin_write(db)  # closing the previous one and creating this one commit together, serialized
    db.add(consultation)
    db.flush()
    # Starting a new consultation ends the patient's previous one(s): they become read-only.
    _close_other_consultations(db, patient.id, consultation.id, datetime.now(timezone.utc))
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


@router.post("/{consultation_id}/finalize", response_model=ConsultationOut)
def finalize_consultation(
    patient_id: int, consultation_id: int, db: DB, pharmacist: CurrentPharmacist
) -> Consultation:
    """“Finalizar atendimento”: the responsible pharmacist closes the open consultation. Only
    closed_at is set (same state as the automatic closing); an already closed consultation
    gets 409 and keeps its original closed_at. There is no way back."""
    consultation = _get_mutable_consultation(db, patient_id, consultation_id, pharmacist)
    closed = db.execute(
        update(Consultation).where(Consultation.id == consultation.id, Consultation.closed_at.is_(None))
        .values(closed_at=datetime.now(timezone.utc), updated_at=Consultation.updated_at)
        .execution_options(synchronize_session=False)
    ).rowcount
    if closed != 1:  # only the request that performs the open -> closed transition succeeds
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Atendimento encerrado. Os dados clínicos deste atendimento são somente para consulta.",
        )
    db.commit()
    db.refresh(consultation)
    return consultation


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
    consultation = _get_mutable_consultation(db, patient_id, consultation_id, pharmacist)
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
    consultation = _get_mutable_consultation(db, patient_id, consultation_id, pharmacist)
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
    consultation = _get_mutable_consultation(db, patient_id, consultation_id, pharmacist)
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
    consultation = _get_mutable_consultation(db, patient_id, consultation_id, pharmacist)
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

