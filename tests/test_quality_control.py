"""Controle de Qualidade (CIQ / CEQ): establishment-owned records, draft -> finalized, mandatory
non-conformity for rejected controls, one immutable PDF. Fictitious data only."""
import hashlib
import io
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader
from sqlalchemy import inspect, text

from backend.auth import create_pharmacist
from backend.database import get_engine, init_db
from backend.models import ROLE_ADMINISTRATOR, QualityControl

PASSWORD = "senha-segura-1"
BASE = {"control_type": "CIQ", "exam_analyte": "Glicose", "equipment_methodology": "Glicosímetro / fotometria",
        "provider_manufacturer": "Fabricante Ficticio", "control_lot": "LT-QC-01", "control_expiry": "03/2027",
        "value_found": "98,5", "unit": "mg/dL", "target_mean": "100", "standard_deviation": "3,2",
        "acceptable_range_min": "93,6", "acceptable_range_max": "106,4", "evaluation_status": "APPROVED"}
NC = {"nc_root_cause": "CALIBRATION", "nc_corrective_action": "Equipamento recalibrado conforme o fabricante.",
      "nc_repeat_value": "101,2", "nc_repeat_status": "APPROVED", "nc_patient_decision": "APPROVED_FOR_PATIENT_TESTING"}
REJECTED = {**BASE, "control_type": "CEQ", "value_found": "121,0", "evaluation_status": "REJECTED"}
FINALIZED = "Registro finalizado. Os dados deste controle de qualidade são somente para consulta."


def pdf_text(data):
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)


