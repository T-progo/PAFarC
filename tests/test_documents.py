import io
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from backend.auth import create_pharmacist
from backend.config import get_settings
from backend.documents import build_referral
from backend.models import Pharmacist
from backend.pdf import DocumentContext, Establishment
from backend.schemas import ReferralIn

# Fictitious data only.
PATIENT = {"full_name": "Benedito Fictício Exemplar", "date_of_birth": "1968-11-02", "cpf": "11144477735"}
PRESCRIPTION = {"items": [
    {"medication": "Tenofovir desoproxila + lamivudina + dolutegravir", "dosage": "300 mg + 300 mg + 50 mg",
     "route": "Oral", "posology": "1 comprimido ao dia, à noite", "duration": "Uso contínuo",
     "guidance": "Tomar sempre no mesmo horário; usar alarme no celular."},
    {"medication": "Hidratação e cuidado renal", "guidance": "Ingerir ≥ 2 L de água por dia."},
]}
EXAM_REQUEST = {
    "exams": ["Carga Viral HIV-1 RNA", "Linfócitos T-CD4+", "Creatinina sérica", "eGFR / CKD-EPI", "  "],
    "clinical_justification": "Monitoramento de efetividade da TARV e de função renal.",
    "follow_up_context": "Acompanhamento farmacoterapêutico mensal no programa.",
}
REFERRAL = {
    "destination": "Nefrologia — Ambulatório de referência",
    "case_summary": "Paciente em TARV com elevação progressiva da creatinina sérica.",
    "prm": "PRM de segurança: possível nefrotoxicidade relacionada a medicamento.",
    "suggested_conduct": "Avaliar ajuste do esquema e reavaliar função renal em 30 dias.",
}


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)


def _context(establishment: Establishment) -> DocumentContext:
    return DocumentContext(establishment, "Paciente Teste", date(1970, 1, 15), "12345678909",
                           "Farmacêutica Teste", "CRF-SP 11111", date(2026, 1, 10), datetime(2026, 1, 10, 9, 0))


@pytest.fixture
def setup(client, db):
    def login(login, name, crf):
        create_pharmacist(db, full_name=name, crf=crf, login=login, password="pw-123456")
        token = client.post("/auth/login", data={"username": login, "password": "pw-123456"}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    headers_a = login("farm.a", "Farmacêutica Alfa Teste", "CRF-SP 10001")
    headers_b = login("farm.b", "Farmacêutico Beta Teste", "CRF-MG 20002")
    pid = client.post("/patients", json=PATIENT, headers=headers_a).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=headers_a).json()["id"]
    return {"a": headers_a, "b": headers_b, "pid": pid, "cid": cid}


def _doc(client, setup, kind, payload, headers=None):
    url = f"/patients/{setup['pid']}/consultations/{setup['cid']}/documents/{kind}"
    return client.post(url, json=payload, headers=setup["a"] if headers is None else headers)


# --- shared PDF service ----------------------------------------------------------

def test_pdf_is_valid_without_optional_establishment_fields():
    data = build_referral(_context(Establishment(name="Programa Teste - PAFarC")), ReferralIn(**REFERRAL))
    assert data.startswith(b"%PDF-")
    text = pdf_text(data)
    assert "Programa Teste - PAFarC" in text
    for absent in ("CNPJ", "Tel.", "N/A", "None", "Endereço"):
        assert absent not in text
    assert "Data: 10 de janeiro de 2026." in text


def test_pdf_includes_optional_establishment_fields_when_configured():
    est = Establishment(name="Programa Teste", address="Rua Fictícia, 1", cnpj="00.000.000/0000-00",
                        phone="(00) 0000-0000", city="Cidade Teste - UF")
    text = pdf_text(build_referral(_context(est), ReferralIn(**REFERRAL)))
    for value in ("Rua Fictícia, 1", "CNPJ 00.000.000/0000-00", "Tel. (00) 0000-0000",
                  "Cidade Teste - UF, 10 de janeiro de 2026."):
        assert value in text


def test_long_multiline_content_spans_pages():
    long_summary = "\n\n".join(f"Parágrafo {i}: " + "texto clínico de teste com acentuação ção ã é " * 30
                               for i in range(6))
    data = build_referral(
        _context(Establishment(name="Programa Teste")),
        ReferralIn(**{**REFERRAL, "case_summary": long_summary + "\r\nfim\x07" + "x" * 400}),
    )
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) >= 2
    text = pdf_text(data)
    assert "Parágrafo 5:" in text and REFERRAL["suggested_conduct"] in text
    assert "(continuação)" in text and f"Página {len(reader.pages)} de {len(reader.pages)}" in text


# --- documents through the API -----------------------------------------------------

