"""Declaração de Serviço Farmacêutico (DSF): built from the consultation's recorded exam
results plus test identification entered at issue. Fictitious data only."""
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
DSF = {"manufacturer": "Fabricante Fictício Diagnósticos S.A.", "lot": "LT-2026-0042", "expiry_date": "2027-03-31"}


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
    for exam in EXAMS:
        assert client.post(f"/patients/{pid}/consultations/{cid}/exams", json=exam, headers=a).status_code == 201
    return {"admin": admin, "a": a, "b": b, "pid": pid, "cid": cid, "est_a": est_a, "est_b": est_b,
            "a_id": ids["a@dsf.test"], "base": f"/patients/{pid}/consultations/{cid}"}


def _dsf(client, env, payload=None, headers=None, base=None):
    return client.post(f"{base or env['base']}/documents/dsf", json=DSF if payload is None else payload,
                       headers=env["a"] if headers is None else headers)


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


# --- data source -----------------------------------------------------------------

def test_dsf_uses_the_consultation_results_patient_pharmacist_and_establishment(client, env):
    forged = {**DSF,
              "exam_results": [{"exam_name": "EXAME FORJADO", "result": "999"}],
              "exams": ["EXAME FORJADO"],
              "establishment_id": env["est_b"], "establishment": EST_B, "establishment_name": EST_B["name"],
              "patient_id": 999, "patient_name": "PACIENTE FORJADO",
              "pharmacist_id": 999, "pharmacist_name": "FARMACÊUTICO FORJADO", "crf": "CRF/XX 99999"}
    text = pdf_text(_dsf(client, env, forged).content)
    for exam in EXAMS:
        assert exam["exam_name"][:40] in text
    assert "FORJADO" not in text
    assert PATIENT["full_name"] in text and "CPF: 529.982.247-25" in text and "Data de nascimento: 20/06/1975" in text
    assert "Farmacêutica Alfa DSF" in text and "CRF/RJ 10001" in text
    for value in (EST_A["name"], f"CNPJ {EST_A['cnpj']}", EST_A["address"], f"Tel. {EST_A['phone']}"):
        assert value in text
    for value in (EST_B["name"], EST_B["cnpj"], EST_B["address"], "CRF/XX"):
        assert value not in text


def test_dsf_does_not_create_or_change_exam_records(client, env):
    before = client.get(env["base"], headers=env["a"]).json()["exam_results"]
    _dsf(client, env)
    assert client.get(env["base"], headers=env["a"]).json()["exam_results"] == before


# --- DSF fields --------------------------------------------------------------------

def test_dsf_fields_dates_notice_and_signature(client, env):
    text = pdf_text(_dsf(client, env).content)
    assert "Declaração de Serviço Farmacêutico" in text
    assert "Fabricante do teste: Fabricante Fictício Diagnósticos S.A." in text
    assert "Número do lote: LT-2026-0042" in text
    assert "Data de validade: 31/03/2027" in text
    assert "Data da realização: 12/01/2026" in text  # the consultation date
    assert DSF_NOTICE in text
    assert DSF_NOTICE == (
        "Este exame possui finalidade de triagem e rastreamento, não sendo destinado à confirmação diagnóstica. "
        "Os resultados devem ser avaliados por profissional legalmente habilitado e, quando necessário, "
        "complementados por exames laboratoriais confirmatórios.")
    assert f"Data: {date.today():%d/%m/%Y}" in text
    assert not re.search(r"[A-Za-zÀ-ú]+ - [A-Z]{2}, \d{2}/\d{2}/\d{4}", text)
    assert not re.search(r"\d{1,2} de [a-zç]+ de \d{4}", text)
    assert "Assinatura e carimbo" in text and "Farmacêutico(a) · CRF/RJ 10001" in text
    assert "Serviço farmacêutico realizado" not in text and "serviço farmacêutico realizado" not in text.lower()


@pytest.mark.parametrize("payload, detail", [
    ({**DSF, "manufacturer": "   "}, "fabricante"),
    ({**DSF, "lot": ""}, "lote"),
])
def test_required_test_identification(client, env, payload, detail):
    r = _dsf(client, env, payload)
    assert r.status_code == 422 and detail in r.json()["detail"]


