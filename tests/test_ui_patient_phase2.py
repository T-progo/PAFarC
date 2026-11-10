"""UI redesign Phase 2: patient search, registration, patient banner/workspace and CPF privacy display.
CPF is masked by default (***.***.***-NN) on the patient page; "Mostrar CPF" / "Ocultar CPF" only change
session display state (no request, nothing stored); closing the patient or an establishment change forgets
it. Search keeps its masked CPF and no contact data; the document caption no longer shows the full CPF.
API stubbed (AppTest) except where stated."""
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_clinical_isolation import CPF as ISO_CPF, iso, new_patient  # noqa: F401  (fixture)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
APP = str(FRONTEND / "app.py")
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402  (the same module app.py imports)
import ui  # noqa: E402

XSS = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
FULL, MASKED = "529.982.247-25", "***.***.***-25"
FULL8, MASKED8 = "111.444.777-35", "***.***.***-35"
ME = {"id": 1, "full_name": "Farm", "crf": "CRF/RJ 1"}
COLLEAGUE = {"id": 2, "full_name": "Colega", "crf": "CRF/RJ 2"}


def _patient(pid=7, cpf="52998224725", **extra):
    return {"id": pid, "full_name": f"Paciente {pid}", "date_of_birth": "1958-03-14", "cpf": cpf, "phone": None,
            "address": None, "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00", **extra}


def _consultation(cid, pharmacist=ME, closed=False, day="2026-11-10"):
    return {"id": cid, "patient_id": 7, "consultation_date": day, "pharmacist": pharmacist,
            "closed_at": "2026-11-10T12:00:00" if closed else None, "created_at": "x", "updated_at": "x"}


# ---- helpers ------------------------------------------------------------------------------------------

def test_cpf_mask_shows_only_the_two_check_digits():
    assert ui.mask_cpf_last2("52998224725") == MASKED
    assert ui.mask_cpf_last2("529.982.247-25") == MASKED  # derived from digits, whatever the formatting
    assert ui.mask_cpf_last2("") == "***.***.***-**" and ui.mask_cpf_last2(None) == "***.***.***-**"
    assert ui.format_cpf_full("52998224725") == FULL
    masked_digits = [c for c in MASKED if c.isdigit()]
    assert masked_digits == ["2", "5"]  # nothing but the last two digits


@pytest.mark.parametrize("born, today, years", [
    (date(1958, 3, 14), date(2026, 3, 13), 67),  # day before the birthday
    (date(1958, 3, 14), date(2026, 3, 14), 68),  # on the birthday
    (date(1958, 3, 14), date(2026, 12, 31), 68),
    (date(2000, 2, 29), date(2025, 2, 28), 24),  # leap-day birthday, non-leap year
    (date(2000, 2, 29), date(2025, 3, 1), 25),
    (date(2025, 6, 1), date(2026, 5, 31), 0),
])
def test_age_is_completed_years(born, today, years):
    assert ui.age_on(born, today) == years


def test_age_labels():
    assert ui.age_label(date(2025, 1, 1), date(2026, 1, 1)) == "1 ano"
    assert ui.birth_label("1958-03-14", date(2026, 11, 10)) == "68 anos · 14/03/1958"


def test_new_html_helpers_escape():
    for html_out in (ui.banner_title_html(XSS), ui.meta_html([XSS, XSS]), ui.section_label_html(XSS),
                     ui.kv_html([(XSS, XSS)])):
        assert "<script" not in html_out and "<img" not in html_out and "&lt;script&gt;" in html_out


# ---- app ----------------------------------------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    import streamlit as st

    state = {"patients": {7: _patient(7), 8: _patient(8, "11144477735")}, "history": {7: [], 8: []},
             "calls": [], "me": None, "search": []}

    def me(self):
        state["calls"].append("me")
        return dict(state["me"] or st.session_state["pharmacist"])

    def get_patient(self, pid):
        state["calls"].append("get_patient")
        return state["patients"][pid]

    def list_consultations(self, pid):
        state["calls"].append("list_consultations")
        return state["history"][pid]

    def search(self, query):
        state["calls"].append("search")
        return state["search"]

    def create_patient(self, *args):
        state["calls"].append(("create_patient", args))
        return state["patients"][7]

    def forbidden(name):
        def call(self, *a, **k):
            state["calls"].append(name)
            raise AssertionError(f"unexpected API call {name}")
        return call

    monkeypatch.setattr(api_client.PharmaTechAPI, "me", me)
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_patient", get_patient)
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_consultations", list_consultations)
    monkeypatch.setattr(api_client.PharmaTechAPI, "search_patients", search)
    monkeypatch.setattr(api_client.PharmaTechAPI, "create_patient", create_patient)
    for name in ("update_patient_contact", "create_consultation"):
        monkeypatch.setattr(api_client.PharmaTechAPI, name, forbidden(name))
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
    parts += [b.label for b in at.button]
    return " ".join(parts)


def _reveal_keys(at):
    return [k for k in at.session_state.keys() if str(k).startswith("cpf_revealed_")]