def test_prescription_contains_patient_pharmacist_and_items(client, setup):
    response = _doc(client, setup, "prescription", PRESCRIPTION)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert "Benedito" not in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    text = pdf_text(response.content)
    for value in ("Prescrição / Plano de Cuidado", "Programa de Aperfeiçoamento em Farmácia Clínica - PAFarC",
                  "PharmaTech", "Benedito Fictício Exemplar", "02/11/1968", "111.444.777-35",
                  "Farmacêutica Alfa Teste", "CRF-SP 10001", "Assinatura e carimbo",
                  "1. Tenofovir desoproxila + lamivudina + dolutegravir", "2. Hidratação e cuidado renal",
                  "300 mg + 300 mg + 50 mg", "Oral", "1 comprimido ao dia, à noite", "Uso contínuo",
                  "Tomar sempre no mesmo horário; usar alarme no celular.", "Ingerir ≥ 2 L de água por dia."):
        assert value in text, value


def test_exam_request_contains_exams_and_justification(client, setup):
    response = _doc(client, setup, "exam-request", EXAM_REQUEST)
    assert response.status_code == 200
    text = pdf_text(response.content)
    for value in ("Solicitação de Exames Laboratoriais", "1. Carga Viral HIV-1 RNA", "2. Linfócitos T-CD4+",
                  "3. Creatinina sérica", "4. eGFR / CKD-EPI", EXAM_REQUEST["clinical_justification"],
                  EXAM_REQUEST["follow_up_context"], "Farmacêutica Alfa Teste", "CRF-SP 10001"):
        assert value in text, value
    assert "5." not in text  # blank exam lines are dropped


def test_referral_contains_all_sections(client, setup):
    response = _doc(client, setup, "referral", REFERRAL)
    assert response.status_code == 200
    text = pdf_text(response.content)
    for value in ("Encaminhamento / Interconsulta", "DESTINO", REFERRAL["destination"], "RESUMO DO CASO",
                  REFERRAL["case_summary"], "PROBLEMA RELACIONADO A MEDICAMENTO (PRM) IDENTIFICADO",
                  REFERRAL["prm"], "CONDUTA SUGERIDA", REFERRAL["suggested_conduct"]):
        assert value in text, value


@pytest.mark.parametrize("kind,payload", [
    ("prescription", {"items": []}),
    ("prescription", {"items": [{"medication": "   "}]}),
    ("exam-request", {**EXAM_REQUEST, "exams": [" ", ""]}),
    ("exam-request", {**EXAM_REQUEST, "clinical_justification": "  "}),
    ("referral", {**REFERRAL, "prm": ""}),
    ("referral", {k: v for k, v in REFERRAL.items() if k != "destination"}),
])
def test_invalid_document_content_is_rejected(client, setup, kind, payload):
    assert _doc(client, setup, kind, payload).status_code == 422


# --- metadata, isolation and security ------------------------------------------------

def test_generation_records_metadata_without_changing_consultation(client, setup):
    url = f"/patients/{setup['pid']}/consultations/{setup['cid']}"
    client.put(f"{url}/soap", json={"subjective": "S", "objective": "O", "assessment": "A", "plan": "P"},
               headers=setup["a"])
    before = client.get(url, headers=setup["a"]).json()

    for kind, payload in (("prescription", PRESCRIPTION), ("exam-request", EXAM_REQUEST), ("referral", REFERRAL)):
        assert _doc(client, setup, kind, payload).status_code == 200

    after = client.get(url, headers=setup["a"]).json()
    assert [d["document_type"] for d in after["documents"]] == ["prescription", "exam_request", "referral"]
    assert all(d["pharmacist"]["crf"] == "CRF-SP 10001" for d in after["documents"])
    assert {k: v for k, v in after.items() if k != "documents"} == {
        k: v for k, v in before.items() if k != "documents"
    }


def test_document_routes_require_authentication(client, setup):
    for kind, payload in (("prescription", PRESCRIPTION), ("exam-request", EXAM_REQUEST), ("referral", REFERRAL)):
        assert _doc(client, setup, kind, payload, headers={}).status_code == 401


def test_only_responsible_pharmacist_can_issue_documents(client, setup):
    assert _doc(client, setup, "referral", REFERRAL, headers=setup["b"]).status_code == 403


def test_document_not_reachable_through_other_patient(client, setup):
    other = client.post("/patients", json={**PATIENT, "cpf": "12345678909"}, headers=setup["a"]).json()["id"]
    url = f"/patients/{other}/consultations/{setup['cid']}/documents/referral"
    assert client.post(url, json=REFERRAL, headers=setup["a"]).status_code == 404


def test_no_secrets_in_generated_pdf(client, db, setup):
    settings = get_settings()
    data = _doc(client, setup, "prescription", PRESCRIPTION).content
    text = pdf_text(data)
    password_hash = db.query(Pharmacist).filter_by(login="farm.a").one().password_hash
    for secret in (settings.secret_key, settings.data_encryption_key, settings.blind_index_key, password_hash):
        assert secret not in text
        assert secret.encode() not in data


def test_no_pdf_files_left_behind(client, setup, tmp_path, monkeypatch):
    def pdf_files():
        roots = [Path(tempfile.gettempdir()), Path.cwd()]
        return {p for root in roots for p in root.glob("*.pdf")}

    before = pdf_files()
    for kind, payload in (("prescription", PRESCRIPTION), ("exam-request", EXAM_REQUEST), ("referral", REFERRAL)):
        assert _doc(client, setup, kind, payload).status_code == 200
    assert pdf_files() == before


