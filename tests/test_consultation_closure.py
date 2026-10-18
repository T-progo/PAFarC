"""Closed consultations are read-only: starting a new consultation for a patient closes the
previous one, after which its SOAP and exam results can no longer be changed (server-side).
Fictitious data only."""
import hashlib

import pytest
from sqlalchemy import text

from backend.auth import create_pharmacist
from backend.consultations import close_superseded_consultations
from backend.models import ROLE_ADMINISTRATOR

PASSWORD = "senha-segura-1"
SOAP = {"subjective": "Queixa inicial.", "objective": "PA 120x80.", "assessment": "PRM de adesão.",
        "plan": "Reavaliar em 30 dias."}
EXAM = {"exam_name": "Glicemia", "result": "98", "unit": "mg/dL", "reference_range": "70 - 99", "notes": "Jejum.",
        "manufacturer": "Fabricante A", "lot": "LOTE-001", "expiry_date": "2027-12-31"}
CLOSED_DETAIL = "Atendimento encerrado. Os dados clínicos deste atendimento são somente para consulta."


def _headers(client, login):
    token = client.post("/auth/login", data={"username": login, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(client, db):
    create_pharmacist(db, full_name="Admin Fecha", crf="", login="admin@fecha.test", password=PASSWORD,
                      role=ROLE_ADMINISTRATOR)
    for name, login in (("Farmacêutica Fecha A", "a@fecha.test"), ("Farmacêutico Fecha B", "b@fecha.test")):
        create_pharmacist(db, full_name=name, crf="10001", crf_uf="RJ", login=login, password=PASSWORD)
    a = _headers(client, "a@fecha.test")
    pid = client.post("/patients", headers=a, json={"full_name": "Paciente Fecha Ficticio",
                                                    "date_of_birth": "1970-01-01", "cpf": "52998224725"}).json()["id"]
    return {"a": a, "b": _headers(client, "b@fecha.test"), "admin": _headers(client, "admin@fecha.test"), "pid": pid}


def _new(client, env, headers=None):
    r = client.post(f"/patients/{env['pid']}/consultations", headers=headers or env["a"], json={})
    assert r.status_code == 201
    return f"/patients/{env['pid']}/consultations/{r.json()['id']}"


def _filled(client, env):
    """Consultation 1 with SOAP and one exam (with kit), while it is open."""
    base = _new(client, env)
    assert client.put(f"{base}/soap", headers=env["a"], json=SOAP).status_code == 200
    exam = client.post(f"{base}/exams", headers=env["a"], json=EXAM).json()
    return base, exam


def _snapshot(db):
    rows = db.execute(text("select * from soap_records order by id")).all() + \
        db.execute(text("select * from exam_results order by id")).all()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


# --- lifecycle -----------------------------------------------------------------------------

def test_new_consultation_is_open_and_fully_editable(client, env):
    base, exam = _filled(client, env)
    body = client.get(base, headers=env["a"]).json()
    assert body["closed_at"] is None
    assert client.put(f"{base}/soap", headers=env["a"], json={**SOAP, "plan": "Plano revisado."}).status_code == 200
    r = client.put(f"{base}/exams/{exam['id']}", headers=env["a"], json={**EXAM, "result": "99", "lot": "LOTE-009"})
    assert r.status_code == 200 and r.json()["lot"] == "LOTE-009"
    extra = client.post(f"{base}/exams", headers=env["a"], json={"exam_name": "Ureia", "result": "30"}).json()
    assert client.delete(f"{base}/exams/{extra['id']}", headers=env["a"]).status_code == 204


def test_starting_a_new_consultation_closes_the_previous_one(client, env):
    c1, _ = _filled(client, env)
    c2 = _new(client, env)
    assert client.get(c1, headers=env["a"]).json()["closed_at"] is not None
    assert client.get(c2, headers=env["a"]).json()["closed_at"] is None
    listing = client.get(f"/patients/{env['pid']}/consultations", headers=env["a"]).json()
    assert [c["closed_at"] is None for c in listing] == [True, False]  # newest first: only it is open


def test_new_consultation_by_another_pharmacist_also_closes_the_previous_one(client, env):
    c1, _ = _filled(client, env)
    _new(client, env, headers=env["b"])
    assert client.get(c1, headers=env["a"]).json()["closed_at"] is not None
    assert client.put(f"{c1}/soap", headers=env["a"], json=SOAP).status_code == 409


def test_consultations_of_other_patients_are_not_closed(client, env):
    c1, _ = _filled(client, env)
    other = client.post("/patients", headers=env["a"], json={"full_name": "Outro Paciente", "date_of_birth": "1980-01-01",
                                                             "cpf": "11144477735"}).json()["id"]
    client.post(f"/patients/{other}/consultations", headers=env["a"], json={})
    assert client.get(c1, headers=env["a"]).json()["closed_at"] is None


def test_a_closed_consultation_never_reopens(client, env):
    c1, _ = _filled(client, env)
    _new(client, env)
    closed_at = client.get(c1, headers=env["a"]).json()["closed_at"]
    for method in ("put", "patch"):  # no route changes a consultation's status
        assert getattr(client, method)(c1, headers=env["a"], json={"closed_at": None}).status_code == 405
    client.post(f"/patients/{env['pid']}/consultations", headers=env["a"], json={"closed_at": None})
    client.put(f"{c1}/soap", headers=env["a"], json=SOAP)
    assert client.get(c1, headers=env["a"]).json()["closed_at"] == closed_at


# --- the client's scenario and every mutation of a closed consultation -------------------------

def test_client_scenario_exam_cannot_be_deleted_after_a_new_consultation(client, env, db):
    c1, exam = _filled(client, env)
    _new(client, env)
    before = _snapshot(db)
    r = client.delete(f"{c1}/exams/{exam['id']}", headers=env["a"])
    assert r.status_code == 409 and r.json()["detail"] == CLOSED_DETAIL
    assert _snapshot(db) == before
    assert [e["id"] for e in client.get(c1, headers=env["a"]).json()["exam_results"]] == [exam["id"]]


@pytest.mark.parametrize("field", ["subjective", "objective", "assessment", "plan"])
def test_closed_soap_section_cannot_change(client, env, db, field):
    c1, _ = _filled(client, env)
    _new(client, env)
    before = _snapshot(db)
    r = client.put(f"{c1}/soap", headers=env["a"], json={**SOAP, field: "ALTERADO"})
    assert r.status_code == 409 and r.json()["detail"] == CLOSED_DETAIL
    assert client.get(c1, headers=env["a"]).json()["soap"] == SOAP
    assert _snapshot(db) == before


def test_closed_exam_cannot_be_added_or_edited(client, env, db):
    c1, exam = _filled(client, env)
    _new(client, env)
    before = _snapshot(db)
    assert client.post(f"{c1}/exams", headers=env["a"], json={"exam_name": "Ureia", "result": "30"}).status_code == 409
    assert client.put(f"{c1}/exams/{exam['id']}", headers=env["a"], json={**EXAM, "result": "250"}).status_code == 409
    assert _snapshot(db) == before
    assert client.get(c1, headers=env["a"]).json()["exam_results"][0]["result"] == "98"


@pytest.mark.parametrize("change", [{"manufacturer": "Outro"}, {"lot": "LOTE-X"}, {"expiry_date": "2030-01-01"},
                                    {"manufacturer": None, "lot": None, "expiry_date": None}])
def test_closed_exam_kit_cannot_change(client, env, db, change):
    c1, exam = _filled(client, env)
    _new(client, env)
    before = _snapshot(db)
    assert client.put(f"{c1}/exams/{exam['id']}", headers=env["a"], json={**EXAM, **change}).status_code == 409
    saved = client.get(c1, headers=env["a"]).json()["exam_results"][0]
    assert (saved["manufacturer"], saved["lot"], saved["expiry_date"]) == ("Fabricante A", "LOTE-001", "2027-12-31")
    assert _snapshot(db) == before


# --- reading stays available ---------------------------------------------------------------------

def test_closed_consultation_stays_readable_and_documents_reprint(client, env):
    c1, _ = _filled(client, env)
    issued = client.post(f"{c1}/documents/prescription", headers=env["a"], json={"items": [{"medication": "Item"}]})
    doc_id = client.get(c1, headers=env["a"]).json()["documents"][0]["id"]
    _new(client, env)
    for headers in (env["a"], env["b"]):
        body = client.get(c1, headers=headers).json()
        assert body["soap"] == SOAP and body["exam_results"][0]["lot"] == "LOTE-001"
        reprint = client.get(f"{c1}/documents/{doc_id}", headers=headers)
        assert reprint.status_code == 200 and reprint.content == issued.content
    c2 = f"/patients/{env['pid']}/consultations/" + str(client.get(
        f"/patients/{env['pid']}/consultations", headers=env["a"]).json()[0]["id"])
    previous = client.get(f"{c2}/previous-exams", headers=env["a"]).json()
    assert previous[0]["exam_results"][0]["exam_name"] == "Glicemia"
    assert client.post("/patients/search", headers=env["a"], json={"query": "Paciente Fecha"}).json()[0]["id"] == env["pid"]


def test_documents_can_still_be_issued_from_a_closed_consultation(client, env):
    """Issuing documents is unchanged (it does not alter clinical content); the DSF uses saved data."""
    c1, _ = _filled(client, env)
    _new(client, env)
    for kind, payload in (("prescription", {"items": [{"medication": "Item"}]}),
                          ("exam-request", {"exams": ["Ureia"], "clinical_justification": "J."}),
                          ("referral", {"destination": "D", "case_summary": "R", "prm": "P", "suggested_conduct": "C"}),
                          ("dsf", {})):
        assert client.post(f"{c1}/documents/{kind}", headers=env["a"], json=payload).status_code == 200, kind


def test_kit_suggestion_still_uses_closed_consultations(client, env):
    _filled(client, env)
    _new(client, env)
    r = client.post("/exam-kit-suggestion", headers=env["a"], json={"exam_name": "glicemia"})
    assert r.json()["lot"] == "LOTE-001"


# --- roles and order of checks ---------------------------------------------------------------------

def test_roles_on_closed_consultation(client, env):
    c1, exam = _filled(client, env)
    _new(client, env)
    calls = [("put", f"{c1}/soap", SOAP), ("post", f"{c1}/exams", EXAM),
             ("put", f"{c1}/exams/{exam['id']}", EXAM), ("delete", f"{c1}/exams/{exam['id']}", None)]
    for method, url, body in calls:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(url, headers=env["admin"], **kwargs).status_code == 403
        assert getattr(client, method)(url, headers=env["b"], **kwargs).status_code == 403  # not the owner
        assert getattr(client, method)(url, **kwargs).status_code == 401
        assert getattr(client, method)(url, headers=env["a"], **kwargs).status_code == 409  # owner: closed
    assert client.get(c1, headers=env["admin"]).status_code == 403


# --- migration of existing consultations ---------------------------------------------------------

def test_startup_closes_only_superseded_consultations_and_is_idempotent(client, env, db):
    c1, _ = _filled(client, env)
    c2 = _new(client, env)
    c3 = _new(client, env)
    other = client.post("/patients", headers=env["a"], json={"full_name": "Outro Paciente", "date_of_birth": "1980-01-01",
                                                             "cpf": "11144477735"}).json()["id"]
    lone = client.post(f"/patients/{other}/consultations", headers=env["a"], json={}).json()["id"]
    db.execute(text("update consultations set closed_at = NULL"))  # as before this release
    db.commit()
    before_rows = db.execute(text("select id, patient_id, pharmacist_id, consultation_date, created_at, updated_at "
                                  "from consultations order by id")).all()
    before_clinical = _snapshot(db)

    assert close_superseded_consultations(db) == 2
    assert close_superseded_consultations(db) == 0  # idempotent
    ids = {url: int(url.rsplit("/", 1)[1]) for url in (c1, c2, c3)}
    state = dict(db.execute(text("select id, closed_at from consultations")).all())
    created = dict(db.execute(text("select id, created_at from consultations")).all())
    assert state[ids[c3]] is None and state[lone] is None  # each patient's latest stays open
    assert state[ids[c1]] == created[ids[c2]] and state[ids[c2]] == created[ids[c3]]  # closed when the next began
    assert db.execute(text("select id, patient_id, pharmacist_id, consultation_date, created_at, updated_at "
                           "from consultations order by id")).all() == before_rows  # status only
    assert _snapshot(db) == before_clinical
    assert client.put(f"{c1}/soap", headers=env["a"], json=SOAP).status_code == 409
    assert client.put(f"{c3}/soap", headers=env["a"], json=SOAP).status_code == 200
