"""Original (2e69755) patient screens with the privacy/safety behaviour kept:
- CPF masked as ***.***.***-NN on the patient page (Mostrar CPF / Ocultar CPF, session-only, per patient,
  forgotten on patient close/open, logout and establishment change), in search rows and in the document caption;
- before “Novo atendimento” closes an open consultation started by ANOTHER pharmacist (backend rule, unchanged),
  the user confirms first.
API stubbed (AppTest) except the backend tests at the end."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_clinical_isolation import CPF as ISO_CPF, iso, new_patient  # noqa: F401  (fixture)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
APP = str(FRONTEND / "app.py")
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402  (the same module app.py imports)
import ui  # noqa: E402

FULL, MASKED, FULL8, MASKED8 = "529.982.247-25", "***.***.***-25", "111.444.777-35", "***.***.***-35"
ME = {"id": 1, "full_name": "Farm", "crf": "CRF/RJ 1"}
COLLEAGUE = {"id": 2, "full_name": "Colega", "crf": "CRF/RJ 2"}
WARNING = ("Já existe um atendimento em andamento para este paciente, iniciado por outro farmacêutico. Ao iniciar "
           "um novo atendimento, o atendimento anterior será encerrado. Deseja continuar?")


def _patient(pid=7, cpf="52998224725"):
    return {"id": pid, "full_name": f"Paciente {pid}", "date_of_birth": "1958-03-14", "cpf": cpf, "phone": None,
            "address": None, "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}


def _consultation(cid, pharmacist=ME, closed=False):
    return {"id": cid, "patient_id": 7, "consultation_date": "2026-11-10", "pharmacist": pharmacist,
            "closed_at": "2026-11-10T12:00:00" if closed else None, "created_at": "x", "updated_at": "x"}


@pytest.fixture
def app(monkeypatch):
    import streamlit as st

    state = {"patients": {7: _patient(7), 8: _patient(8, "11144477735")}, "history": {7: [], 8: []}, "calls": [],
             "me": None, "search": [], "created": []}

    def record(name, value):
        def call(self, *a):
            state["calls"].append(name)
            return value(*a)
        return call

    monkeypatch.setattr(api_client.PharmaTechAPI, "me", lambda self: dict(state["me"] or st.session_state["pharmacist"]))
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_patient", record("get_patient", lambda pid: state["patients"][pid]))
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_consultations", record("list_consultations", lambda pid: state["history"][pid]))
    monkeypatch.setattr(api_client.PharmaTechAPI, "search_patients", record("search", lambda q: state["search"]))

    def create(self, pid):
        state["created"].append(pid)
        return {"id": 50}

    monkeypatch.setattr(api_client.PharmaTechAPI, "create_consultation", create)
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_consultation",
                        lambda self, pid, cid: {**_consultation(cid, ME if cid == 50 else COLLEAGUE), "soap": None,
                                                "exam_results": [], "documents": []})
    monkeypatch.setattr(api_client.PharmaTechAPI, "previous_exams", lambda self, pid, cid: [])
    os.environ["PHARMATECH_API_URL"] = "http://127.0.0.1:9"

    def run(**session):
        at = AppTest.from_file(APP, default_timeout=30)
        at.session_state["token"] = "t"
        at.session_state["pharmacist"] = {**ME, "is_admin": False, "clinical_access": True, "establishment_id": 1,
                                          "establishment_name": "Farmácia A"}
        for key, value in session.items():
            at.session_state[key] = value
        at.run()
        assert not at.exception, at.exception
        return at

    return run, state


def _text(at) -> str:
    parts = [str(e.value) for e in list(at.markdown) + list(at.caption) + list(at.success) + list(at.warning)
             + list(at.error) + list(at.info) + list(at.sidebar.markdown)]
    return " ".join(parts + [b.label for b in at.button])


def _keys(at, prefix):
    return [k for k in at.session_state.keys() if str(k).startswith(prefix)]


def _warnings(at):
    return [w.value for w in at.warning]


# ---- CPF ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, shown", [
    ("52998224725", MASKED), ("529.982.247-25", MASKED), (" 529 982 247 25 ", MASKED), ("5299822472599", "***.***.***-99"),
    ("12", "***.***.***-12"), ("1", "***.***.***-**"), ("", "***.***.***-**"), (None, "***.***.***-**"), ("abc", "***.***.***-**"),
])
def test_mask_never_shows_more_than_the_two_check_digits(value, shown):
    assert ui.mask_cpf_last2(value) == shown and sum(c.isdigit() for c in shown) <= 2
    assert ui.format_cpf_full("52998224725") == FULL


def test_patient_page_masks_cpf_and_toggles_without_writes(app):
    run, state = app
    at = run(patient_id=7)
    assert MASKED in _text(at) and FULL not in _text(at) and "52998224725" not in _text(at)
    assert at.button(key="cpf_toggle_7").label == "Mostrar CPF"
    state["calls"].clear()
    at.button(key="cpf_toggle_7").click().run()
    assert FULL in _text(at) and MASKED not in _text(at) and at.button(key="cpf_toggle_7").label == "Ocultar CPF"
    assert set(state["calls"]) <= {"get_patient", "list_consultations"} and state["created"] == []  # reads only
    at.button(key="cpf_toggle_7").click().run()
    assert MASKED in _text(at) and FULL not in _text(at)


def test_reveal_is_per_patient_and_forgotten_on_close_and_reopen(app):
    run, state = app
    at = run(patient_id=7)
    at.button(key="cpf_toggle_7").click().run()
    assert _keys(at, "cpf_revealed_") == ["cpf_revealed_7"]
    at.session_state["patient_id"] = 8
    at.run()
    assert MASKED8 in _text(at) and FULL8 not in _text(at)  # the other patient stays masked
    at.button(key="back").click().run()  # close_patient()
    assert _keys(at, "cpf_revealed_") == [] and "patient_id" not in at.session_state
    state["search"] = [{k: v for k, v in _patient(7).items() if k not in ("phone", "address")}]
    at.text_input(key="search_query").set_value("Paciente").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    at.button(key="open_7").click().run()
    assert MASKED in _text(at) and FULL not in _text(at)


def test_logout_and_establishment_change_forget_the_reveal(app):
    run, state = app
    at = run(patient_id=7)
    at.button(key="cpf_toggle_7").click().run()
    state["me"] = {**at.session_state["pharmacist"], "establishment_id": 2}
    at.run()
    assert "patient_id" not in at.session_state and _keys(at, "cpf_revealed_") == [] and FULL not in _text(at)
    state["me"] = None
    at = run(patient_id=7)
    at.button(key="cpf_toggle_7").click().run()
    at.button(key="logout").click().run()
    assert _keys(at, "cpf_revealed_") == [] and "token" not in at.session_state


def test_search_rows_use_the_same_mask_and_no_contact(app):
    run, state = app
    state["search"] = [{k: v for k, v in _patient(p, c).items() if k not in ("phone", "address")}
                       for p, c in ((7, "52998224725"), (8, "11144477735"))]
    at = run()
    at.text_input(key="search_query").set_value("Paciente").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    plain = _text(at).replace("\\", "")
    assert MASKED in plain and MASKED8 in plain and "982.247" not in plain and FULL not in plain
    assert {"open_7", "open_8"} <= {b.key for b in at.button}


def test_document_caption_uses_the_mask(app):
    run, _ = app
    at = run(patient_id=7, consultation_id=50)
    captions = " ".join(str(c.value) for c in at.caption).replace("\\", "")
    assert "preenchidos automaticamente" in captions and MASKED in captions and FULL not in _text(at)


# ---- warning before closing a colleague's open consultation --------------------------------------------

def test_no_open_or_own_open_consultation_starts_directly(app):
    run, state = app
    state["history"][7] = [_consultation(9), _consultation(3, closed=True)]  # own open one: original behaviour
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    assert state["created"] == [7] and WARNING not in _warnings(at) and at.session_state["consultation_id"] == 50


def test_colleague_open_consultation_asks_first_and_cancel_changes_nothing(app):
    run, state = app
    state["history"][7] = [_consultation(8, COLLEAGUE), _consultation(3, closed=True)]
    at = run(patient_id=7)
    assert WARNING not in _warnings(at) and "Em andamento" in _text(at) and "Colega" in _text(at)
    at.button(key="new_consultation").click().run()
    assert _warnings(at) == [WARNING] and state["created"] == []
    assert not [b for b in at.button if b.key == "new_consultation"] and _keys(at, "new_consultation_confirm_") == ["new_consultation_confirm_7"]
    assert {"new_consultation_cancel_7", "new_consultation_yes_7", "open_consultation_8"} <= {b.key for b in at.button}
    at.button(key="new_consultation_cancel_7").click().run()
    assert WARNING not in _warnings(at) and state["created"] == [] and _keys(at, "new_consultation_confirm_") == []


def test_confirm_creates_exactly_once(app):
    run, state = app
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="new_consultation_yes_7").click().run()
    assert state["created"] == [7] and at.session_state["consultation_id"] == 50 and _keys(at, "new_consultation_confirm_") == []
    assert not [b for b in at.button if str(b.key).startswith("new_consultation_yes_")]
    at.run()
    assert state["created"] == [7]


def test_colleague_consultation_stays_readable(app):
    run, state = app
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="open_consultation_8").click().run()
    assert at.session_state["consultation_id"] == 8 and state["created"] == []
    assert any("Somente leitura" in i.value for i in at.info)


def test_confirmation_is_per_patient_and_cleared(app):
    run, state = app
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.session_state["patient_id"] = 8
    at.run()
    assert WARNING not in _warnings(at)
    at.button(key="back").click().run()
    assert _keys(at, "new_consultation_confirm_") == [] and state["created"] == []
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="logout").click().run()
    assert _keys(at, "new_consultation_confirm_") == [] and state["created"] == []


def test_establishment_change_while_asking_creates_nothing(app):
    run, state = app
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    state["me"] = {**at.session_state["pharmacist"], "establishment_id": 2}
    at.button(key="new_consultation_yes_7").click().run()
    assert state["created"] == [] and "patient_id" not in at.session_state and _keys(at, "new_consultation_confirm_") == []


def test_create_404_drops_the_stale_patient(app, monkeypatch):
    run, state = app
    state["history"][7] = [_consultation(8, COLLEAGUE)]

    def gone(self, pid):
        raise api_client.ApiError(404, "Paciente não encontrado.")

    monkeypatch.setattr(api_client.PharmaTechAPI, "create_consultation", gone)
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="new_consultation_yes_7").click().run()
    assert "patient_id" not in at.session_state and any("não encontrado" in e.value for e in at.error)


# ---- backend (real API): rules unchanged ---------------------------------------------------------------

def test_search_response_has_no_contact_fields(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    c.patch(f"/patients/{pid}/contact", headers=iso["a1"], json={"phone": "(21) 90000-0000", "address": "Rua X"})
    found = c.post("/patients/search", headers=iso["a1"], json={"query": ISO_CPF}).json()
    assert [p["id"] for p in found] == [pid]
    assert set(found[0]) == {"id", "full_name", "date_of_birth", "cpf", "created_at", "updated_at"}


def test_backend_closes_the_colleagues_open_consultation_and_keeps_access_rules(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    col = c.post(f"/patients/{pid}/consultations", headers=iso["a2"], json={}).json()["id"]
    assert c.post(f"/patients/{pid}/consultations", headers=iso["b1"], json={}).status_code == 404
    assert c.post(f"/patients/{pid}/consultations", headers=iso["admin"], json={}).status_code == 403
    assert c.post(f"/patients/{pid}/consultations", json={}).status_code == 401
    assert c.get(f"/patients/{pid}/consultations/{col}", headers=iso["a1"]).status_code == 200
    new = c.post(f"/patients/{pid}/consultations", headers=iso["a1"], json={}).json()["id"]
    history = c.get(f"/patients/{pid}/consultations", headers=iso["a1"]).json()
    assert [h["id"] for h in history if h["closed_at"] is None] == [new] and len(history) == 2