# --- reprint of issued documents ----------------------------------------------------------

def _issued(client, setup):
    url = f"/patients/{setup['pid']}/consultations/{setup['cid']}"
    return client.get(url, headers=setup["a"]).json()["documents"]


def _reprint(client, setup, doc_id, headers=None):
    url = f"/patients/{setup['pid']}/consultations/{setup['cid']}/documents/{doc_id}"
    return client.get(url, headers=setup["a"] if headers is None else headers)


def test_reprint_returns_the_original_pdf(client, setup):
    original = _doc(client, setup, "prescription", PRESCRIPTION).content
    [doc] = _issued(client, setup)
    assert doc["reprintable"] is True
    response = _reprint(client, setup, doc["id"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == original
    # any authenticated pharmacist may reprint (same as read access)
    assert _reprint(client, setup, doc["id"], headers=setup["b"]).content == original


def test_reprint_survives_application_restart(client, setup):
    from fastapi.testclient import TestClient

    from backend.database import get_engine
    from backend.main import app

    original = _doc(client, setup, "referral", REFERRAL).content
    doc_id = _issued(client, setup)[0]["id"]
    get_engine().dispose()  # drop every pooled connection, as a process restart would
    with TestClient(app) as restarted:  # runs the startup sequence again
        assert _reprint(restarted, setup, doc_id).content == original


def test_reprint_unaffected_by_later_changes(client, db, setup, monkeypatch):
    from backend import documents
    from backend.models import Patient, Pharmacist

    original = _doc(client, setup, "exam-request", EXAM_REQUEST).content
    doc_id = _issued(client, setup)[0]["id"]
    # change patient, pharmacist, consultation content and even the document template
    patient = db.get(Patient, setup["pid"])
    patient.full_name = "Nome Alterado Depois"
    db.query(Pharmacist).filter_by(login="farm.a").one().full_name = "Farmacêutica Renomeada"
    db.commit()
    url = f"/patients/{setup['pid']}/consultations/{setup['cid']}"
    soap = {"subjective": "novo", "objective": "", "assessment": "", "plan": ""}
    client.put(f"{url}/soap", json=soap, headers=setup["a"])
    monkeypatch.setitem(documents.DOCUMENT_TITLES, "exam_request", "Modelo Novo")

    reprinted = _reprint(client, setup, doc_id).content
    assert reprinted == original
    text = pdf_text(reprinted)
    assert "Benedito Fictício Exemplar" in text and "Farmacêutica Alfa Teste" in text
    assert "Nome Alterado Depois" not in text and "Modelo Novo" not in text


def test_reprint_requires_authentication_and_scope(client, setup):
    _doc(client, setup, "prescription", PRESCRIPTION)
    doc_id = _issued(client, setup)[0]["id"]
    assert _reprint(client, setup, doc_id, headers={}).status_code == 401
    assert _reprint(client, setup, 9999).status_code == 404
    other = client.post("/patients", json={**PATIENT, "cpf": "12345678909"}, headers=setup["a"]).json()["id"]
    other_c = client.post(f"/patients/{other}/consultations", json={}, headers=setup["a"]).json()["id"]
    url = f"/patients/{other}/consultations/{other_c}/documents/{doc_id}"
    assert client.get(url, headers=setup["a"]).status_code == 404  # document of another consultation


def test_legacy_document_without_stored_pdf_is_not_reprintable(client, db, setup):
    from backend.models import GeneratedDocument

    db.add(GeneratedDocument(consultation_id=setup["cid"], pharmacist_id=1, document_type="referral"))
    db.commit()
    [doc] = _issued(client, setup)
    assert doc["reprintable"] is False
    assert _reprint(client, setup, doc["id"]).status_code == 404


def test_stored_pdf_is_encrypted_at_rest(client, setup):
    from sqlalchemy import text

    from backend.database import get_engine

    original = _doc(client, setup, "prescription", PRESCRIPTION).content
    with get_engine().connect() as connection:
        stored = connection.execute(text("SELECT pdf_encrypted, pdf_size FROM generated_documents")).one()
    assert stored.pdf_size == len(original)
    assert b"%PDF" not in bytes(stored.pdf_encrypted)
    get_engine().dispose()
    assert b"%PDF-" not in Path(get_engine().url.database).read_bytes()


def test_existing_database_gets_new_document_columns(client):
    from sqlalchemy import inspect, text

    from backend.database import get_engine, init_db

    with get_engine().begin() as connection:  # recreate the pre-reprint table layout
        connection.execute(text("DROP TABLE generated_documents"))
        connection.execute(text(
            "CREATE TABLE generated_documents (id INTEGER PRIMARY KEY, consultation_id INTEGER, "
            "pharmacist_id INTEGER, document_type VARCHAR(30), created_at DATETIME)"))
    init_db()
    columns = {c["name"] for c in inspect(get_engine()).get_columns("generated_documents")}
    assert {"pdf_encrypted", "pdf_size"} <= columns