def _headers(client, login):
    token = client.post("/auth/login", data={"username": login, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(client, db):
    create_pharmacist(db, full_name="Admin QC", crf="", login="admin@qc.test", password=PASSWORD, role=ROLE_ADMINISTRATOR)
    admin = _headers(client, "admin@qc.test")
    est_a = client.post("/admin/establishments", headers=admin, json={"name": "Farmácia QC A", "cnpj": "11.222.333/0001-81",
                                                                      "address": "Rua A, 1", "phone": "(21) 1111-0000"}).json()
    est_b = client.post("/admin/establishments", headers=admin, json={"name": "Farmácia QC B"}).json()
    ids = {}
    for name, login, est in (("Farmacêutica A Um", "a1@qc.test", est_a), ("Farmacêutico A Dois", "a2@qc.test", est_a),
                             ("Farmacêutica B", "b1@qc.test", est_b)):
        ids[login] = client.post("/admin/users", headers=admin, json={
            "full_name": name, "crf": "16906", "crf_uf": "RJ", "login": login, "password": PASSWORD,
            "establishment_id": est["id"]}).json()["id"]
    return {"admin": admin, "a1": _headers(client, "a1@qc.test"), "a2": _headers(client, "a2@qc.test"),
            "b1": _headers(client, "b1@qc.test"), "est_a": est_a, "est_b": est_b, "ids": ids}


def _create(client, h, data=BASE):
    r = client.post("/quality-controls", headers=h, json=data)
    assert r.status_code == 201, r.text
    return r.json()


def _finalize(client, h, rid):
    return client.post(f"/quality-controls/{rid}/finalize", headers=h)


# --- database / migration ---------------------------------------------------------------

def _app_schema_and_rows():
    with get_engine().connect() as conn:
        schema = conn.execute(text("SELECT type, name, sql FROM sqlite_master WHERE tbl_name != 'quality_controls' "
                                   "ORDER BY type, name")).fetchall()
        rows = {t: hashlib.sha256(repr(conn.execute(text(f"SELECT * FROM {t} ORDER BY rowid")).fetchall()).encode()).hexdigest()
                for (t,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name != 'quality_controls'"))}
    return schema, rows


def test_migration_adds_the_table_only_and_is_idempotent(client, env):
    QualityControl.__table__.drop(get_engine())  # a database from before this module
    before = _app_schema_and_rows()
    init_db()
    insp = inspect(get_engine())
    assert "quality_controls" in insp.get_table_names()
    assert {i["name"] for i in insp.get_indexes("quality_controls")} >= {"ix_quality_controls_establishment_execution"}
    first = _app_schema_and_rows()
    with get_engine().connect() as conn:
        qc_schema = conn.execute(text("SELECT sql FROM sqlite_master WHERE tbl_name='quality_controls' ORDER BY name")).fetchall()
    init_db()  # second run changes nothing
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT sql FROM sqlite_master WHERE tbl_name='quality_controls' ORDER BY name")).fetchall() == qc_schema
    assert first == before == _app_schema_and_rows()  # existing tables and rows untouched


def test_server_sets_owner_professional_and_times(client, env):
    forged = {**BASE, "pharmacist_id": 999, "establishment_id": env["est_b"]["id"], "execution_at": "2000-01-01T00:00:00",
              "finalized_at": "2000-01-01T00:00:00", "created_at": "2000-01-01T00:00:00",
              "responsible_name": "FORJADO", "responsible_crf": "CRF/XX 1", "nc_event_at": "2000-01-01T00:00:00"}
    t0 = datetime.now(timezone.utc).replace(tzinfo=None)
    record = _create(client, env["a1"], forged)
    t1 = datetime.now(timezone.utc).replace(tzinfo=None)
    assert record["pharmacist_id"] == env["ids"]["a1@qc.test"] and record["establishment_id"] == env["est_a"]["id"]
    assert record["responsible_name"] == "Farmacêutica A Um" and record["responsible_crf"] == "CRF/RJ 16906"
    assert t0 <= datetime.fromisoformat(record["execution_at"]) <= t1
    assert record["finalized_at"] is None and record["nc_event_at"] is None
    done = _finalize(client, env["a1"], record["id"]).json()
    assert t1 <= datetime.fromisoformat(done["finalized_at"]) <= datetime.now(timezone.utc).replace(tzinfo=None)
    assert done["execution_at"] == record["execution_at"]


def test_values_are_kept_as_typed_and_encrypted_at_rest(client, env):
    record = _create(client, env["a1"], {**BASE, "value_found": "1.234,56", "target_mean": "1,2e3"})
    assert record["value_found"] == "1.234,56" and record["target_mean"] == "1,2e3"  # no parsing/rounding
    with get_engine().connect() as conn:
        raw = repr(conn.execute(text("SELECT * FROM quality_controls")).fetchall())
    for plain in ("Glicose", "Glicosímetro", "Fabricante Ficticio", "LT-QC-01", "1.234,56", "mg/dL", "2027-03", "106,4"):
        assert plain not in raw, plain


@pytest.mark.parametrize("given, stored", [("03/2027", "03/2027"), ("3/2027", "03/2027"), (" 12/2030 ", "12/2030"),
                                           ("", "")])
def test_control_expiry_month_year(client, env, given, stored):
    assert _create(client, env["a1"], {**BASE, "control_expiry": given})["control_expiry"] == stored


@pytest.mark.parametrize("bad", ["13/2027", "00/2027", "2027-03", "03/27", "março/2027"])
def test_invalid_control_expiry_rejected(client, env, bad):
    r = client.post("/quality-controls", headers=env["a1"], json={**BASE, "control_expiry": bad})
    assert r.status_code == 422


# --- access control -----------------------------------------------------------------------

def test_unauthenticated_and_administrator(client, env):
    rid = _create(client, env["a1"])["id"]
    calls = [("GET", "/quality-controls", None), ("POST", "/quality-controls", BASE), ("GET", f"/quality-controls/{rid}", None),
             ("PUT", f"/quality-controls/{rid}", BASE), ("POST", f"/quality-controls/{rid}/finalize", None),
             ("GET", f"/quality-controls/{rid}/pdf", None)]
    for method, path, body in calls:
        assert client.request(method, path, json=body).status_code == 401, path
        assert client.request(method, path, headers=env["admin"], json=body).status_code == 403, path


def test_establishment_isolation(client, env):
    a_draft = _create(client, env["a1"])
    a_final = _create(client, env["a1"])
    _finalize(client, env["a1"], a_final["id"])
    b_record = _create(client, env["b1"])
    assert {r["id"] for r in client.get("/quality-controls", headers=env["a1"]).json()} == {a_draft["id"], a_final["id"]}
    assert {r["id"] for r in client.get("/quality-controls", headers=env["b1"]).json()} == {b_record["id"]}
    for rid in (a_draft["id"], a_final["id"]):  # B cannot reach A's records in any way (404: no existence leak)
        assert client.get(f"/quality-controls/{rid}", headers=env["b1"]).status_code == 404
        assert client.put(f"/quality-controls/{rid}", headers=env["b1"], json=BASE).status_code == 404
        assert _finalize(client, env["b1"], rid).status_code == 404
        assert client.get(f"/quality-controls/{rid}/pdf", headers=env["b1"]).status_code == 404
    assert client.get(f"/quality-controls/{b_record['id']}", headers=env["a1"]).status_code == 404
    assert client.get("/quality-controls/999999", headers=env["a1"]).status_code == 404


def test_same_establishment_other_pharmacist(client, env):
    draft = _create(client, env["a1"])
    final = _create(client, env["a1"])
    _finalize(client, env["a1"], final["id"])
    a2 = env["a2"]
    assert client.get(f"/quality-controls/{draft['id']}", headers=a2).status_code == 200  # read-only view
    assert client.put(f"/quality-controls/{draft['id']}", headers=a2, json=BASE).status_code == 403
    assert _finalize(client, a2, draft["id"]).status_code == 403
    assert client.get(f"/quality-controls/{final['id']}", headers=a2).status_code == 200
    pdf_a1 = client.get(f"/quality-controls/{final['id']}/pdf", headers=env["a1"]).content
    assert client.get(f"/quality-controls/{final['id']}/pdf", headers=a2).content == pdf_a1


def test_record_stays_with_its_establishment_when_the_pharmacist_moves(client, env):
    record = _create(client, env["a1"])
    client.patch(f"/admin/users/{env['ids']['a1@qc.test']}", headers=env["admin"], json={"establishment_id": env["est_b"]["id"]})
    assert client.get(f"/quality-controls/{record['id']}", headers=env["a1"]).status_code == 404  # now in B
    assert client.get(f"/quality-controls/{record['id']}", headers=env["a2"]).json()["establishment_id"] == env["est_a"]["id"]
    assert client.get(f"/quality-controls/{record['id']}", headers=env["b1"]).status_code == 404


def test_no_delete_route(client, env):
    rid = _create(client, env["a1"])["id"]
    assert client.delete(f"/quality-controls/{rid}", headers=env["a1"]).status_code == 405


# --- approved flow --------------------------------------------------------------------------

@pytest.mark.parametrize("control_type", ["CIQ", "CEQ"])
def test_approved_flow(client, env, control_type):
    record = _create(client, env["a1"], {**BASE, "control_type": control_type, "value_found": ""})
    r = client.put(f"/quality-controls/{record['id']}", headers=env["a1"], json={**BASE, "control_type": control_type})
    assert r.status_code == 200 and r.json()["value_found"] == "98,5"
    assert client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a1"]).status_code == 409  # not final yet
    done = _finalize(client, env["a1"], record["id"])
    assert done.status_code == 200 and done.json()["finalized_at"]
    pdf = client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a1"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    t = pdf_text(pdf.content)
    for value in ("REGISTRO DE CONTROLE DE QUALIDADE (CIQ / CEQ)", "RDC 978/2025", f"Tipo de controle: {control_type}",
                  "Exame / Analito: Glicose", "Provedor / Fabricante: Fabricante Ficticio", "Lote do controle: LT-QC-01",
                  "Data de validade: 03/2027", "Valor encontrado: 98,5", "Unidade de medida: mg/dL", "Média alvo: 100",
                  "Desvio padrão: 3,2", "Faixa aceitável: 93,6 a 106,4", "APROVADO / ADEQUADO", "Farmacêutica A Um",
                  "CRF/RJ 16906", "Farmácia QC A", "CNPJ 11.222.333/0001-81", "Assinatura e carimbo"):
        assert value in t, value
    assert "NÃO CONFORMIDADE" not in t
    for claim in ("100%", "certificad", "conformidade total", "totalmente conforme"):
        assert claim not in t.lower()
    assert client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a1"]).content == pdf.content  # exact bytes
    r = client.put(f"/quality-controls/{record['id']}", headers=env["a1"], json={**BASE, "value_found": "1"})
    assert r.status_code == 409 and r.json()["detail"] == FINALIZED
    assert _finalize(client, env["a1"], record["id"]).status_code == 409


def test_approved_record_drops_draft_non_conformity_data(client, env):
    record = _create(client, env["a1"], {**BASE, **NC})
    done = _finalize(client, env["a1"], record["id"]).json()
    assert all(done[k] is None for k in ("nc_root_cause", "nc_corrective_action", "nc_repeat_value", "nc_repeat_status",
                                          "nc_patient_decision", "nc_event_at"))


def test_incomplete_record_cannot_be_finalized(client, env):
    record = _create(client, env["a1"], {"control_type": "CIQ", "exam_analyte": "Glicose"})
    r = _finalize(client, env["a1"], record["id"])
    assert r.status_code == 422
    for label in ("Equipamento / Metodologia", "Lote do controle", "Validade do controle", "Valor encontrado",
                  "Média alvo", "Desvio padrão", "Faixa aceitável (mínimo)", "Status da avaliação"):
        assert label in r.json()["detail"]
    assert client.get(f"/quality-controls/{record['id']}", headers=env["a1"]).json()["finalized_at"] is None


# --- rejected flow ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing, label", [
    ("nc_root_cause", "Causa raiz"), ("nc_corrective_action", "Ação corretiva adotada"),
    ("nc_repeat_value", "Novo valor obtido"), ("nc_repeat_status", "Status do novo teste"),
    ("nc_patient_decision", "Decisão quanto às amostras dos pacientes"),
])
def test_rejected_needs_complete_non_conformity(client, env, missing, label):
    nc = {**NC, missing: None if missing in ("nc_root_cause", "nc_repeat_status", "nc_patient_decision") else ""}
    record = _create(client, env["a1"], {**REJECTED, **nc})
    r = _finalize(client, env["a1"], record["id"])
    assert r.status_code == 422 and label in r.json()["detail"]
    assert client.get(f"/quality-controls/{record['id']}", headers=env["a1"]).json()["finalized_at"] is None


def test_rejected_other_cause_needs_description(client, env):
    record = _create(client, env["a1"], {**REJECTED, **NC, "nc_root_cause": "OTHER"})
    r = _finalize(client, env["a1"], record["id"])
    assert r.status_code == 422 and "Descrição da causa raiz (Outro)" in r.json()["detail"]
    client.put(f"/quality-controls/{record['id']}", headers=env["a1"],
               json={**REJECTED, **NC, "nc_root_cause": "OTHER", "nc_root_cause_other": "Frasco aberto há 40 dias."})
    assert _finalize(client, env["a1"], record["id"]).status_code == 200


def test_rejected_flow_one_pdf_with_both_sections(client, env):
    t0 = datetime.now(timezone.utc).replace(tzinfo=None)
    record = _create(client, env["a1"], REJECTED)
    assert record["nc_event_at"] is not None and datetime.fromisoformat(record["nc_event_at"]) >= t0  # server-generated
    record = client.put(f"/quality-controls/{record['id']}", headers=env["a1"], json={**REJECTED, **NC}).json()
    done = _finalize(client, env["a1"], record["id"]).json()
    assert done["finalized_at"] and done["nc_event_at"] == record["nc_event_at"]
    pdf = client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a1"]).content
    t = pdf_text(pdf)
    for value in ("REGISTRO DE CONTROLE DE QUALIDADE (CIQ / CEQ)", "Tipo de controle: CEQ", "REJEITADO / INADEQUADO",
                  "REGISTRO DE NÃO CONFORMIDADE E AÇÃO CORRETIVA", "Exame afetado: Glicose", "121,0 mg/dL",
                  "Falha de calibração do equipamento/dispositivo", "Equipamento recalibrado conforme o fabricante.",
                  "Novo valor obtido: 101,2", "Status do novo teste: Aprovado",
                  "Aprovado para realização e liberação dos exames dos pacientes", "RESPONSÁVEL TÉCNICO",
                  "Farmacêutica A Um", "CRF/RJ 16906"):
        assert value in t, value
    title = "REGISTRO DE CONTROLE DE QUALIDADE (CIQ / CEQ)"
    pages = len(PdfReader(io.BytesIO(pdf)).pages)
    assert t.count(title) == pages  # one document: its heading + the continuation header of each further page
    assert client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a2"]).content == pdf
    assert client.put(f"/quality-controls/{record['id']}", headers=env["a1"], json={**REJECTED, **NC}).status_code == 409


def test_blocked_decision_and_rejected_repeat_render(client, env):
    record = _create(client, env["a1"], {**REJECTED, **NC, "nc_repeat_status": "REJECTED",
                                         "nc_patient_decision": "BLOCKED_UNTIL_TECHNICAL_MAINTENANCE", "nc_root_cause": "EXPIRED"})
    _finalize(client, env["a1"], record["id"])
    t = pdf_text(client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a1"]).content)
    assert "Bloqueado até manutenção técnica" in t and "Status do novo teste: Rejeitado" in t
    assert "Validade do reagente/controle expirada" in t


def test_status_back_to_approved_clears_event(client, env):
    record = _create(client, env["a1"], REJECTED)
    assert record["nc_event_at"]
    assert client.put(f"/quality-controls/{record['id']}", headers=env["a1"], json=BASE).json()["nc_event_at"] is None


# --- document integrity ------------------------------------------------------------------------

def test_old_pdf_unchanged_after_establishment_and_profile_changes(client, env):
    record = _create(client, env["a1"])
    _finalize(client, env["a1"], record["id"])
    pdf = client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a1"]).content
    client.patch(f"/admin/establishments/{env['est_a']['id']}", headers=env["admin"], json={"name": "Nome NOVO"})
    client.patch(f"/admin/users/{env['ids']['a1@qc.test']}", headers=env["admin"], json={"full_name": "Nome Farm NOVO"})
    again = client.get(f"/quality-controls/{record['id']}/pdf", headers=env["a2"]).content
    assert again == pdf and "NOVO" not in pdf_text(again)
    assert client.get(f"/quality-controls/{record['id']}", headers=env["a2"]).json()["responsible_name"] == "Farmacêutica A Um"


def test_quality_control_does_not_touch_clinical_documents(client, env):
    pid = client.post("/patients", headers=env["a1"], json={"full_name": "Paciente QC", "date_of_birth": "1970-01-01",
                                                            "cpf": "52998224725"}).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", headers=env["a1"], json={}).json()["id"]
    base = f"/patients/{pid}/consultations/{cid}"
    issued = client.post(f"{base}/documents/prescription", headers=env["a1"], json={"items": [{"medication": "Item"}]}).content
    doc_id = client.get(base, headers=env["a1"]).json()["documents"][0]["id"]
    record = _create(client, env["a1"])
    _finalize(client, env["a1"], record["id"])
    assert client.get(f"{base}/documents/{doc_id}", headers=env["a1"]).content == issued
    assert len(client.get(base, headers=env["a1"]).json()["documents"]) == 1  # QC PDFs are not patient documents
