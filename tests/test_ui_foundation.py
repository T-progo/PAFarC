"""UI redesign Phase 1 (visual foundation): helpers escape user text, the status vocabulary is fixed,
the sidebar shows the user's identity and current establishment, administrators get only the
administration menu, empty states and page headers render. The API is stubbed (AppTest)."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
APP = str(FRONTEND / "app.py")
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402  (the same module app.py imports)
import ui  # noqa: E402

XSS = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'


def _no_markup(text: str) -> bool:
    return "<script" not in text and "<img" not in text and "&lt;script&gt;" in text and "&lt;img" in text


# ---- helpers (no Streamlit run) ---------------------------------------------------------------------

def test_html_helpers_escape_user_text():
    assert _no_markup(ui.page_header_html(XSS, eyebrow=XSS, meta=XSS))
    assert ui.page_header_html(XSS, XSS, XSS).count("&lt;script&gt;") == 3
    assert _no_markup(ui.empty_state_html(XSS, XSS))
    assert _no_markup(ui.field_html(XSS, XSS))
    identity = ui.identity_html({"full_name": XSS, "crf": XSS, "establishment_name": XSS, "is_admin": False})
    assert _no_markup(identity) and identity.count("&lt;script&gt;") == 3
    assert "&quot;&gt;" in ui.field_html("x", '">')  # attribute-breaking quotes escaped too


def test_identity_block_by_role():
    pharmacist = ui.identity_html({"full_name": "Farm", "crf": "CRF/RJ 1", "establishment_name": "Farmácia A",
                                   "is_admin": False})
    assert "CRF/RJ 1" in pharmacist and "Farmácia A" in pharmacist and "Estabelecimento" in pharmacist
    unassigned = ui.identity_html({"full_name": "Farm", "crf": "CRF/RJ 1", "establishment_name": None, "is_admin": False})
    assert "Sem estabelecimento vinculado" in unassigned
    admin = ui.identity_html({"full_name": "Adm", "crf": "", "establishment_name": "Farmácia A", "is_admin": True})
    assert "Administrador" in admin and "Estabelecimento" not in admin and "Farmácia A" not in admin


def test_status_vocabulary():
    assert ui.consultation_status({"closed_at": None}) == "consultation_open"
    assert ui.consultation_status({"closed_at": "2026-11-10T10:00:00"}) == "consultation_closed"
    draft = {"finalized_at": None, "cancelled_at": None}
    assert ui.qc_lifecycle_status(draft) == "qc_draft"
    assert ui.qc_lifecycle_status({**draft, "finalized_at": "x"}) == "qc_finalized"
    assert ui.qc_lifecycle_status({**draft, "cancelled_at": "x"}) == "qc_cancelled"
    assert ui.qc_evaluation_status({"evaluation_status": "APPROVED"}) == "qc_approved"
    assert ui.qc_evaluation_status({"evaluation_status": "REJECTED"}) == "qc_rejected"
    assert ui.qc_evaluation_status({"evaluation_status": None}) == "qc_not_evaluated"
    assert ui.account_status({"active": True}) == "account_active"
    assert ui.account_status({"active": False}) == "account_blocked"
    labels = {key: label for key, (label, _, _) in ui.STATUS.items()}
    assert (labels["consultation_open"], labels["consultation_closed"]) == ("Em andamento", "Encerrado")
    assert (labels["qc_draft"], labels["qc_finalized"], labels["qc_cancelled"]) == ("Em preenchimento", "Finalizado", "Cancelado")
    assert (labels["account_active"], labels["account_blocked"]) == ("Ativo", "Bloqueado")
    # lifecycle and evaluation are two different badges, never the same words
    assert {labels["qc_draft"], labels["qc_finalized"], labels["qc_cancelled"]}.isdisjoint(
        {labels["qc_approved"], labels["qc_rejected"], labels["qc_not_evaluated"]})
    for key, (label, color, icon) in ui.STATUS.items():  # text + icon always, colour never alone
        assert label.strip() and icon.startswith(":material/") and "[" not in label and "]" not in label, key
        assert color in {"red", "orange", "yellow", "blue", "green", "violet", "gray"}, key
        assert ui.badge_md(key) == f":{color}-badge[{icon} {label}]"


def test_css_uses_stable_selectors_and_keeps_focus():
    css = ui.CSS
    assert "emotion-cache" not in css and "st-emotion" not in css
    assert "outline: none" not in css and "outline:none" not in css and "outline: 0" not in css
    assert ":focus-visible" in css and "gradient" not in css


# ---- app (AppTest, API stubbed) --------------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    import streamlit as st

    monkeypatch.setattr(api_client.PharmaTechAPI, "me", lambda self: dict(st.session_state["pharmacist"]))
    os.environ["PHARMATECH_API_URL"] = "http://127.0.0.1:9"  # anything not stubbed fails fast

    def run(user: dict, **state):
        at = AppTest.from_file(APP, default_timeout=30)
        at.session_state["token"] = "t"
        at.session_state["pharmacist"] = {"id": 1, "full_name": "Farm", "crf": "CRF/RJ 1", "is_admin": False,
                                          "clinical_access": True, "establishment_id": 1,
                                          "establishment_name": "Farmácia A", **user}
        for key, value in state.items():
            at.session_state[key] = value
        at.run()
        assert not at.exception, at.exception
        return at

    return run


def _texts(elements) -> str:
    return " ".join(str(m.value) for m in elements)


def _assert_never_raw_html(at) -> None:
    """Text reaching an element that renders HTML is escaped; elsewhere Streamlit renders raw HTML as text."""
    for element in list(at.markdown) + list(at.sidebar.markdown):
        if element.proto.allow_html:
            assert "<script" not in element.value and "<img" not in element.value, element.value


def test_sidebar_shows_identity_and_establishment_escaped(app):
    at = app({"full_name": XSS, "establishment_name": XSS})
    sidebar = _texts(at.sidebar.markdown)
    assert _no_markup(sidebar) and "Estabelecimento" in sidebar and "CRF/RJ 1" in sidebar
    _assert_never_raw_html(at)
    assert at.radio(key="nav").label == "Clínico"
    assert "## PharmaTech" not in _texts(at.markdown)  # no repeated global heading in the page body


def test_administrator_sidebar_has_no_clinical_items(app):
    at = app({"is_admin": True, "clinical_access": False, "crf": "", "establishment_id": None, "establishment_name": None})
    sidebar = _texts(at.sidebar.markdown)
    assert "Administrador" in sidebar and "Estabelecimento" not in sidebar
    nav = at.radio(key="nav")
    assert nav.label == "Administração" and nav.options == ["Usuários", "Estabelecimentos"]


def test_patient_header_escapes_name_and_consultation_badges(app, monkeypatch):
    patient = {"id": 7, "full_name": XSS, "date_of_birth": "1970-01-15", "cpf": "52998224725", "phone": None,
               "address": None, "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}
    pharm = {"id": 1, "full_name": "Farm", "crf": "CRF/RJ 1"}
    history = [{"id": 2, "patient_id": 7, "consultation_date": "2026-11-10", "pharmacist": pharm, "closed_at": None,
                "created_at": "x", "updated_at": "x"},
               {"id": 1, "patient_id": 7, "consultation_date": "2026-10-01", "pharmacist": pharm,
                "closed_at": "2026-10-01T12:00:00", "created_at": "x", "updated_at": "x"}]
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_patient", lambda self, pid: patient)
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_consultations", lambda self, pid: history)
    at = app({}, patient_id=7)
    body = _texts(at.markdown)
    assert _no_markup(body)
    _assert_never_raw_html(at)
    assert ui.badge_md("consultation_open") in body and ui.badge_md("consultation_closed") in body
    assert {"open_consultation_1", "open_consultation_2", "contact_edit_open", "back"} <= {b.key for b in at.button}


def test_empty_states(app, monkeypatch):
    monkeypatch.setattr(api_client.PharmaTechAPI, "search_patients", lambda self, q: [])
    at = app({})
    at.text_input(key="search_query").set_value("ninguem").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    body = _texts(at.markdown)
    assert "pt-empty" in body and "Nenhum paciente encontrado" in body
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_quality_controls", lambda self: [])
    at = app({}, nav="Controle de Qualidade")
    assert "Nenhum registro de controle de qualidade" in _texts(at.markdown)


def test_qc_history_shows_lifecycle_and_evaluation_as_two_badges(app, monkeypatch):
    record = {"id": 3, "control_type": "CIQ", "execution_at": "2026-10-20T09:00:00", "exam_analyte": XSS,
              "evaluation_status": "REJECTED", "responsible_name": "Farm", "finalized_at": "2026-10-20T10:00:00",
              "cancelled_at": None}
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_quality_controls", lambda self: [record])
    at = app({}, nav="Controle de Qualidade")
    body = _texts(at.markdown)
    assert ui.badge_md("qc_rejected") in body and ui.badge_md("qc_finalized") in body
    _assert_never_raw_html(at)  # the free text in the row stays Markdown-escaped, in an element without HTML
    assert r"\<script\>" in body
    assert "qc_open_3" in {b.key for b in at.button}
