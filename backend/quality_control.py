"""Controle de Qualidade (CIQ / CEQ): manual records of quality-control executions.

A record belongs to the establishment where it was created and is visible only to pharmacists
currently linked to that establishment (records of another establishment answer 404). Only its
creator can change a draft or cancel it (which deletes it). Finalizing validates it (a rejected
control needs the non-conformity / corrective action), builds the PDF from the stored data and
stores it, all in the same locked transaction as the change to finalized; afterwards the record is
read-only and the PDF is always returned exactly as issued. No result is calculated or interpreted.
"""

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import consultations
from backend.auth import get_current_pharmacist, require_clinical
from backend.database import get_db
from backend.models import Pharmacist, QualityControl
from backend.pdf import Establishment, QualityControlContext, QualityControlPDF, clean_text, crf_label
from backend.schemas import QualityControlIn, QualityControlOut, QualityControlSummary

CurrentPharmacist = Annotated[Pharmacist, Depends(get_current_pharmacist)]
DB = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/quality-controls", tags=["quality-control"], dependencies=[Depends(require_clinical)])

TITLE = "REGISTRO DE CONTROLE DE QUALIDADE (CIQ / CEQ)"
STATUS_LABELS = {"APPROVED": "APROVADO / ADEQUADO", "REJECTED": "REJEITADO / INADEQUADO"}
REPEAT_LABELS = {"APPROVED": "Aprovado", "REJECTED": "Rejeitado"}
ROOT_CAUSE_LABELS = {
    "EXPIRED": "Validade do reagente/controle expirada",
    "STORAGE": "Erro de armazenamento / reconstituição do controle",
    "CALIBRATION": "Falha de calibração do equipamento/dispositivo",
    "DEFECTIVE_LOT": "Insumo/fita com defeito de lote",
    "OTHER": "Outro",
}
DECISION_LABELS = {
    "APPROVED_FOR_PATIENT_TESTING": "Aprovado para realização e liberação dos exames dos pacientes",
    "BLOCKED_UNTIL_TECHNICAL_MAINTENANCE": "Bloqueado até manutenção técnica",
}
TEXT_FIELDS = ("exam_analyte", "equipment_methodology", "provider_manufacturer", "control_lot", "value_found", "unit",
               "target_mean", "standard_deviation", "acceptable_range_min", "acceptable_range_max")
NC_TEXT_FIELDS = ("nc_root_cause_other", "nc_corrective_action", "nc_repeat_value")
NC_CHOICE_FIELDS = ("nc_root_cause", "nc_repeat_status", "nc_patient_decision")
REQUIRED = {  # field -> label used in the "complete before finalizing" message
    "exam_analyte": "Exame / Analito", "equipment_methodology": "Equipamento / Metodologia",
    "provider_manufacturer": "Provedor / Fabricante", "control_lot": "Lote do controle",
    "control_expiry": "Validade do controle", "value_found": "Valor encontrado", "unit": "Unidade",
    "target_mean": "Média alvo", "standard_deviation": "Desvio padrão",
    "acceptable_range_min": "Faixa aceitável (mínimo)", "acceptable_range_max": "Faixa aceitável (máximo)",
    "evaluation_status": "Status da avaliação",
}
NC_REQUIRED = {
    "nc_root_cause": "Causa raiz", "nc_corrective_action": "Ação corretiva adotada",
    "nc_repeat_value": "Novo valor obtido (repetição)", "nc_repeat_status": "Status do novo teste",
    "nc_patient_decision": "Decisão quanto às amostras dos pacientes",
}
FINALIZED = "Registro finalizado. Os dados deste controle de qualidade são somente para consulta."
_EXPIRY = re.compile(r"^(\d{1,2})/(\d{4})$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _establishment_id(pharmacist: Pharmacist) -> int:
    if pharmacist.establishment_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Seu usuário não está vinculado a um estabelecimento. Procure o administrador.")
    return pharmacist.establishment_id


def _get_visible(db: Session, record_id: int, pharmacist: Pharmacist) -> QualityControl:
    """Records of the pharmacist's current establishment only; any other answers 404 (no leak)."""
    establishment_id = _establishment_id(pharmacist)
    record = db.get(QualityControl, record_id)
    if record is None or record.establishment_id != establishment_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Registro de controle de qualidade não encontrado.")
    return record


def _check_creator(record: QualityControl, pharmacist: Pharmacist) -> None:
    if record.pharmacist_id != pharmacist.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Somente o farmacêutico que criou o registro pode alterá-lo.")


def _get_mutable(db: Session, record_id: int, pharmacist: Pharmacist) -> QualityControl:
    """Access is checked first (404, 403); then the same write lock as the consultations
    (BEGIN IMMEDIATE) is taken and everything is re-read, so a finalization committed by another
    request is seen and no change can commit after it."""
    _check_creator(_get_visible(db, record_id, pharmacist), pharmacist)
    consultations._begin_write(db)
    db.expire_all()
    record = _get_visible(db, record_id, pharmacist)
    _check_creator(record, pharmacist)
    if record.finalized_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=FINALIZED)
    return record