@pytest.mark.parametrize("payload", [
    {**DSF, "expiry_date": "31/02/2027"}, {**DSF, "expiry_date": "2027-02-31"}, {**DSF, "expiry_date": ""},
    {k: v for k, v in DSF.items() if k != "expiry_date"}, {k: v for k, v in DSF.items() if k != "lot"},
    {**DSF, "manufacturer": "x" * 201},
])
def test_invalid_dsf_input_rejected(client, env, payload):
    assert _dsf(client, env, payload).status_code == 422


# --- exam behaviour ------------------------------------------------------------------

def test_multiple_results_render_with_unit_reference_and_notes(client, env):
    data = _dsf(client, env).content
    text = pdf_text(data)
    for value in ("Exame", "Resultado", "Unidade", "Valor de referência", "Glicemia capilar", "98", "mg/dL",
                  "70 - 99", "Observações: Jejum de 8 horas.", "5,9", "< 5,7", "Colesterol total", "182", "< 190"):
        assert value in text, value
    assert "Observações: Observação longa: resultado sujeito a confirmação laboratorial;" in text
    assert text.count("resultado sujeito a confirmação laboratorial") == 25  # long note wrapped, nothing lost
    long_exam_words = " ".join(text.split())
    assert all(word in long_exam_words for word in LONG_EXAM.split())


def test_many_results_span_pages_with_numbering(client, env):
    base = env["base"]
    for i in range(30):
        client.post(f"{base}/exams", json={"exam_name": f"Teste rápido {i + 1}", "result": "Não reagente",
                                           "reference_range": "Não reagente", "notes": "Sem intercorrências."},
                    headers=env["a"])
    data = _dsf(client, env).content
    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    assert pages >= 2
    text = pdf_text(data)
    assert all(f"Teste rápido {i + 1} " in text for i in range(30))
    assert f"Página {pages} de {pages}" in text
    assert "(continuação)" in text
    last = " ".join((reader.pages[-1].extract_text() or "").split())
    assert "Assinatura e carimbo" in last
    assert DSF_NOTICE in text  # notice kept whole


def test_consultation_without_exam_results_is_rejected(client, env):
    cid = client.post(f"/patients/{env['pid']}/consultations", json={}, headers=env["a"]).json()["id"]
    base = f"/patients/{env['pid']}/consultations/{cid}"
    r = _dsf(client, env, base=base)
    assert r.status_code == 422
    assert "Registre os resultados de exame" in r.json()["detail"]
    assert client.get(base, headers=env["a"]).json()["documents"] == []


# --- history and reprint ---------------------------------------------------------------

def test_dsf_history_and_byte_identical_reprint_after_source_changes(client, env, db):
    issued = _dsf(client, env).content
    docs = client.get(env["base"], headers=env["a"]).json()["documents"]
    assert [d["document_type"] for d in docs] == ["dsf"] and docs[0]["reprintable"]
    doc_id = docs[0]["id"]
    digest = hashlib.sha256(issued).hexdigest()

    # change every source: exam results, patient, pharmacist, establishment
    exams = client.get(env["base"], headers=env["a"]).json()["exam_results"]
    client.put(f"{env['base']}/exams/{exams[0]['id']}", headers=env["a"],
               json={"exam_name": "Glicemia ALTERADA", "result": "250", "unit": "mg/dL"})
    client.delete(f"{env['base']}/exams/{exams[2]['id']}", headers=env["a"])
    client.patch(f"/admin/establishments/{env['est_a']}", headers=env["admin"], json={"name": "Nome NOVO"})
    client.patch(f"/admin/users/{env['a_id']}", headers=env["admin"], json={"full_name": "Nome Farm NOVO"})
    new_dsf = pdf_text(_dsf(client, env, {**DSF, "lot": "LOTE-NOVO"}).content)
    assert "Glicemia ALTERADA" in new_dsf and "Nome NOVO" in new_dsf and "LOTE-NOVO" in new_dsf

    reprint = client.get(f"{env['base']}/documents/{doc_id}", headers=env["b"])  # any pharmacist may reprint
    assert reprint.status_code == 200
    assert hashlib.sha256(reprint.content).hexdigest() == digest
    assert 'filename="declaracao-servico-farmaceutico-atendimento-' in reprint.headers["content-disposition"]
    old = pdf_text(reprint.content)
    assert "ALTERADA" not in old and "Nome NOVO" not in old and "LT-2026-0042" in old and "Colesterol total" in old


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
        assert r.status_code == 200 and title in pdf_text(r.content)
        assert "Fabricante do teste" not in pdf_text(r.content)
