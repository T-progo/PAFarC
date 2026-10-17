"""Declaração de Serviço Farmacêutico (DSF): built entirely from the consultation's recorded exam
results, including each result's stored Fabricante/Lote/Validade. Fictitious data only."""
import hashlib
import io
import re
from datetime import date

import pytest
from pypdf import PdfReader

from backend.auth import create_pharmacist
from backend.documents import DSF_NOTICE
from backend.models import ROLE_ADMINISTRATOR

PASSWORD = "senha-segura-1"
EST_A = {"name": "Farmácia Fictícia DSF A", "cnpj": "11.222.333/0001-81",
         "address": "Rua Fictícia, 100 - Centro - Niterói - RJ", "phone": "(21) 2222-1111"}
EST_B = {"name": "Farmácia Fictícia DSF B", "cnpj": "44.555.666/0001-02",
         "address": "Avenida Fictícia, 200 - Bairro - Belo Horizonte - MG", "phone": "(31) 3333-2222"}
PATIENT = {"full_name": "Paciente Fictícia Declaração", "date_of_birth": "1975-06-20", "cpf": "52998224725"}
LONG_EXAM = ("Hemoglobina glicada (HbA1c) por cromatografia líquida de alta eficiência em sangue capilar "
             "- teste rápido point-of-care")
EXAMS = [
    {"exam_name": "Glicemia capilar", "result": "98", "unit": "mg/dL", "reference_range": "70 - 99",
     "notes": "Jejum de 8 horas."},
    {"exam_name": LONG_EXAM, "result": "5,9", "unit": "%", "reference_range": "< 5,7",
     "notes": "Observação longa: " + "resultado sujeito a confirmação laboratorial; " * 25},
    {"exam_name": "Colesterol total", "result": "182", "unit": "mg/dL", "reference_range": "< 190", "notes": ""},
]
KITS = [("Fabricante Alfa Diagnósticos", "LOTE-ALFA-001", "2027-01-31", "31/01/2027"),
        ("Fabricante Beta Testes Rápidos", "LOTE-BETA-002", "2027-06-30", "30/06/2027"),
        ("Fabricante Gama Lab", "LOTE-GAMA-003", "2026-12-15", "15/12/2026")]
OLD_NOTICE = "Este exame possui finalidade de triagem e rastreamento"


def pdf_text(data: bytes) -> str:
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)