def _normalize_expiry(value: str) -> str | None:
    """MM/YYYY (also M/YYYY) -> stored YYYY-MM; empty -> None."""
    value = value.strip()
    if not value:
        return None
    match = _EXPIRY.match(value)
    if not match or not 1 <= int(match.group(1)) <= 12 or not 2000 <= int(match.group(2)) <= 2100:
        raise HTTPException(status_code=422, detail="Validade do controle inválida. Use MM/AAAA.")
    return f"{match.group(2)}-{int(match.group(1)):02d}"


def _apply(record: QualityControl, data: QualityControlIn, pharmacist: Pharmacist) -> None:
    record.control_type = data.control_type
    for field in TEXT_FIELDS + NC_TEXT_FIELDS:
        setattr(record, field, clean_text(getattr(data, field)) or None)
    record.control_expiry = _normalize_expiry(data.control_expiry)
    for field in ("evaluation_status",) + NC_CHOICE_FIELDS:
        setattr(record, field, getattr(data, field))
    # The non-conformity event is registered when the control is first marked rejected.
    if record.evaluation_status == "REJECTED":
        record.nc_event_at = record.nc_event_at or _now()
    else:
        record.nc_event_at = None
    record.responsible_name = pharmacist.full_name
    record.responsible_crf = pharmacist.crf_display


def _missing(record: QualityControl) -> list[str]:
    missing = [label for field, label in REQUIRED.items() if not getattr(record, field)]
    if record.evaluation_status == "REJECTED":
        missing += [label for field, label in NC_REQUIRED.items() if not getattr(record, field)]
        if record.nc_root_cause == "OTHER" and not record.nc_root_cause_other:
            missing.append("Descrição da causa raiz (Outro)")
    return missing


def format_datetime(value: datetime | None) -> str:
    """DD/MM/YYYY - HH:MM in the server's local time (values are stored in UTC)."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d/%m/%Y - %H:%M")


def build_quality_control_pdf(record: QualityControl, issued_at: datetime) -> bytes:
    est = record.establishment
    ctx = QualityControlContext(
        establishment=Establishment(name=est.name, address=est.address, cnpj=est.cnpj, phone=est.phone),
        pharmacist_name=record.responsible_name, pharmacist_crf=record.responsible_crf, issued_at=issued_at,
    )
    pdf = QualityControlPDF(TITLE, ctx)
    pdf.section("Dados da execução")
    pdf.labeled_line("Data e hora", format_datetime(record.execution_at))
    pdf.labeled_line("Tipo de controle", record.control_type)
    pdf.labeled_line("Exame / Analito", record.exam_analyte)
    pdf.labeled_line("Equipamento / Metodologia", record.equipment_methodology)
    pdf.section("Dados do controle / reagente")
    pdf.labeled_line("Provedor / Fabricante", record.provider_manufacturer)
    pdf.labeled_line("Lote do controle", record.control_lot)
    pdf.labeled_line("Data de validade", record.control_expiry_display)
    pdf.section("Resultado obtido")
    pdf.labeled_line("Valor encontrado", record.value_found)
    pdf.labeled_line("Unidade de medida", record.unit)
    pdf.labeled_line("Média alvo", record.target_mean)
    pdf.labeled_line("Desvio padrão", record.standard_deviation)
    pdf.labeled_line("Faixa aceitável", f"{record.acceptable_range_min} a {record.acceptable_range_max}")
    pdf.section("Status da avaliação")
    rejected = record.evaluation_status == "REJECTED"
    pdf.status_box(STATUS_LABELS[record.evaluation_status], alert=rejected)
    pdf.section("Responsável")
    pdf.labeled_line("Farmacêutico(a)", record.responsible_name)
    pdf.labeled_line("CRF/UF", crf_label(record.responsible_crf))
    if rejected:
        pdf.part_heading("REGISTRO DE NÃO CONFORMIDADE E AÇÃO CORRETIVA")
        pdf.labeled_line("Data do evento", format_datetime(record.nc_event_at))
        pdf.labeled_line("Exame afetado", record.exam_analyte)
        pdf.labeled_line("Valor fora da faixa encontrado", f"{record.value_found} {record.unit or ''}".strip())
        pdf.section("Análise de causa raiz")
        pdf.paragraph(ROOT_CAUSE_LABELS[record.nc_root_cause])
        if record.nc_root_cause == "OTHER":
            pdf.labeled_line("Descrição", record.nc_root_cause_other)
        pdf.section("Ação corretiva adotada")
        pdf.paragraph(record.nc_corrective_action)
        pdf.section("Repetição do teste de controle")
        pdf.labeled_line("Novo valor obtido", record.nc_repeat_value)
        pdf.labeled_line("Status do novo teste", REPEAT_LABELS[record.nc_repeat_status])
        pdf.section("Decisão quanto às amostras dos pacientes")
        pdf.paragraph(DECISION_LABELS[record.nc_patient_decision])
        pdf.section("Responsável técnico")
        pdf.labeled_line("Farmacêutico(a)", record.responsible_name)
        pdf.labeled_line("CRF/UF", crf_label(record.responsible_crf))
    pdf.closing()
    return pdf.to_bytes()


# --- routes --------------------------------------------------------------------------

@router.get("", response_model=list[QualityControlSummary])
def list_quality_controls(db: DB, pharmacist: CurrentPharmacist) -> list[QualityControl]:
    """History of the pharmacist's current establishment, newest first."""
    establishment_id = _establishment_id(pharmacist)
    return list(db.scalars(
        select(QualityControl).where(QualityControl.establishment_id == establishment_id)
        .order_by(QualityControl.execution_at.desc(), QualityControl.id.desc())
    ).all())


