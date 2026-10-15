"""Document date line: only 'Data: DD/MM/AAAA', no city/UF beside it (client's final rule)."""
import io
import re
from datetime import date

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from backend.auth import create_pharmacist
from backend.models import ROLE_ADMINISTRATOR, GeneratedDocument

PASSWORD = "senha-segura-1"
EST = {"name": "Farmácia Fictícia Data", "cnpj": "33.333.333/0001-33",
       "address": "Rua Fictícia, 50 - Centro - Rio de Janeiro - RJ", "phone": "(21) 3333-3333"}
PATIENT = {"full_name": "Paciente Ficticio Data", "date_of_birth": "1970-02-02", "cpf": "12345678909"}
DOC_PAYLOADS = {
    "prescription": {"items": [{"medication": "Item de teste"}]},
    "exam-request": {"exams": ["Ureia"], "clinical_justification": "Justificativa de teste."},
    "referral": {"destination": "Nefrologia", "case_summary": "Resumo.", "prm": "PRM.", "suggested_conduct": "Conduta."},
}


def _headers(client, login):
    token = client.post("/auth/login", data={"username": login, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def pdf_text(data):
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture
def pharmacist(client, db, monkeypatch):
    # the former city setting may still be present in a server's environment: it must be ignored
    monkeypatch.setenv("PHARMATECH_ESTABLISHMENT_CITY", "Cidade Antiga - XX")
    create_pharmacist(db, full_name="Admin Data", crf="", login="admin@data.test", password=PASSWORD,
                      role=ROLE_ADMINISTRATOR)
    admin = _headers(client, "admin@data.test")
    est = client.post("/admin/establishments", headers=admin, json=EST).json()["id"]
    client.post("/admin/users", headers=admin, json={
        "full_name": "Farmacêutica Data", "crf": "16906", "crf_uf": "RJ", "login": "farm@data.test",
        "password": PASSWORD, "establishment_id": est})
    headers = _headers(client, "farm@data.test")
    pid = client.post("/patients", json=PATIENT, headers=headers).json()["id"]
    return headers, pid


@pytest.mark.parametrize("kind", list(DOC_PAYLOADS))
def test_date_line_shows_only_the_date(client, pharmacist, kind):
    headers, pid = pharmacist
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=headers).json()["id"]
    response = client.post(f"/patients/{pid}/consultations/{cid}/documents/{kind}", json=DOC_PAYLOADS[kind],
                           headers=headers)
    assert response.status_code == 200
    text = pdf_text(response.content)
    today = date.today().strftime("%d/%m/%Y")

    assert f"Data: {today}" in text  # the date is there, in the new format
    assert not re.search(r"[A-Za-zÀ-ú]+ - [A-Z]{2}, \d{2}/\d{2}/\d{4}", text)  # no "Cidade - UF, DD/MM/AAAA"
    assert not re.search(r"\d{1,2} de [a-zç]+ de \d{4}", text)  # old long date gone
    assert "Cidade Antiga" not in text  # former global city setting ignored

    # establishment only in the header (address with its city printed once, not beside the date)
    for value in (EST["name"], f"CNPJ {EST['cnpj']}", EST["address"], f"Tel. {EST['phone']}"):
        assert value in text, (kind, value)
    assert text.count("Rio de Janeiro - RJ") == 1
    # pharmacist identification unchanged
    assert "Farmacêutica Data" in text and "CRF/RJ 16906" in text


def test_historical_pdfs_reprint_unchanged(client, pharmacist, db):
    headers, pid = pharmacist
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=headers).json()["id"]
    issued = client.post(f"/patients/{pid}/consultations/{cid}/documents/referral",
                         json=DOC_PAYLOADS["referral"], headers=headers).content
    # a document issued before this change (old "Cidade - UF, long date" line) is stored as-is
    old_pdf = b"%PDF-1.3 legacy document with 'Rio de Janeiro - RJ, 5 de outubro de 2026.' %%EOF"
    pharmacist_id = client.get("/auth/me", headers=headers).json()["id"]
    db.add(GeneratedDocument(consultation_id=cid, pharmacist_id=pharmacist_id, document_type="prescription",
                             pdf=old_pdf, pdf_size=len(old_pdf)))
    db.commit()
    docs = db.scalars(select(GeneratedDocument).where(GeneratedDocument.consultation_id == cid)
                      .order_by(GeneratedDocument.id)).all()
    base = f"/patients/{pid}/consultations/{cid}/documents"
    assert client.get(f"{base}/{docs[0].id}", headers=headers).content == issued
    assert client.get(f"{base}/{docs[1].id}", headers=headers).content == old_pdf  # never regenerated
