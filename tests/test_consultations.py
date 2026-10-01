from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.auth import create_pharmacist
from backend.database import get_engine

# Fictitious data only.
PATIENT = {"full_name": "Paciente Fictício Teste", "date_of_birth": "1970-01-15", "cpf": "12345678909"}
OTHER_PATIENT = {"full_name": "Outro Paciente Teste", "date_of_birth": "1985-06-30", "cpf": "98765432100"}
SOAP_A = {
    "subjective": "Relata adesão irregular à TARV e náuseas matinais.",
    "objective": "PA 128/82 mmHg. Peso 71 kg.",
    "assessment": "Falha virológica provável por baixa adesão.",
    "plan": "Reforçar adesão; repetir carga viral em 30 dias.",
}
SOAP_B = {
    "subjective": "Sem queixas novas desde o último atendimento.",
    "objective": "PA 120/80 mmHg.",
    "assessment": "Função renal estável.",
    "plan": "Manter conduta e reavaliar em 90 dias.",
}
EXAMS = [
    {"exam_name": "Carga Viral HIV-1 RNA", "result": "15400", "unit": "cópias/mL",
     "reference_range": "Indetectável (< 40)", "notes": "Coleta em jejum"},
    {"exam_name": "Linfócitos T-CD4+", "result": "312", "unit": "células/mm³",
     "reference_range": "500 a 1500", "notes": ""},
    {"exam_name": "Creatinina sérica", "result": "1,32", "unit": "mg/dL",
     "reference_range": "0,70 a 1,30", "notes": ""},
]


