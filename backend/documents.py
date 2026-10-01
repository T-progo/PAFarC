"""Document generation: Prescrição / Plano de Cuidado, Solicitação de Exames
Laboratoriais and Encaminhamento / Interconsulta.

PDFs are built in memory from the consultation context plus content entered by
the pharmacist, returned as the response body, and never written to disk.
Only a metadata record (type, consultation, pharmacist, time) is stored.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from backend.auth import get_current_pharmacist
from backend.consultations import _get_editable_consultation, _get_patient
from backend.database import get_db
from backend.models import GeneratedDocument, Pharmacist
from backend.pdf import DocumentContext, DocumentPDF, Establishment, clean_text
from backend.schemas import ExamRequestIn, PrescriptionIn, ReferralIn

PRESCRIPTION = "prescription"
EXAM_REQUEST = "exam_request"
REFERRAL = "referral"
DOCUMENT_TITLES = {
    PRESCRIPTION: "Prescrição / Plano de Cuidado",
    EXAM_REQUEST: "Solicitação de Exames Laboratoriais",
    REFERRAL: "Encaminhamento / Interconsulta",
}
FILE_PREFIXES = {
    PRESCRIPTION: "prescricao-plano-de-cuidado",
    EXAM_REQUEST: "solicitacao-de-exames",
    REFERRAL: "encaminhamento-interconsulta",
}

CurrentPharmacist = Annotated[Pharmacist, Depends(get_current_pharmacist)]
DB = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/patients/{patient_id}/consultations/{consultation_id}/documents",
    tags=["documents"],
)


def _required(value: str, message: str) -> str:
    value = clean_text(value)
    if not value:
        raise HTTPException(status_code=422, detail=message)
    return value


# --- builders (pure: context + content -> PDF bytes) -----------------------------

def build_prescription(ctx: DocumentContext, data: PrescriptionIn) -> bytes:
    pdf = DocumentPDF(DOCUMENT_TITLES[PRESCRIPTION], ctx)
    pdf.section("Medicamentos e cuidados")
    for number, item in enumerate(data.items, start=1):
        pdf.numbered_heading(number, item.medication)
        pdf.labeled_line("Dosagem", item.dosage, indent=5)
        pdf.labeled_line("Via", item.route, indent=5)
        pdf.labeled_line("Posologia", item.posology, indent=5)
        pdf.labeled_line("Tempo de tratamento", item.duration, indent=5)
        pdf.labeled_line("Orientações farmacêuticas / estilo de vida", item.guidance, indent=5)
        pdf.ln(3)
    pdf.closing()
    return pdf.to_bytes()


def build_exam_request(ctx: DocumentContext, data: ExamRequestIn) -> bytes:
    pdf = DocumentPDF(DOCUMENT_TITLES[EXAM_REQUEST], ctx)
    pdf.section("Exames solicitados")
    for number, exam in enumerate(data.exams, start=1):
        pdf.numbered_heading(number, exam, bold=False)
    pdf.ln(2)
    pdf.section("Justificativa clínica")
    pdf.paragraph(data.clinical_justification)
    if clean_text(data.follow_up_context):
        pdf.section("Contexto do acompanhamento farmacoterapêutico")
        pdf.paragraph(data.follow_up_context)
    pdf.closing()
    return pdf.to_bytes()


def build_referral(ctx: DocumentContext, data: ReferralIn) -> bytes:
    pdf = DocumentPDF(DOCUMENT_TITLES[REFERRAL], ctx)
    pdf.section("Destino")
    pdf.paragraph(data.destination)
    pdf.section("Resumo do caso")
    pdf.paragraph(data.case_summary)
    pdf.section("Problema relacionado a medicamento (PRM) identificado")
    pdf.paragraph(data.prm)
    pdf.section("Conduta sugerida")
    pdf.paragraph(data.suggested_conduct)
    pdf.closing()
    return pdf.to_bytes()


# --- routes ------------------------------------------------------------------------

def _issue(
    db: Session,
    patient_id: int,
    consultation_id: int,
    pharmacist: Pharmacist,
    document_type: str,
    build: Callable[[DocumentContext], bytes],
) -> Response:
    patient = _get_patient(db, patient_id)
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    context = DocumentContext(
        establishment=Establishment.from_settings(),
        patient_name=patient.full_name,
        patient_date_of_birth=patient.date_of_birth,
        patient_cpf=patient.cpf,
        pharmacist_name=pharmacist.full_name,
        pharmacist_crf=pharmacist.crf,
        consultation_date=consultation.consultation_date,
        issued_at=datetime.now().astimezone(),
    )
    pdf_bytes = build(context)
    db.add(GeneratedDocument(
        consultation_id=consultation.id, pharmacist_id=pharmacist.id, document_type=document_type
    ))
    db.commit()
    filename = f"{FILE_PREFIXES[document_type]}-atendimento-{consultation.id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/prescription", response_class=Response)
def generate_prescription(
    patient_id: int, consultation_id: int, data: PrescriptionIn, db: DB, pharmacist: CurrentPharmacist
) -> Response:
    for item in data.items:
        item.medication = _required(item.medication, "Informe o medicamento ou cuidado de cada item.")
    return _issue(db, patient_id, consultation_id, pharmacist, PRESCRIPTION,
                  lambda ctx: build_prescription(ctx, data))


@router.post("/exam-request", response_class=Response)
def generate_exam_request(
    patient_id: int, consultation_id: int, data: ExamRequestIn, db: DB, pharmacist: CurrentPharmacist
) -> Response:
    data.exams = [exam for exam in (clean_text(e) for e in data.exams) if exam]
    if not data.exams:
        raise HTTPException(status_code=422, detail="Informe ao menos um exame.")
    if any(len(exam) > 200 for exam in data.exams):
        raise HTTPException(status_code=422, detail="Nome de exame muito longo.")
    _required(data.clinical_justification, "Informe a justificativa clínica.")
    return _issue(db, patient_id, consultation_id, pharmacist, EXAM_REQUEST,
                  lambda ctx: build_exam_request(ctx, data))


@router.post("/referral", response_class=Response)
def generate_referral(
    patient_id: int, consultation_id: int, data: ReferralIn, db: DB, pharmacist: CurrentPharmacist
) -> Response:
    _required(data.destination, "Informe o profissional ou equipe de destino.")
    _required(data.case_summary, "Informe o resumo do caso.")
    _required(data.prm, "Informe o PRM identificado.")
    _required(data.suggested_conduct, "Informe a conduta sugerida.")
    return _issue(db, patient_id, consultation_id, pharmacist, REFERRAL,
                  lambda ctx: build_referral(ctx, data))