def _headers(client, login):
    token = client.post("/auth/login", data={"username": login, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(client, db):
    create_pharmacist(db, full_name="Admin DSF", crf="", login="admin@dsf.test", password=PASSWORD,
                      role=ROLE_ADMINISTRATOR)
    admin = _headers(client, "admin@dsf.test")
    est_a = client.post("/admin/establishments", headers=admin, json=EST_A).json()["id"]
    est_b = client.post("/admin/establishments", headers=admin, json=EST_B).json()["id"]
    ids = {}
    for name, crf, uf, login, est in (("Farmacêutica Alfa DSF", "10001", "RJ", "a@dsf.test", est_a),
                                      ("Farmacêutico Beta DSF", "20002", "MG", "b@dsf.test", est_b)):
        ids[login] = client.post("/admin/users", headers=admin, json={
            "full_name": name, "crf": crf, "crf_uf": uf, "login": login, "password": PASSWORD,
            "establishment_id": est}).json()["id"]
    a, b = _headers(client, "a@dsf.test"), _headers(client, "b@dsf.test")
    pid = client.post("/patients", json=PATIENT, headers=a).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={"consultation_date": "2026-01-12"},
                      headers=a).json()["id"]
    base = f"/patients/{pid}/consultations/{cid}"
    exam_ids = [client.post(f"{base}/exams", headers=a, json={
        **exam, "manufacturer": kit[0], "lot": kit[1], "expiry_date": kit[2]}).json()["id"]
        for exam, kit in zip(EXAMS, KITS)]
    return {"admin": admin, "a": a, "b": b, "pid": pid, "cid": cid, "est_a": est_a, "est_b": est_b,
            "a_id": ids["a@dsf.test"], "base": base, "exam_ids": exam_ids}


def _dsf(client, env, payload=None, headers=None, base=None):
    return client.post(f"{base or env['base']}/documents/dsf", json={} if payload is None else payload,
                       headers=env["a"] if headers is None else headers)


def _add_exam(client, env, base=None, **fields):
    exam = {"exam_name": "Exame extra", "result": "1", **fields}
    r = client.post(f"{base or env['base']}/exams", headers=env["a"], json=exam)
    assert r.status_code == 201
    return r.json()["id"]


def _group(text, number, next_number=None):
    """Text of exam group `number` in the results table (up to the next group or the notice)."""
    start = text.index(f" {number} {EXAMS[number - 1]['exam_name'][:20]}")
    end = text.index(f" {next_number} ", start + 1) if next_number else text.index("AVISO", start)
    return text[start:end]


# --- authorization ---------------------------------------------------------------

def test_responsible_pharmacist_issues_dsf(client, env):
    r = _dsf(client, env)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert 'filename="declaracao-servico-farmaceutico-atendimento-' in r.headers["content-disposition"]


def test_other_pharmacist_cannot_issue_dsf_for_the_consultation(client, env):
    assert _dsf(client, env, headers=env["b"]).status_code == 403


def test_administrator_gets_403(client, env):
    assert _dsf(client, env, headers=env["admin"]).status_code == 403


def test_unauthenticated_gets_401(client, env):
    assert _dsf(client, env, headers={}).status_code == 401


# --- stored per-exam kit -----------------------------------------------------------------

def test_each_exam_uses_its_own_stored_kit(client, env):
    text = pdf_text(_dsf(client, env).content)
    groups = [_group(text, 1, 2), _group(text, 2, 3), _group(text, 3)]
    for n, (group, (manufacturer, lot, _, expiry)) in enumerate(zip(groups, KITS)):
        assert f"Fabricante: {manufacturer}" in group and f"Lote: {lot}" in group and f"Validade: {expiry}" in group
        for other in (k for i, k in enumerate(KITS) if i != n):  # nothing from another exam's kit
            assert other[0] not in group and other[1] not in group and other[3] not in group


def test_request_cannot_override_the_stored_kit(client, env):
    forged = {"exam_metadata": [{"exam_result_id": i, "manufacturer": "FAB FORJADO", "lot": "LOTE FORJADO",
                                 "expiry_date": "1999-01-01"} for i in env["exam_ids"]],
              "manufacturer": "FAB FORJADO", "lot": "LOTE FORJADO", "expiry_date": "1999-01-01"}
    text = pdf_text(_dsf(client, env, forged).content)
    assert "FORJADO" not in text and "1999" not in text
    assert all(f"Lote: {lot}" in text for _, lot, _, _ in KITS)


@pytest.mark.parametrize("missing", ["manufacturer", "lot", "expiry_date"])
def test_any_exam_without_complete_kit_blocks_the_dsf(client, env, missing):
    kit = {"manufacturer": "Fab", "lot": "L1", "expiry_date": "2027-01-01"}
    kit[missing] = None
    _add_exam(client, env, exam_name="Ureia sem kit", **kit)
    r = _dsf(client, env)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Complete Fabricante, Lote e Validade" in detail and "Resultados de exames deste atendimento" in detail
    assert "Ureia sem kit" in detail and "Glicemia capilar" not in detail
    assert client.get(env["base"], headers=env["a"]).json()["documents"] == []


def test_completing_the_kit_allows_the_dsf(client, env):
    exam_id = _add_exam(client, env, exam_name="Ureia")
    assert _dsf(client, env).status_code == 422
    client.put(f"{env['base']}/exams/{exam_id}", headers=env["a"], json={
        "exam_name": "Ureia", "result": "1", "manufacturer": "Fab U", "lot": "LOTE-U", "expiry_date": "2020-05-01"})
    r = _dsf(client, env)  # a past expiry date is informational only
    assert r.status_code == 200 and "Validade: 01/05/2020" in pdf_text(r.content)


# --- authoritative data ------------------------------------------------------------------

def test_request_cannot_override_exam_data_or_identities(client, env):
    payload = {"exam_metadata": [{"exam_result_id": i, "exam_name": "EXAME FORJADO", "result": "RESULTADO FORJADO",
                                  "unit": "UNIDADE FORJADA", "reference_range": "REFERENCIA FORJADA",
                                  "notes": "OBSERVACAO FORJADA"} for i in env["exam_ids"]]}
    payload.update({"exam_results": [{"exam_name": "EXAME FORJADO"}], "consultation_date": "1999-01-01",
                    "performance_date": "1999-01-01", "establishment_id": env["est_b"], "establishment": EST_B,
                    "patient_name": "PACIENTE FORJADO", "pharmacist_name": "FARM FORJADO", "crf": "CRF/XX 999"})
    text = pdf_text(_dsf(client, env, payload).content)
    assert "FORJAD" not in text and "1999" not in text and "CRF/XX" not in text
    for exam in EXAMS:
        assert exam["exam_name"][:40] in text and exam["result"] in text and exam["reference_range"] in text
    assert "Observações: Jejum de 8 horas." in text
    assert PATIENT["full_name"] in text and "CPF: 529.982.247-25" in text and "Data de nascimento: 20/06/1975" in text
    assert "Farmacêutica Alfa DSF" in text and "CRF/RJ 10001" in text
    for value in (EST_A["name"], f"CNPJ {EST_A['cnpj']}", EST_A["address"], f"Tel. {EST_A['phone']}"):
        assert value in text
    for value in (EST_B["name"], EST_B["cnpj"], EST_B["address"]):
        assert value not in text


def test_dsf_does_not_create_or_change_exam_records(client, env):
    before = client.get(env["base"], headers=env["a"]).json()["exam_results"]
    _dsf(client, env)
    assert client.get(env["base"], headers=env["a"]).json()["exam_results"] == before


# --- content: date, notice, signature -------------------------------------------------------

def test_dates_notice_and_signature(client, env):
    text = pdf_text(_dsf(client, env).content)
    assert "Declaração de Serviço Farmacêutico" in text
    assert "Data da realização: 12/01/2026" in text  # = consultation_date
    assert text.count("Data da realização") == 1
    assert DSF_NOTICE == ("Os exames realizados possuem finalidade de triagem, rastreamento e acompanhamento "
                          "farmacoterapêutico, não constituindo diagnóstico confirmatório isolado.")
    assert DSF_NOTICE in text and text.count("AVISO") == 1
    assert OLD_NOTICE not in text and "confirmação diagnóstica" not in text
    assert f"Data: {date.today():%d/%m/%Y}" in text
    assert not re.search(r"[A-Za-zÀ-ú]+ - [A-Z]{2}, \d{2}/\d{2}/\d{4}", text)
    assert not re.search(r"\d{1,2} de [a-zç]+ de \d{4}", text)
    assert "Assinatura e carimbo" in text and "Farmacêutico(a) · CRF/RJ 10001" in text
    assert "serviço farmacêutico realizado" not in text.lower()
    assert "Dados do teste".upper() not in text  # no global kit block any more


def test_results_render_with_unit_reference_and_long_notes(client, env):
    text = pdf_text(_dsf(client, env).content)
    for value in ("Nº", "Exame", "Resultado", "Unidade", "Valor de referência", "Glicemia capilar", "98", "mg/dL",
                  "70 - 99", "5,9", "< 5,7", "Colesterol total", "182", "< 190"):
        assert value in text, value
    assert text.count("resultado sujeito a confirmação laboratorial") == 25  # wrapped, nothing lost
    assert all(word in text for word in LONG_EXAM.split())


def test_many_results_span_pages_keeping_each_exam_together(client, env):
    for i in range(30):
        client.post(f"{env['base']}/exams", headers=env["a"], json={
            "exam_name": f"Teste rápido {i + 1}", "result": "Não reagente", "reference_range": "Não reagente",
            "notes": "Sem intercorrências.", "manufacturer": f"Fab {i + 3}", "lot": f"LOTE-{i + 3:03d}",
            "expiry_date": "2027-01-01"})
    exams = client.get(env["base"], headers=env["a"]).json()["exam_results"]
    data = _dsf(client, env).content
    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    assert pages >= 3
    page_texts = [" ".join((p.extract_text() or "").split()) for p in reader.pages]
    for exam in exams[3:]:  # an exam's result and its own lot are on the same page
        lot = f"Lote: {exam['lot']}"
        page = next(i for i, t in enumerate(page_texts) if lot in t)
        assert exam["exam_name"][:20] in page_texts[page]
    text = " ".join(page_texts)
    assert f"Página {pages} de {pages}" in text and "(continuação)" in text
    assert all(t.count("Valor de referência") >= 1 for t in page_texts[:-1])  # heading repeated
    assert "Assinatura e carimbo" in page_texts[-1] and DSF_NOTICE in text


def test_consultation_without_exam_results_is_rejected(client, env):
    cid = client.post(f"/patients/{env['pid']}/consultations", json={}, headers=env["a"]).json()["id"]
    base = f"/patients/{env['pid']}/consultations/{cid}"
    r = _dsf(client, env, base=base)
    assert r.status_code == 422 and "Registre os resultados de exame" in r.json()["detail"]
    assert client.get(base, headers=env["a"]).json()["documents"] == []


# --- history and reprint ---------------------------------------------------------------------

def test_dsf_history_and_byte_identical_reprint_after_source_changes(client, env):
    issued = _dsf(client, env).content
    docs = client.get(env["base"], headers=env["a"]).json()["documents"]
    assert [d["document_type"] for d in docs] == ["dsf"] and docs[0]["reprintable"]
    doc_id = docs[0]["id"]
    digest = hashlib.sha256(issued).hexdigest()

    ids = env["exam_ids"]
    client.put(f"{env['base']}/exams/{ids[0]}", headers=env["a"],
               json={"exam_name": "Glicemia ALTERADA", "result": "250", "unit": "mg/dL", "manufacturer": "Fab X",
                     "lot": "LOTE-X", "expiry_date": "2028-01-01"})
    client.delete(f"{env['base']}/exams/{ids[2]}", headers=env["a"])
    client.patch(f"/admin/establishments/{env['est_a']}", headers=env["admin"], json={"name": "Nome NOVO"})
    client.patch(f"/admin/users/{env['a_id']}", headers=env["admin"], json={"full_name": "Nome Farm NOVO"})
    client.put(f"{env['base']}/exams/{ids[1]}", headers=env["a"], json={
        **EXAMS[1], "manufacturer": "Fab NOVO", "lot": "LOTE-NOVO", "expiry_date": "2028-01-01"})
    new_dsf = pdf_text(_dsf(client, env).content)
    assert "Glicemia ALTERADA" in new_dsf and "Nome NOVO" in new_dsf and "LOTE-NOVO" in new_dsf

    reprint = client.get(f"{env['base']}/documents/{doc_id}", headers=env["b"])  # any pharmacist may reprint
    assert reprint.status_code == 200 and hashlib.sha256(reprint.content).hexdigest() == digest
    assert 'filename="declaracao-servico-farmaceutico-atendimento-' in reprint.headers["content-disposition"]
    old = pdf_text(reprint.content)
    assert "ALTERADA" not in old and "NOVO" not in old
    assert "LOTE-ALFA-001" in old and "LOTE-BETA-002" in old and "LOTE-GAMA-003" in old
    assert "Colesterol total" in old


def test_dsf_follows_the_pharmacist_establishment_change(client, env):
    client.patch(f"/admin/users/{env['a_id']}", headers=env["admin"], json={"establishment_id": env["est_b"]})
    text = pdf_text(_dsf(client, env).content)
    assert EST_B["name"] in text and EST_A["name"] not in text


# --- regression: the other three document types are unchanged ----------------------------

def test_existing_document_types_still_issue(client, env):
    for kind, payload, title in (
        ("prescription", {"items": [{"medication": "Item"}]}, "Prescrição / Plano de Cuidado"),
        ("exam-request", {"exams": ["Ureia"], "clinical_justification": "J."}, "Solicitação de Exames Laboratoriais"),
        ("referral", {"destination": "D", "case_summary": "R", "prm": "P", "suggested_conduct": "C"},
         "Encaminhamento / Interconsulta"),
    ):
        r = client.post(f"{env['base']}/documents/{kind}", json=payload, headers=env["a"])
        text = pdf_text(r.content)
        assert r.status_code == 200 and title in text
        assert "Fabricante" not in text and DSF_NOTICE not in text