def test_patient_page_masks_cpf_by_default_and_toggles(app):
    run, state = app
    at = run(patient_id=7)
    text = _text(at)
    assert text.count(MASKED) == 2 and FULL not in text and "52998224725" not in text  # banner + Dados cadastrais
    toggle = at.button(key="cpf_toggle_7")
    assert toggle.label == "Mostrar CPF"
    state["calls"].clear()
    toggle.click().run()
    text = _text(at)
    assert text.count(FULL) == 2 and MASKED not in text and at.button(key="cpf_toggle_7").label == "Ocultar CPF"
    # only the normal page reads ran again (same as any click on this page); no write, no extra endpoint
    assert set(state["calls"]) <= {"me", "get_patient", "list_consultations"}
    at.button(key="cpf_toggle_7").click().run()
    text = _text(at)
    assert text.count(MASKED) == 2 and FULL not in text and at.button(key="cpf_toggle_7").label == "Mostrar CPF"


def test_reveal_is_patient_specific(app):
    run, _ = app
    at = run(patient_id=7)
    at.button(key="cpf_toggle_7").click().run()
    assert at.session_state["cpf_revealed_7"] is True
    at.session_state["patient_id"] = 8  # another patient in the same session
    at.run()
    text = _text(at)
    assert MASKED8 in text and FULL8 not in text and FULL not in text


def test_closing_or_opening_a_patient_forgets_the_reveal(app):
    run, state = app
    at = run(patient_id=7)
    at.button(key="cpf_toggle_7").click().run()
    assert _reveal_keys(at) == ["cpf_revealed_7"]  # the helper does see the reveal state
    at.button(key="back").click().run()  # close_patient()
    assert _reveal_keys(at) == [] and "patient_id" not in at.session_state
    state["search"] = [{k: v for k, v in _patient(7).items() if k not in ("phone", "address")}]
    at.text_input(key="search_query").set_value("Paciente").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    at.button(key="open_7").click().run()
    text = _text(at)
    assert MASKED in text and FULL not in text  # reopened: masked again


def test_establishment_change_clears_patient_and_reveal(app):
    run, state = app
    at = run(patient_id=7)
    at.button(key="cpf_toggle_7").click().run()
    state["me"] = {**at.session_state["pharmacist"], "establishment_id": 2, "establishment_name": "Farmácia B"}
    at.run()
    assert "patient_id" not in at.session_state and _reveal_keys(at) == []
    assert FULL not in _text(at) and "Paciente 7" not in _text(at)


def test_search_rows_use_the_standard_mask_and_no_contact(app):
    run, state = app
    state["search"] = [{k: v for k, v in _patient(pid, cpf).items() if k not in ("phone", "address")}
                       for pid, cpf in ((7, "52998224725"), (8, "11144477735"))]
    state["patients"][7]["phone"] = "(21) 90000-0000"
    at = run()
    at.text_input(key="search_query").set_value("Paciente").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    text = _text(at)
    plain = text.replace("\\", "")  # rows are Markdown-escaped
    # Phase 2.1: the same mask as the patient page (was ***.982.247-**, which exposed the middle digits)
    assert MASKED in plain and MASKED8 in plain and "982.247" not in plain and "444.777" not in plain
    assert FULL not in text and "90000-0000" not in text
    assert "2 pacientes encontrados" in text and {"open_7", "open_8"} <= {b.key for b in at.button}
    assert "68 anos" in text or "67 anos" in text  # age · date of birth shown, never instead of the date
    assert "14/03/1958" in text


def test_search_new_patient_and_empty_state_use_the_existing_menu_entry(app):
    run, state = app
    at = run()
    at.text_input(key="search_query").set_value("ninguem").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    assert "Nenhum paciente encontrado" in _text(at)
    at.button(key="empty_new_patient").click().run()
    assert at.session_state["nav"] == "Cadastrar paciente" and at.text_input(key="reg_name") is not None
    at = run()
    at.button(key="search_new_patient").click().run()
    assert at.session_state["nav"] == "Cadastrar paciente"


def test_registration_layout_keeps_fields_validation_and_request(app):
    run, state = app
    at = run(nav="Cadastrar paciente")
    assert {"reg_name", "reg_cpf", "reg_phone", "reg_address"} <= {w.key for w in at.text_input}
    at.button(key="FormSubmitter:register_form-Cadastrar").click().run()
    assert any("Preencha nome completo" in w.value for w in at.warning)
    assert not [c for c in state["calls"] if isinstance(c, tuple)]
    at.text_input(key="reg_name").set_value("Nova Paciente")
    at.date_input(key="reg_dob").set_value(date(1990, 5, 6))
    at.text_input(key="reg_cpf").set_value("529.982.247-25")
    at.text_input(key="reg_phone").set_value("")
    at.text_input(key="reg_address").set_value("Rua A, 1")
    at.button(key="FormSubmitter:register_form-Cadastrar").click().run()
    created = [c for c in state["calls"] if isinstance(c, tuple)]
    assert created == [("create_patient", ("Nova Paciente", date(1990, 5, 6), "529.982.247-25", "", "Rua A, 1"))]
    assert at.session_state["patient_id"] == 7 and MASKED in _text(at)


