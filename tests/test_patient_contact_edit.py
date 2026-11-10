"""Editing an existing patient's phone/address only (PATCH /patients/{id}/contact): encrypted, kept as
typed, establishment-scoped, safe against a concurrent transfer; nothing else about the patient changes."""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from backend.database import get_engine
from tests.test_clinical_isolation import CPF, iso, new_patient  # noqa: F401  (fixture)
from tests.test_clinical_isolation_races import run_with_transfer

PHONE = "(21) 99999-9999"
ADDRESS = "Rua Exemplo, 10 - Niterói/RJ"


def patch(env, h, pid, phone, address):
    return env["c"].patch(f"/patients/{pid}/contact", headers=h, json={"phone": phone, "address": address})


def row(pid):
    with get_engine().connect() as conn:
        return dict(conn.execute(text("SELECT * FROM patients WHERE id = :i"), {"i": pid}).mappings().one())


def tokens(pid):
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT id, token_index FROM patient_name_tokens WHERE patient_id = :i ORDER BY id"),
                            {"i": pid}).fetchall()


IDENTITY = ("id", "establishment_id", "full_name_encrypted", "full_name_index", "date_of_birth_encrypted", "cpf_encrypted",
            "cpf_index", "created_at")


def test_existing_patient_gets_contact_and_identity_is_untouched(iso):  # noqa: F811
    pid = new_patient(iso, iso["a1"])  # registered without contact
    before, toks = row(pid), tokens(pid)
    assert (before["phone_encrypted"], before["address_encrypted"]) == (None, None)
    r = patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    assert r.status_code == 200 and (r.json()["phone"], r.json()["address"]) == (PHONE, ADDRESS)
    assert iso["c"].get(f"/patients/{pid}", headers=iso["a1"]).json()["phone"] == PHONE
    after = row(pid)
    assert {k: after[k] for k in IDENTITY} == {k: before[k] for k in IDENTITY}  # name/CPF/DOB/index/owner unchanged
    assert tokens(pid) == toks
    assert after["phone_encrypted"] and after["address_encrypted"]


@pytest.mark.parametrize("first, second, expected", [
    ((PHONE, ADDRESS), ("+55 21 98888-7777", ADDRESS), ("+55 21 98888-7777", ADDRESS)),  # phone only
    ((PHONE, ADDRESS), (PHONE, "Av. Nova, 200"), (PHONE, "Av. Nova, 200")),  # address only
    ((PHONE, ADDRESS), ("", ADDRESS), (None, ADDRESS)),  # clear phone
    ((PHONE, ADDRESS), (PHONE, "   "), (PHONE, None)),  # clear address
    ((PHONE, ADDRESS), ("", " \t "), (None, None)),  # both blank
    ((None, None), ("  " + PHONE + "  ", "  " + ADDRESS), (PHONE, ADDRESS)),  # trimmed, kept as typed
])
def test_update_semantics(iso, first, second, expected):  # noqa: F811
    pid = new_patient(iso, iso["a1"])
    if first != (None, None):
        assert patch(iso, iso["a1"], pid, *first).status_code == 200
    r = patch(iso, iso["a1"], pid, *second)
    assert r.status_code == 200 and (r.json()["phone"], r.json()["address"]) == expected
    stored = row(pid)
    assert (stored["phone_encrypted"] is None, stored["address_encrypted"] is None) == (expected[0] is None, expected[1] is None)


def test_unchanged_value_keeps_its_ciphertext(iso):  # noqa: F811
    pid = new_patient(iso, iso["a1"])
    patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    before = row(pid)
    patch(iso, iso["a1"], pid, PHONE, "Outra Rua, 5")
    after = row(pid)
    assert after["phone_encrypted"] == before["phone_encrypted"] and after["address_encrypted"] != before["address_encrypted"]
    same = row(pid)
    patch(iso, iso["a1"], pid, PHONE, "Outra Rua, 5")  # nothing changed: nothing rewritten
    assert row(pid) == same