def _login_headers(client, db, login, name, crf):
    create_pharmacist(db, full_name=name, crf=crf, login=login, password="pw-123456")
    token = client.post("/auth/login", data={"username": login, "password": "pw-123456"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers_a(client, db):
    return _login_headers(client, db, "farm.a", "Farmacêutica A", "CRF-SP 11111")


@pytest.fixture
def headers_b(client, db):
    return _login_headers(client, db, "farm.b", "Farmacêutico B", "CRF-RJ 22222")


@pytest.fixture
def patient_id(client, headers_a):
    return client.post("/patients", json=PATIENT, headers=headers_a).json()["id"]


def _new(client, headers, pid, consultation_date=None):
    body = {"consultation_date": consultation_date} if consultation_date else {}
    return client.post(f"/patients/{pid}/consultations", json=body, headers=headers)


def _url(pid, cid, suffix=""):
    return f"/patients/{pid}/consultations/{cid}{suffix}"


# --- consultations -------------------------------------------------------------

def test_create_consultation_records_authenticated_pharmacist(client, headers_a, patient_id):
    response = _new(client, headers_a, patient_id)
    assert response.status_code == 201
    body = response.json()
    assert body["patient_id"] == patient_id
    assert body["consultation_date"] == date.today().isoformat()
    assert body["pharmacist"]["full_name"] == "Farmacêutica A"
    assert body["pharmacist"]["crf"] == "CRF-SP 11111"
    assert body["soap"] is None and body["exam_results"] == []


def test_client_supplied_pharmacist_id_is_ignored(client, headers_a, headers_b, patient_id):
    me_b = client.get("/auth/me", headers=headers_b).json()
    response = client.post(
        f"/patients/{patient_id}/consultations",
        json={"pharmacist_id": me_b["id"]},
        headers=headers_a,
    )
    assert response.json()["pharmacist"]["full_name"] == "Farmacêutica A"


def test_consultation_routes_require_authentication(client, headers_a, patient_id):
    cid = _new(client, headers_a, patient_id).json()["id"]
    assert _new(client, {}, patient_id).status_code == 401
    assert client.get(f"/patients/{patient_id}/consultations").status_code == 401
    assert client.get(_url(patient_id, cid)).status_code == 401
    assert client.put(_url(patient_id, cid, "/soap"), json=SOAP_A).status_code == 401
    assert client.post(_url(patient_id, cid, "/exams"), json=EXAMS[0]).status_code == 401


def test_nonexistent_patient_is_rejected(client, headers_a):
    assert _new(client, headers_a, 9999).status_code == 404
    assert client.get("/patients/9999/consultations", headers=headers_a).status_code == 404


@pytest.mark.parametrize("bad_date", ["2999-01-01", "1960-01-01", "15/01/2024"])
def test_invalid_consultation_date_is_rejected(client, headers_a, patient_id, bad_date):
    assert _new(client, headers_a, patient_id, bad_date).status_code == 422


def test_consultation_not_reachable_through_other_patient(client, headers_a, patient_id):
    other_id = client.post("/patients", json=OTHER_PATIENT, headers=headers_a).json()["id"]
    cid = _new(client, headers_a, patient_id).json()["id"]
    assert client.get(_url(other_id, cid), headers=headers_a).status_code == 404
    assert client.put(_url(other_id, cid, "/soap"), json=SOAP_A, headers=headers_a).status_code == 404
    assert client.get(f"/patients/{other_id}/consultations", headers=headers_a).json() == []


# --- SOAP ----------------------------------------------------------------------

def test_soap_saves_and_reopens(client, headers_a, patient_id):
    cid = _new(client, headers_a, patient_id).json()["id"]
    response = client.put(_url(patient_id, cid, "/soap"), json=SOAP_A, headers=headers_a)
    assert response.status_code == 200
    assert response.json() == SOAP_A
    reopened = client.get(_url(patient_id, cid), headers=headers_a).json()
    assert reopened["soap"] == SOAP_A


def test_soap_update_does_not_affect_other_consultation(client, headers_a, patient_id):
    c1 = _new(client, headers_a, patient_id).json()["id"]
    c2 = _new(client, headers_a, patient_id).json()["id"]
    client.put(_url(patient_id, c1, "/soap"), json=SOAP_A, headers=headers_a)
    client.put(_url(patient_id, c2, "/soap"), json=SOAP_B, headers=headers_a)
    updated = {**SOAP_A, "plan": "Plano revisado."}
    client.put(_url(patient_id, c1, "/soap"), json=updated, headers=headers_a)

    assert client.get(_url(patient_id, c1), headers=headers_a).json()["soap"] == updated
    assert client.get(_url(patient_id, c2), headers=headers_a).json()["soap"] == SOAP_B


def test_only_responsible_pharmacist_can_edit(client, headers_a, headers_b, patient_id):
    cid = _new(client, headers_a, patient_id).json()["id"]
    client.put(_url(patient_id, cid, "/soap"), json=SOAP_A, headers=headers_a)
    exam_id = client.post(_url(patient_id, cid, "/exams"), json=EXAMS[0], headers=headers_a).json()["id"]

    assert client.put(_url(patient_id, cid, "/soap"), json=SOAP_B, headers=headers_b).status_code == 403
    assert client.post(_url(patient_id, cid, "/exams"), json=EXAMS[1], headers=headers_b).status_code == 403
    assert client.delete(_url(patient_id, cid, f"/exams/{exam_id}"), headers=headers_b).status_code == 403
    # ...but can read it.
    body = client.get(_url(patient_id, cid), headers=headers_b).json()
    assert body["soap"] == SOAP_A and len(body["exam_results"]) == 1


# --- exam results --------------------------------------------------------------

def test_multiple_exam_results_belong_to_their_consultation(client, headers_a, patient_id):
    c1 = _new(client, headers_a, patient_id).json()["id"]
    c2 = _new(client, headers_a, patient_id).json()["id"]
    for exam in EXAMS:
        response = client.post(_url(patient_id, c1, "/exams"), json=exam, headers=headers_a)
        assert response.status_code == 201
        assert response.json()["consultation_id"] == c1
    client.post(_url(patient_id, c2, "/exams"), json={"exam_name": "Ureia", "result": "38", "unit": "mg/dL"}, headers=headers_a)

    exams_1 = client.get(_url(patient_id, c1), headers=headers_a).json()["exam_results"]
    exams_2 = client.get(_url(patient_id, c2), headers=headers_a).json()["exam_results"]
    assert [e["exam_name"] for e in exams_1] == [e["exam_name"] for e in EXAMS]
    assert exams_1[0]["result"] == "15400" and exams_1[0]["unit"] == "cópias/mL"
    assert [e["exam_name"] for e in exams_2] == ["Ureia"]


def test_exam_result_requires_name_and_result(client, headers_a, patient_id):
    cid = _new(client, headers_a, patient_id).json()["id"]
    for payload in ({"exam_name": "  ", "result": "1"}, {"exam_name": "ALT / TGP", "result": ""}):
        assert client.post(_url(patient_id, cid, "/exams"), json=payload, headers=headers_a).status_code == 422


def test_edit_and_delete_exam_result(client, headers_a, patient_id):
    cid = _new(client, headers_a, patient_id).json()["id"]
    ids = [client.post(_url(patient_id, cid, "/exams"), json=e, headers=headers_a).json()["id"] for e in EXAMS]

    edited = {**EXAMS[1], "result": "298"}
    response = client.put(_url(patient_id, cid, f"/exams/{ids[1]}"), json=edited, headers=headers_a)
    assert response.status_code == 200 and response.json()["result"] == "298"

    assert client.delete(_url(patient_id, cid, f"/exams/{ids[0]}"), headers=headers_a).status_code == 204
    exams = client.get(_url(patient_id, cid), headers=headers_a).json()["exam_results"]
    assert [(e["id"], e["result"]) for e in exams] == [(ids[1], "298"), (ids[2], "1,32")]


def test_exam_from_other_consultation_cannot_be_edited(client, headers_a, patient_id):
    c1 = _new(client, headers_a, patient_id).json()["id"]
    c2 = _new(client, headers_a, patient_id).json()["id"]
    exam_id = client.post(_url(patient_id, c1, "/exams"), json=EXAMS[0], headers=headers_a).json()["id"]
    assert client.put(_url(patient_id, c2, f"/exams/{exam_id}"), json=EXAMS[1], headers=headers_a).status_code == 404
    assert client.delete(_url(patient_id, c2, f"/exams/{exam_id}"), headers=headers_a).status_code == 404
    assert client.get(_url(patient_id, c1), headers=headers_a).json()["exam_results"][0]["result"] == "15400"


# --- history -------------------------------------------------------------------

def test_history_newest_first_with_pharmacist_and_crf(client, headers_a, headers_b, patient_id):
    older = _new(client, headers_a, patient_id, "2024-03-10").json()["id"]
    newer = _new(client, headers_b, patient_id, "2025-08-20").json()["id"]
    same_day_later = _new(client, headers_a, patient_id, "2025-08-20").json()["id"]

    history = client.get(f"/patients/{patient_id}/consultations", headers=headers_b).json()
    assert [c["id"] for c in history] == [same_day_later, newer, older]
    assert [(c["pharmacist"]["full_name"], c["pharmacist"]["crf"]) for c in history] == [
        ("Farmacêutica A", "CRF-SP 11111"),
        ("Farmacêutico B", "CRF-RJ 22222"),
        ("Farmacêutica A", "CRF-SP 11111"),
    ]
    assert all(c["patient_id"] == patient_id for c in history)
    assert "soap" not in history[0]  # history is a summary; content comes from the detail route


# --- privacy at rest -----------------------------------------------------------

def test_clinical_data_not_readable_in_database(client, headers_a, patient_id):
    cid = _new(client, headers_a, patient_id).json()["id"]
    client.put(_url(patient_id, cid, "/soap"), json=SOAP_A, headers=headers_a)
    for exam in EXAMS:
        client.post(_url(patient_id, cid, "/exams"), json=exam, headers=headers_a)

    sensitive = list(SOAP_A.values()) + [v for e in EXAMS for v in e.values() if v]
    sensitive += ["náuseas", "Carga Viral", "15400", "T-CD4+", "Creatinina", "cópias/mL"]
    # Short tokens (e.g. "312") could occur by chance inside base64 ciphertext or
    # hex digests, so only distinctive values are scanned for.
    sensitive = [v for v in sensitive if len(v) >= 5]

    with get_engine().connect() as connection:
        rows = [
            *connection.execute(text("SELECT * FROM soap_records")).all(),
            *connection.execute(text("SELECT * FROM exam_results")).all(),
        ]
    stored = " ".join(str(v) for row in rows for v in row)
    assert stored
    for value in sensitive:
        assert value not in stored

    get_engine().dispose()
    raw = Path(get_engine().url.database).read_bytes()
    for value in sensitive:
        assert value.encode() not in raw


# --- previous exams (read-only history) -------------------------------------------------

def _previous(client, headers, pid, cid):
    return client.get(_url(pid, cid, "/previous-exams"), headers=headers)


def test_previous_exams_visible_in_later_consultation(client, headers_a, headers_b, patient_id):
    c1 = _new(client, headers_a, patient_id, "2025-01-10").json()["id"]
    for exam in EXAMS[:2]:
        client.post(_url(patient_id, c1, "/exams"), json=exam, headers=headers_a)
    c2 = _new(client, headers_b, patient_id, "2025-06-20").json()["id"]
    client.post(_url(patient_id, c2, "/exams"), json=EXAMS[2], headers=headers_b)
    c3 = _new(client, headers_a, patient_id).json()["id"]  # today

    groups = _previous(client, headers_a, patient_id, c3).json()
    assert [g["consultation_id"] for g in groups] == [c2, c1]  # newest first
    assert groups[0]["consultation_date"] == "2025-06-20"
    assert groups[0]["pharmacist"]["crf"] == "CRF-RJ 22222"
    assert [e["exam_name"] for e in groups[1]["exam_results"]] == ["Carga Viral HIV-1 RNA", "Linfócitos T-CD4+"]
    first = groups[1]["exam_results"][0]
    assert (first["result"], first["unit"], first["reference_range"]) == ("15400", "cópias/mL", "Indetectável (< 40)")
    # only consultations before the current one; the current one's own exams are not "previous"
    assert [g["consultation_id"] for g in _previous(client, headers_a, patient_id, c2).json()] == [c1]
    assert _previous(client, headers_a, patient_id, c1).json() == []


def test_previous_exams_skip_consultations_without_exams(client, headers_a, patient_id):
    _new(client, headers_a, patient_id, "2025-01-10")
    c2 = _new(client, headers_a, patient_id).json()["id"]
    assert _previous(client, headers_a, patient_id, c2).json() == []


def test_previous_exams_are_read_only_and_unchanged(client, headers_a, patient_id):
    c1 = _new(client, headers_a, patient_id, "2025-01-10").json()["id"]
    exam_id = client.post(_url(patient_id, c1, "/exams"), json=EXAMS[0], headers=headers_a).json()["id"]
    c2 = _new(client, headers_a, patient_id).json()["id"]
    before = _previous(client, headers_a, patient_id, c2).json()
    # no write methods on the history endpoint
    for method in ("post", "put", "delete"):
        response = getattr(client, method)(_url(patient_id, c2, "/previous-exams"), headers=headers_a)
        assert response.status_code == 405
    # a historical exam cannot be changed through the current consultation
    response = client.put(_url(patient_id, c2, f"/exams/{exam_id}"), json=EXAMS[1], headers=headers_a)
    assert response.status_code == 404
    # working on the current consultation does not touch history
    client.post(_url(patient_id, c2, "/exams"), json=EXAMS[1], headers=headers_a)
    client.put(_url(patient_id, c2, "/soap"), json=SOAP_B, headers=headers_a)
    assert _previous(client, headers_a, patient_id, c2).json() == before


def test_previous_exams_require_auth_and_patient_scope(client, headers_a, patient_id):
    c1 = _new(client, headers_a, patient_id).json()["id"]
    other_id = client.post("/patients", json=OTHER_PATIENT, headers=headers_a).json()["id"]
    assert client.get(_url(patient_id, c1, "/previous-exams")).status_code == 401
    assert _previous(client, headers_a, other_id, c1).status_code == 404