def test_primary_action_and_history_rows(app):
    run, state = app
    # no consultation: Novo atendimento is the primary action, no status badge
    at = run(patient_id=7)
    assert at.button(key="new_consultation").proto.type == "primary" and not [b for b in at.button if b.key == "continue_consultation"]
    assert ui.badge_md("consultation_open") not in _text(at) and "Nenhum atendimento registrado" in _text(at)
    # own open consultation (newest first, as returned): Continuar atendimento + row "Continuar"
    state["history"][7] = [_consultation(9), _consultation(8, COLLEAGUE), _consultation(3, closed=True, day="2026-10-01")]
    at = run(patient_id=7)
    assert at.button(key="continue_consultation").label == "Continuar atendimento"
    assert at.button(key="new_consultation").proto.type == "secondary"  # still available, not the main action
    rows = [b for b in at.button if b.key.startswith("open_consultation_")]
    assert [(b.key, b.label) for b in rows] == [("open_consultation_9", "Continuar"), ("open_consultation_8", "Abrir"),
                                                ("open_consultation_3", "Abrir")]  # order unchanged
    assert ui.badge_md("consultation_open") in _text(at) and ui.badge_md("consultation_closed") in _text(at)
    # colleague's open consultation only: badge shown, no "Continuar", Novo atendimento stays primary
    state["history"][7] = [_consultation(8, COLLEAGUE), _consultation(3, closed=True)]
    at = run(patient_id=7)
    assert not [b for b in at.button if b.key == "continue_consultation"]
    assert at.button(key="new_consultation").proto.type == "primary" and ui.badge_md("consultation_open") in _text(at)


def test_continuar_opens_the_own_open_consultation_without_creating_one(app, monkeypatch):
    run, state = app
    state["history"][7] = [_consultation(9), _consultation(3, closed=True)]
    opened = {**_consultation(9), "soap": None, "exam_results": [], "documents": []}
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_consultation", lambda self, pid, cid: opened)
    monkeypatch.setattr(api_client.PharmaTechAPI, "previous_exams", lambda self, pid, cid: [])
    at = run(patient_id=7)
    at.button(key="continue_consultation").click().run()
    assert at.session_state["consultation_id"] == 9
    assert "create_consultation" not in state["calls"]  # the stub would have raised


def test_contact_card_shows_dash_for_missing_values_and_keeps_the_editor(app):
    run, state = app
    state["patients"][7]["address"] = "Rua Exemplo, 10"
    at = run(patient_id=7)
    text = _text(at)
    assert "Telefone" in text and "—" in text and "Rua Exemplo, 10" in text
    at.button(key="contact_edit_open").click().run()
    assert at.text_input(key="contact_7_phone").value == "" and at.text_input(key="contact_7_address").value == "Rua Exemplo, 10"
    assert not [b for b in at.button if b.key == "contact_edit_open"]  # hidden while editing
    at.button(key="FormSubmitter:contact_form_7-Cancelar").click().run()
    assert at.button(key="contact_edit_open") is not None


def test_banner_escapes_names_and_establishment(app):
    run, state = app
    state["patients"][7]["full_name"] = XSS
    state["history"][7] = [_consultation(9, {"id": 3, "full_name": XSS, "crf": XSS})]
    at = run(patient_id=7)
    at.session_state["pharmacist"] = {**at.session_state["pharmacist"], "establishment_name": XSS}
    at.run()
    for element in list(at.markdown) + list(at.sidebar.markdown):
        if element.proto.allow_html:
            assert "<script" not in element.value and "<img" not in element.value, element.value
    assert "&lt;script&gt;" in _text(at)


def test_document_caption_uses_the_masked_cpf(app, monkeypatch):
    run, state = app
    consultation = {**_consultation(5), "soap": None, "exam_results": [], "documents": []}
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_consultation", lambda self, pid, cid: consultation)
    monkeypatch.setattr(api_client.PharmaTechAPI, "previous_exams", lambda self, pid, cid: [])
    at = run(patient_id=7, consultation_id=5)
    captions = " ".join(str(c.value) for c in at.caption)
    assert "preenchidos automaticamente" in captions and MASKED.replace("*", "\\*").replace(".", "\\.").replace("-", "\\-") in captions
    assert FULL not in _text(at) and "52998224725" not in _text(at)


# ---- API (real backend): search stays minimal ---------------------------------------------------------

def test_search_response_has_no_contact_fields(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    assert c.patch(f"/patients/{pid}/contact", headers=iso["a1"], json={"phone": "(21) 90000-0000", "address": "Rua X"}).status_code == 200
    found = c.post("/patients/search", headers=iso["a1"], json={"query": ISO_CPF}).json()
    assert [p["id"] for p in found] == [pid]
    assert set(found[0]) == {"id", "full_name", "date_of_birth", "cpf", "created_at", "updated_at"}
    assert c.get(f"/patients/{pid}", headers=iso["a1"]).json()["phone"] == "(21) 90000-0000"  # detail keeps it