@router.post("", response_model=QualityControlOut, status_code=status.HTTP_201_CREATED)
def create_quality_control(data: QualityControlIn, db: DB, pharmacist: CurrentPharmacist) -> QualityControl:
    record = QualityControl(
        establishment_id=_establishment_id(pharmacist),  # kept even if the pharmacist moves later
        pharmacist_id=pharmacist.id,
        execution_at=_now(),
    )
    _apply(record, data, pharmacist)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/{record_id}", response_model=QualityControlOut)
def get_quality_control(record_id: int, db: DB, pharmacist: CurrentPharmacist) -> QualityControl:
    return _get_visible(db, record_id, pharmacist)


@router.put("/{record_id}", response_model=QualityControlOut)
def update_quality_control(record_id: int, data: QualityControlIn, db: DB,
                           pharmacist: CurrentPharmacist) -> QualityControl:
    record = _get_mutable(db, record_id, pharmacist)
    _apply(record, data, pharmacist)
    db.commit()
    db.refresh(record)
    return record


@router.post("/{record_id}/finalize", response_model=QualityControlOut)
def finalize_quality_control(record_id: int, db: DB, pharmacist: CurrentPharmacist) -> QualityControl:
    """Validate, build and store the PDF and mark the record finalized, in one locked transaction:
    if anything fails nothing is committed, so a finalized record always has its PDF."""
    record = _get_mutable(db, record_id, pharmacist)
    missing = _missing(record)
    if missing:
        raise HTTPException(status_code=422, detail="Complete antes de finalizar: " + "; ".join(missing) + ".")
    if record.evaluation_status != "REJECTED":  # the non-conformity part only belongs to rejected controls
        for field in NC_TEXT_FIELDS + NC_CHOICE_FIELDS + ("nc_event_at",):
            setattr(record, field, None)
    record.responsible_name = pharmacist.full_name
    record.responsible_crf = pharmacist.crf_display
    finalized_at = _now()
    pdf_bytes = build_quality_control_pdf(record, finalized_at.astimezone())
    record.pdf = pdf_bytes
    record.pdf_size = len(pdf_bytes)
    record.finalized_at = finalized_at
    db.commit()
    db.refresh(record)
    return record


@router.post("/{record_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_quality_control(record_id: int, db: DB, pharmacist: CurrentPharmacist) -> Response:
    """Discard a draft its creator gave up on ("Cancelar lançamento"). A draft is a single row
    (nothing references it and it has no PDF), so it is deleted, under the same write lock as
    finalization: a finalized record answers 409 and is never deleted; a draft already cancelled
    answers 404."""
    record = _get_mutable(db, record_id, pharmacist)
    db.delete(record)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{record_id}/pdf", response_class=Response)
def quality_control_pdf(record_id: int, db: DB, pharmacist: CurrentPharmacist) -> Response:
    """The stored PDF exactly as issued at finalization (any pharmacist of the establishment)."""
    record = _get_visible(db, record_id, pharmacist)
    if record.finalized_at is None or record.pdf is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="O PDF fica disponível após a finalização do registro.")
    return Response(
        content=record.pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="controle-qualidade-{record.control_type.lower()}-'
                                        f'{record.id}.pdf"', "Cache-Control": "no-store"},
    )
