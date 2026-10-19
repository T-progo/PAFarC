"""Test/kit identification (Fabricante, Lote, Validade) stored with each exam result, and the
suggestion of the last kit used for the same exam. Fictitious data only."""
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from backend import database
from backend.auth import create_pharmacist
from backend.models import ROLE_ADMINISTRATOR, ExamResult

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frontend"))
from exam_kit import EMPTY_KIT, exam_form_has_input, kit_from_api, merge_kit_suggestion  # noqa: E402

PASSWORD = "senha-segura-1"
KIT_A = {"manufacturer": "Fabricante A", "lot": "LOTE-001", "expiry_date": "2027-12-31"}


def _headers(client, login):
    token = client.post("/auth/login", data={"username": login, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(client, db):
    create_pharmacist(db, full_name="Admin Kit", crf="", login="admin@kit.test", password=PASSWORD,
                      role=ROLE_ADMINISTRATOR)
    for name, login in (("Farmacêutica Kit A", "a@kit.test"), ("Farmacêutico Kit B", "b@kit.test")):
        create_pharmacist(db, full_name=name, crf="10001", crf_uf="RJ", login=login, password=PASSWORD)
    a, b = _headers(client, "a@kit.test"), _headers(client, "b@kit.test")
    pid = client.post("/patients", headers=a, json={"full_name": "Paciente Kit Ficticio",
                                                    "date_of_birth": "1970-01-01", "cpf": "52998224725"}).json()["id"]

    def consultation(headers=a):
        cid = client.post(f"/patients/{pid}/consultations", headers=headers, json={}).json()["id"]
        return f"/patients/{pid}/consultations/{cid}"

    return {"a": a, "b": b, "admin": _headers(client, "admin@kit.test"), "pid": pid, "new": consultation}


def _add(client, base, headers, **fields):
    exam = {"exam_name": "Glicemia", "result": "98", "unit": "mg/dL", "reference_range": "70 - 99",
            "notes": "Jejum.", **fields}
    r = client.post(f"{base}/exams", headers=headers, json=exam)
    assert r.status_code == 201, r.text
    return r.json()


def _suggest(client, headers, name):
    return client.post("/exam-kit-suggestion", headers=headers, json={"exam_name": name})


# --- persistence -------------------------------------------------------------------------

def test_kit_is_saved_with_the_exam_result_and_can_be_edited(client, env):
    base = env["new"]()
    exam = _add(client, base, env["a"], **KIT_A)
    assert (exam["manufacturer"], exam["lot"], exam["expiry_date"]) == ("Fabricante A", "LOTE-001", "2027-12-31")
    r = client.put(f"{base}/exams/{exam['id']}", headers=env["a"], json={
        "exam_name": "Glicemia", "result": "98", "manufacturer": "Fabricante B", "lot": " LOTE-009 ",
        "expiry_date": "2028-01-15"})
    assert (r.json()["manufacturer"], r.json()["lot"], r.json()["expiry_date"]) == ("Fabricante B", "LOTE-009", "2028-01-15")
    saved = client.get(base, headers=env["a"]).json()["exam_results"][0]
    assert (saved["manufacturer"], saved["lot"], saved["expiry_date"]) == ("Fabricante B", "LOTE-009", "2028-01-15")


def test_kit_is_optional_and_past_expiry_is_accepted(client, env):
    base = env["new"]()
    exam = _add(client, base, env["a"], manufacturer="  ", lot="")
    assert exam["manufacturer"] is None and exam["lot"] is None and exam["expiry_date"] is None
    past = _add(client, base, env["a"], manufacturer="Fab", lot="L", expiry_date="2020-01-01")
    assert past["expiry_date"] == "2020-01-01"  # informational only, never blocked


@pytest.mark.parametrize("change", [{"expiry_date": "31/12/2027"}, {"manufacturer": "x" * 201},
                                    {"lot": "x" * 101}])
def test_invalid_kit_values_rejected(client, env, change):
    r = client.post(f"{env['new']()}/exams", headers=env["a"],
                    json={"exam_name": "Glicemia", "result": "1", **KIT_A, **change})
    assert r.status_code == 422


def test_old_rows_without_kit_remain_valid(client, env, db):
    base = env["new"]()
    cid = int(base.rsplit("/", 1)[1])
    db.add(ExamResult(consultation_id=cid, exam_name="Ureia", result="30", unit="mg/dL", reference_range="",
                      notes=""))  # as recorded before the kit existed: no kit, no name index
    db.commit()
    exams = client.get(base, headers=env["a"]).json()["exam_results"]
    assert [(e["exam_name"], e["manufacturer"], e["lot"], e["expiry_date"]) for e in exams] == [("Ureia", None, None, None)]
    assert _suggest(client, env["a"], "Ureia").json() == {"manufacturer": None, "lot": None, "expiry_date": None}


def test_migration_adds_kit_columns_without_touching_existing_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE exam_results (id INTEGER PRIMARY KEY, consultation_id INTEGER, "
                          "exam_name_encrypted TEXT, result_encrypted TEXT, unit_encrypted TEXT, "
                          "reference_range_encrypted TEXT, notes_encrypted TEXT, created_at DATETIME)"))
        conn.execute(text("INSERT INTO exam_results VALUES (1, 1, 'n', 'r', 'u', 'ref', 'obs', '2026-01-01')"))
    for _ in range(2):  # idempotent
        database._add_missing_columns(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("exam_results")}
    assert {"manufacturer_encrypted", "lot_encrypted", "expiry_date_encrypted", "exam_name_index"} <= columns
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM exam_results")).mappings().one()
    assert (row["exam_name_encrypted"], row["result_encrypted"], row["notes_encrypted"]) == ("n", "r", "obs")
    assert row["manufacturer_encrypted"] is None and row["lot_encrypted"] is None
    assert row["expiry_date_encrypted"] is None and row["exam_name_index"] is None


# --- suggestion -----------------------------------------------------------------------------

def test_later_consultation_gets_the_latest_kit_for_the_same_exam(client, env):
    _add(client, env["new"](), env["a"], **KIT_A)
    assert _suggest(client, env["a"], "Glicemia").json() == {
        "manufacturer": "Fabricante A", "lot": "LOTE-001", "expiry_date": "2027-12-31"}
    _add(client, env["new"](), env["a"], manufacturer="Fabricante A", lot="LOTE-002", expiry_date="2028-06-30")
    assert _suggest(client, env["a"], "Glicemia").json()["lot"] == "LOTE-002"  # latest wins
    _add(client, env["new"](), env["a"], exam_name="Glicemia", result="1")  # no kit: never a source
    assert _suggest(client, env["a"], "Glicemia").json()["lot"] == "LOTE-002"


@pytest.mark.parametrize("name", ["glicemia", "GLICEMIA", "  Glicemia ", "Glicêmia", "glicemía"])
def test_matching_ignores_case_accents_and_surrounding_spaces(client, env, name):
    _add(client, env["new"](), env["a"], **KIT_A)
    assert _suggest(client, env["a"], name).json()["lot"] == "LOTE-001"


@pytest.mark.parametrize("name", ["Glicemia capilar", "Glicemi", "Glicemia 2", "Colesterol", "Hemoglobina glicada"])
def test_unrelated_or_similar_names_do_not_match(client, env, name):
    _add(client, env["new"](), env["a"], **KIT_A)
    assert _suggest(client, env["a"], name).json() == {"manufacturer": None, "lot": None, "expiry_date": None}


def test_suggestion_returns_only_the_kit_fields(client, env):
    _add(client, env["new"](), env["a"], **KIT_A)
    body = _suggest(client, env["a"], "Glicemia").json()
    assert set(body) == {"manufacturer", "lot", "expiry_date"}
    for leaked in ("98", "mg/dL", "70 - 99", "Jejum", "Paciente", "529"):
        assert leaked not in str(body)


def test_no_suggestion_without_prior_kit(client, env):
    assert _suggest(client, env["a"], "Glicemia").json() == {"manufacturer": None, "lot": None, "expiry_date": None}


def test_suggestion_uses_only_the_pharmacists_own_entries(client, env):
    _add(client, env["new"](env["a"]), env["a"], **KIT_A)
    assert _suggest(client, env["b"], "Glicemia").json()["lot"] is None
    _add(client, env["new"](env["b"]), env["b"], manufacturer="Fab B", lot="LOTE-B", expiry_date="2029-01-01")
    assert _suggest(client, env["a"], "Glicemia").json()["lot"] == "LOTE-001"
    assert _suggest(client, env["b"], "Glicemia").json()["lot"] == "LOTE-B"


def test_suggestion_authorization(client, env):
    assert _suggest(client, env["admin"], "Glicemia").status_code == 403
    assert _suggest(client, {}, "Glicemia").status_code == 401
    assert _suggest(client, env["a"], "").status_code == 422


def test_existing_exam_keeps_its_own_kit_after_newer_ones(client, env):
    old_base = env["new"]()
    old = _add(client, old_base, env["a"], **KIT_A)
    # a newer Glicemia kit recorded for another patient (so old_base stays open and editable)
    other_pid = client.post("/patients", headers=env["a"], json={
        "full_name": "Outro Paciente Kit", "date_of_birth": "1980-01-01", "cpf": "11144477735"}).json()["id"]
    other_cid = client.post(f"/patients/{other_pid}/consultations", headers=env["a"], json={}).json()["id"]
    _add(client, f"/patients/{other_pid}/consultations/{other_cid}", env["a"],
         manufacturer="Fabricante A", lot="LOTE-NEW", expiry_date="2030-01-01")
    assert _suggest(client, env["a"], "Glicemia").json()["lot"] == "LOTE-NEW"
    shown = client.get(old_base, headers=env["a"]).json()["exam_results"][0]
    assert (shown["lot"], shown["expiry_date"]) == ("LOTE-001", "2027-12-31")
    r = client.put(f"{old_base}/exams/{old['id']}", headers=env["a"], json={**{k: shown[k] for k in (
        "exam_name", "result", "unit", "reference_range", "notes", "manufacturer", "lot", "expiry_date")},
        "result": "99"})
    assert (r.json()["result"], r.json()["lot"], r.json()["expiry_date"]) == ("99", "LOTE-001", "2027-12-31")


def test_other_pharmacist_cannot_add_or_edit_kit(client, env):
    base = env["new"]()
    exam = _add(client, base, env["a"], **KIT_A)
    assert client.post(f"{base}/exams", headers=env["b"], json={"exam_name": "X", "result": "1", **KIT_A}).status_code == 403
    assert client.put(f"{base}/exams/{exam['id']}", headers=env["b"],
                      json={"exam_name": "Glicemia", "result": "1", "lot": "HACK"}).status_code == 403
    assert client.post(f"{base}/exams", headers=env["admin"], json={"exam_name": "X", "result": "1"}).status_code == 403


# --- form rule: a suggestion never overwrites what the pharmacist typed ----------------------

def test_merge_fills_empty_fields_and_replaces_only_untouched_suggestions():
    first = kit_from_api({"manufacturer": "Fabricante A", "lot": "LOTE-001", "expiry_date": "2027-12-31"})
    assert first == {"manufacturer": "Fabricante A", "lot": "LOTE-001", "expiry": date(2027, 12, 31)}
    filled = merge_kit_suggestion(EMPTY_KIT, EMPTY_KIT, first)
    assert filled == first
    # pharmacist changes the lot, then edits the exam name again: the typed lot stays
    typed = {**filled, "lot": "LOTE-002"}
    other = kit_from_api({"manufacturer": "Fabricante Z", "lot": "LOTE-Z", "expiry_date": "2029-01-01"})
    after = merge_kit_suggestion(typed, first, other)
    assert after == {"manufacturer": "Fabricante Z", "lot": "LOTE-002", "expiry": date(2029, 1, 1)}
    # a name without history clears untouched suggested values but keeps typed ones
    assert merge_kit_suggestion(typed, first, kit_from_api({})) == {"manufacturer": "", "lot": "LOTE-002",
                                                                     "expiry": None}


# --- finalization guard: unsaved input in the add-exam form ----------------------------------

def test_exam_form_has_input_detects_any_typed_or_suggested_value():
    empty = {"name": "", "result": "", "unit": "", "reference": "", "notes": "", "manufacturer": "", "lot": "",
             "expiry": None}
    assert not exam_form_has_input(empty)
    assert not exam_form_has_input({})  # form not rendered yet
    assert not exam_form_has_input({**empty, "name": "   "})  # whitespace only
    for field, value in (("name", "Glicemia"), ("result", "98"), ("unit", "mg/dL"), ("reference", "70 - 99"),
                         ("notes", "Jejum"), ("manufacturer", "Fab A"), ("lot", "L1"), ("expiry", date(2027, 1, 1))):
        assert exam_form_has_input({**empty, field: value}), field