def test_limits_missing_keys_and_extra_fields_write_nothing(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    before = row(pid)
    bad = [{"phone": "9" * 41, "address": ""}, {"phone": "", "address": "á" * 301}, {"phone": PHONE},
           {"address": ADDRESS}, {}, {"phone": None, "address": None},
           {"phone": PHONE, "address": ADDRESS, "full_name": "Outro Nome"},
           {"phone": PHONE, "address": ADDRESS, "cpf": "11144477735"},
           {"phone": PHONE, "address": ADDRESS, "establishment_id": iso["B"]},
           {"phone": PHONE, "address": ADDRESS, "date_of_birth": "1990-01-01"}]
    for body in bad:
        assert c.patch(f"/patients/{pid}/contact", headers=iso["a1"], json=body).status_code == 422, body
    assert row(pid) == before
    r = patch(iso, iso["a1"], pid, "9" * 40, "á" * 300)
    assert r.status_code == 200 and (r.json()["phone"], r.json()["address"]) == ("9" * 40, "á" * 300)


def test_contact_encrypted_at_rest_and_search_unchanged(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    get_engine().dispose()
    data = open(get_engine().url.database, "rb").read()
    for needle in (PHONE, "99999-9999", ADDRESS, "Exemplo, 10", "Niterói"):
        assert needle.encode() not in data, needle
    for query in (CPF, "comum"):
        found = c.post("/patients/search", headers=iso["a1"], json={"query": query}).json()
        assert [p["id"] for p in found] == [pid] and "phone" not in found[0] and "address" not in found[0]
    assert c.post("/patients/search", headers=iso["a1"], json={"query": "Exemplo"}).json() == []


def test_authorization(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    before = row(pid)
    assert c.patch(f"/patients/{pid}/contact", json={"phone": "", "address": ""}).status_code == 401
    assert patch(iso, iso["admin"], pid, "", "").status_code == 403
    r = patch(iso, iso["b1"], pid, "", "")
    assert r.status_code == 404 and r.json()["detail"] == "Paciente não encontrado."
    assert patch(iso, iso["a1"], 999999, "", "").status_code == 404
    assert row(pid) == before
    r = patch(iso, iso["a2"], pid, "(21) 2222-2222", ADDRESS)  # colleague in the same establishment
    assert r.status_code == 200 and r.json()["phone"] == "(21) 2222-2222"
    iso["move"]("a1@iso.test", "B")
    after_colleague = row(pid)
    assert patch(iso, iso["a1"], pid, "", "").status_code == 404  # moved pharmacist
    assert row(pid) == after_colleague


@pytest.mark.parametrize("hold_lock", [False, True])
def test_contact_update_vs_transfer(iso, monkeypatch, hold_lock):  # noqa: F811
    pid = new_patient(iso, iso["a1"])
    patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    before = row(pid)
    r = run_with_transfer(monkeypatch, iso, lambda c: c.patch(f"/patients/{pid}/contact", headers=iso["a1"],
                                                              json={"phone": "(21) 1111-1111", "address": ""}),
                          hold_lock=hold_lock)
    after = iso["c"].get(f"/patients/{pid}", headers=iso["a2"]).json()
    if hold_lock:  # the update held the lock first: it committed, then the transfer
        assert r.status_code == 200 and (after["phone"], after["address"]) == ("(21) 1111-1111", None)
    else:  # transfer first: the stale update is refused and nothing is written
        assert r.status_code == 404 and row(pid) == before
    assert iso["c"].get(f"/patients/{pid}", headers=iso["a1"]).status_code == 404


def test_documents_do_not_print_edited_contact(iso):  # noqa: F811
    import io

    from pypdf import PdfReader

    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    patch(iso, iso["a1"], pid, PHONE, ADDRESS)
    cid = c.post(f"/patients/{pid}/consultations", headers=iso["a1"], json={}).json()["id"]
    pdf = c.post(f"/patients/{pid}/consultations/{cid}/documents/referral", headers=iso["a1"],
                 json={"destination": "D", "case_summary": "R", "prm": "P", "suggested_conduct": "C"}).content
    body = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "99999-9999" not in body and "Exemplo" not in body


# ---- UI (API stubbed) -----------------------------------------------------------------------------

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402  (the same module app.py imports)


@pytest.fixture
def ui(monkeypatch):
    import streamlit as st

    state = {"patient": {"id": 7, "full_name": "Paciente UI", "date_of_birth": "1970-01-15", "cpf": "52998224725",
                         "phone": None, "address": None, "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"},
             "calls": []}

    def update(self, pid, phone, address):
        state["calls"].append((pid, phone, address))
        state["patient"] = {**state["patient"], "phone": phone.strip() or None, "address": address.strip() or None}
        return state["patient"]

    monkeypatch.setattr(api_client.PharmaTechAPI, "me", lambda self: dict(st.session_state["pharmacist"]))
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_patient", lambda self, pid: state["patient"])
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_consultations", lambda self, pid: [])
    monkeypatch.setattr(api_client.PharmaTechAPI, "update_patient_contact", update)
    os.environ["PHARMATECH_API_URL"] = "http://127.0.0.1:9"
    at = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=30)
    at.session_state["token"] = "t"
    at.session_state["pharmacist"] = {"id": 1, "full_name": "Farm", "crf": "CRF/RJ 1", "is_admin": False,
                                      "clinical_access": True, "establishment_id": 1}
    at.session_state["patient_id"] = 7
    at.run()
    assert not at.exception, at.exception
    return at, state


def _texts(at):
    return " ".join(str(m.value) for m in at.markdown)


def test_ui_edit_save_and_cancel(ui):
    at, state = ui
    assert "Telefone" in _texts(at) and "—" in _texts(at)
    at.button(key="contact_edit_open").click().run()
    at.text_input(key="contact_7_phone").set_value(PHONE)
    at.text_input(key="contact_7_address").set_value(ADDRESS)
    at.button(key="FormSubmitter:contact_form_7-Salvar").click().run()
    assert state["calls"] == [(7, PHONE, ADDRESS)]
    assert [s.value for s in at.success] == ["Contato atualizado."] and PHONE in _texts(at) and ADDRESS in _texts(at)
    # cancel: typed change discarded, no call, editor reopens with the stored values
    at.button(key="contact_edit_open").click().run()
    at.text_input(key="contact_7_phone").set_value("(11) 0000-0000")
    at.button(key="FormSubmitter:contact_form_7-Cancelar").click().run()
    assert state["calls"] == [(7, PHONE, ADDRESS)] and PHONE in _texts(at)
    at.button(key="contact_edit_open").click().run()
    assert at.text_input(key="contact_7_phone").value == PHONE
