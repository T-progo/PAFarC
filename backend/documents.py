"""Document generation: Prescrição / Plano de Cuidado, Solicitação de Exames
Laboratoriais, Encaminhamento / Interconsulta and Declaração de Serviço Farmacêutico (DSF).

PDFs are built in memory from the consultation context plus content entered by
the pharmacist and returned as the response body; they are never written to disk.
The issued PDF is stored encrypted in the database with its metadata, so a reprint
returns exactly the original document, whatever changes later.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from backend.auth import get_current_pharmacist, require_clinical
from backend.consultations import _get_consultation, _get_editable_consultation, _get_patient
from backend.database import get_db
from backend.models import GeneratedDocument, Pharmacist
from backend.establishments import NoEstablishmentError, document_header_for
from backend.pdf import DocumentContext, DocumentPDF, ResultRow, clean_text, format_date
from backend.schemas import DsfIn, ExamRequestIn, PrescriptionIn, ReferralIn

PRESCRIPTION = "prescription"
EXAM_REQUEST = "exam_request"
REFERRAL = "referral"
DSF = "dsf"
DOCUMENT_TITLES = {
    PRESCRIPTION: "Prescrição / Plano de Cuidado",
    EXAM_REQUEST: "Solicitação de Exames Laboratoriais",
    REFERRAL: "Encaminhamento / Interconsulta",
    DSF: "Declaração de Serviço Farmacêutico",
}
FILE_PREFIXES = {
    PRESCRIPTION: "prescricao-plano-de-cuidado",
    EXAM_REQUEST: "solicitacao-de-exames",
    REFERRAL: "encaminhamento-interconsulta",
    DSF: "declaracao-servico-farmaceutico",
}
# Client-provided wording, printed exactly as given.
DSF_NOTICE = (
    "Este exame possui finalidade de triagem e rastreamento, não sendo destinado à confirmação "
    "diagnóstica. Os resultados devem ser avaliados por profissional legalmente habilitado e, quando "
    "necessário, complementados por exames laboratoriais confirmatórios."
)

CurrentPharmacist = Annotated[Pharmacist, Depends(get_current_pharmacist)]
DB = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/patients/{patient_id}/consultations/{consultation_id}/documents",
    tags=["documents"],
    dependencies=[Depends(require_clinical)],
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


def build_dsf(ctx: DocumentContext, results: list[ResultRow], data: DsfIn) -> bytes:
    pdf = DocumentPDF(DOCUMENT_TITLES[DSF], ctx)
    pdf.section("Resultados registrados no atendimento")
    pdf.results_table(results)
    pdf.section("Dados do teste")
    pdf.labeled_line("Fabricante do teste", data.manufacturer)
    pdf.labeled_line("Número do lote", data.lot)
    pdf.labeled_line("Data de validade", format_date(data.expiry_date))
    # The results belong to the consultation, so they were obtained on its date.
    pdf.labeled_line("Data da realização", format_date(ctx.consultation_date))
    pdf.ln(4)
    pdf.notice("Aviso", DSF_NOTICE)
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
    try:
        # Header = the authenticated pharmacist's own establishment (never from the request).
        header = document_header_for(pharmacist)
    except NoEstablishmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    context = DocumentContext(
        establishment=header,
        patient_name=patient.full_name,
        patient_date_of_birth=patient.date_of_birth,
        patient_cpf=patient.cpf,
        pharmacist_name=pharmacist.full_name,
        pharmacist_crf=pharmacist.crf_display,
        consultation_date=consultation.consultation_date,
        issued_at=datetime.now().astimezone(),
    )
    pdf_bytes = build(context)
    db.add(GeneratedDocument(
        consultation_id=consultation.id,
        pharmacist_id=pharmacist.id,
        document_type=document_type,
        pdf=pdf_bytes,
        pdf_size=len(pdf_bytes),
    ))
    db.commit()
    return _pdf_response(pdf_bytes, f"{FILE_PREFIXES[document_type]}-atendimento-{consultation.id}.pdf")


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{document_id}", response_class=Response)
def reprint_document(
    patient_id: int, consultation_id: int, document_id: int, db: DB, _pharmacist: CurrentPharmacist
) -> Response:
    """The stored PDF exactly as originally issued (any authenticated pharmacist)."""
    consultation = _get_consultation(db, patient_id, consultation_id)
    document = db.get(GeneratedDocument, document_id)
    if document is None or document.consultation_id != consultation.id:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    if document.pdf is None:
        raise HTTPException(
            status_code=404, detail="Documento emitido antes da reimpressão estar disponível."
        )
    prefix = FILE_PREFIXES.get(document.document_type, "documento")
    return _pdf_response(document.pdf, f"{prefix}-atendimento-{consultation.id}-{document.id}.pdf")


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


@router.post("/dsf", response_class=Response)
def generate_dsf(
    patient_id: int, consultation_id: int, data: DsfIn, db: DB, pharmacist: CurrentPharmacist
) -> Response:
    data.manufacturer = _required(data.manufacturer, "Informe o fabricante do teste.")
    data.lot = _required(data.lot, "Informe o número do lote.")
    consultation = _get_editable_consultation(db, patient_id, consultation_id, pharmacist)
    # Results come only from the consultation (snapshot at issue); the request cannot supply them.
    results = [
        ResultRow(exam=e.exam_name, result=e.result, unit=e.unit, reference=e.reference_range, notes=e.notes)
        for e in consultation.exam_results
    ]
    if not results:
        raise HTTPException(
            status_code=422,
            detail="Registre os resultados de exame neste atendimento antes de emitir a DSF.",
        )
    return _issue(db, patient_id, consultation_id, pharmacist, DSF,
                  lambda ctx: build_dsf(ctx, results, data))
