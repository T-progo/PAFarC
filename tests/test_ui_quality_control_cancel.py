"""“Cancelar lançamento” in the quality-control editor: shown only on the user's own draft, asks for
confirmation, “Voltar” changes nothing, blocked while there are unsaved changes; confirming keeps the
record, read-only, as Cancelado. The API is replaced by a stub (the API rules themselves are tested
in test_quality_control*.py)."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
APP = str(FRONTEND / "app.py")
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402  (the same module app.py imports)

CONFIRM = ("Deseja cancelar este lançamento? O registro será mantido no histórico como Cancelado e não poderá mais "
           "ser alterado.")
UNSAVED = "Existem alterações ainda não salvas. Salve as alterações antes de cancelar o lançamento."
NOTICE = ("Registro cancelado. Este lançamento foi preservado no histórico para rastreabilidade e não pode mais ser "
          "alterado.")
EDIT_BUTTONS = {"Salvar rascunho", "Finalizar registro", "Cancelar lançamento"}
RID = 7


@pytest.fixture(autouse=True)
def _identity_unchanged(monkeypatch):
    """The app re-reads the logged-in user on every run; here it answers with the session's own user."""
    import streamlit as st

    monkeypatch.setattr(api_client.PharmaTechAPI, "me", lambda self: dict(st.session_state["pharmacist"]))


def _record(**changes):
    record = {"id": RID, "control_type": "CIQ", "pharmacist_id": 1, "establishment_id": 1,
              "execution_at": "2026-10-20T09:00:00", "finalized_at": None, "cancelled_at": None,
              "cancelled_by_pharmacist_id": None, "nc_event_at": None,
              "responsible_name": "Teste", "responsible_crf": "CRF/RJ 1", "exam_analyte": "Glicose",
              "equipment_methodology": "", "provider_manufacturer": "", "control_lot": "", "control_expiry": "",
              "value_found": "98,5", "unit": "", "target_mean": "", "standard_deviation": "",
              "acceptable_range_min": "", "acceptable_range_max": "", "evaluation_status": None,
              "nc_root_cause": None, "nc_root_cause_other": None, "nc_corrective_action": None,
              "nc_repeat_value": None, "nc_repeat_status": None, "nc_patient_decision": None}
    return {**record, **changes}


@pytest.fixture
def ui(monkeypatch):
    calls = []
    state = {"record": _record()}

    def update(self, rid, data):
        state["record"] = {**state["record"], **data}
        return state["record"]

    def cancel(self, rid):
        calls.append(rid)
        state["record"] = {**state["record"], "cancelled_at": "2026-10-20T09:45:00", "cancelled_by_pharmacist_id": 1}
        return state["record"]

    monkeypatch.setattr(api_client.PharmaTechAPI, "get_quality_control", lambda self, rid: state["record"])
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_quality_controls", lambda self: [state["record"]])
    monkeypatch.setattr(api_client.PharmaTechAPI, "update_quality_control", update)
    monkeypatch.setattr(api_client.PharmaTechAPI, "cancel_quality_control", cancel)
    os.environ["PHARMATECH_API_URL"] = "http://127.0.0.1:9"  # anything not stubbed fails fast

    def open_record(record=None):
        if record is not None:
            state["record"] = record
        at = AppTest.from_file(APP, default_timeout=30)
        at.session_state["token"] = "test-token"
        at.session_state["pharmacist"] = {"id": 1, "full_name": "Teste", "crf": "CRF/RJ 1", "is_admin": False,
                                          "clinical_access": True}
        at.session_state["nav"] = "Controle de Qualidade"
        at.session_state["qc_id"] = RID
        at.run()
        assert not at.exception, at.exception
        return at

    return open_record, calls, state


def _labels(at):
    return [b.label for b in at.button]


def _texts(at):
    return " ".join(str(m.value) for m in at.markdown)


def test_confirming_keeps_the_record_read_only_as_cancelled(ui):
    open_record, calls, state = ui
    at = open_record()
    assert "Cancelar lançamento" in _labels(at)
    at.button(key=f"qc_cancel_{RID}").click().run()
    assert any(w.value == CONFIRM for w in at.warning)
    assert {"Voltar", "Sim, cancelar lançamento"} <= set(_labels(at))
    assert calls == []  # nothing happens before confirmation
    at.button(key=f"qc_cancel_yes_{RID}").click().run()
    assert not at.exception, at.exception
    assert calls == [RID]
    assert [s.value for s in at.success] == ["Lançamento cancelado."]
    # the record stays open, read-only, as Cancelado
    assert at.session_state["qc_id"] == RID
    assert any(w.value == NOTICE for w in at.warning)
    assert "Cancelado" in _texts(at) and "Data do cancelamento" in _texts(at)
    assert not EDIT_BUTTONS & set(_labels(at)) and not at.text_input  # no edit controls
    assert not at.get("download_button")  # no PDF
    assert f"qc_cancel_confirm_{RID}" not in at.session_state


def test_back_changes_nothing(ui):
    open_record, calls, _ = ui
    at = open_record()
    at.button(key=f"qc_cancel_{RID}").click().run()
    at.button(key=f"qc_cancel_no_{RID}").click().run()
    assert calls == []
    assert not any(w.value == CONFIRM for w in at.warning)
    assert at.session_state["qc_id"] == RID
    assert "Cancelar lançamento" in _labels(at) and "Salvar rascunho" in _labels(at)


def test_unsaved_changes_block_cancellation_until_saved(ui):
    open_record, calls, state = ui
    at = open_record()
    at.text_input(key=f"qc_{RID}_exam_analyte").set_value("Ureia digitada").run()
    at.button(key=f"qc_cancel_{RID}").click().run()
    assert any(e.value == UNSAVED for e in at.error)
    assert at.button(key=f"qc_cancel_yes_{RID}").disabled
    at.button(key=f"qc_cancel_no_{RID}").click().run()
    assert calls == []
    assert at.text_input(key=f"qc_{RID}_exam_analyte").value == "Ureia digitada"  # typed value kept
    at.button(key=f"qc_{RID}_save").click().run()  # “Salvar rascunho”
    assert state["record"]["exam_analyte"] == "Ureia digitada"
    at.button(key=f"qc_cancel_{RID}").click().run()
    assert not any(e.value == UNSAVED for e in at.error)
    assert not at.button(key=f"qc_cancel_yes_{RID}").disabled
    at.button(key=f"qc_cancel_yes_{RID}").click().run()
    assert calls == [RID] and state["record"]["exam_analyte"] == "Ureia digitada"


@pytest.mark.parametrize("record", [_record(finalized_at="2026-10-20T09:30:00"), _record(pharmacist_id=2),
                                    _record(cancelled_at="2026-10-20T09:45:00", cancelled_by_pharmacist_id=1)],
                         ids=["finalized", "colleague draft", "cancelled"])
def test_not_offered_outside_the_users_own_draft(ui, record):
    open_record, _, _ = ui
    at = open_record(record)
    assert "Cancelar lançamento" not in _labels(at) and "Salvar rascunho" not in _labels(at)
    assert len(at.get("download_button")) == (1 if record["finalized_at"] else 0)  # “Reimprimir PDF” only once finalized


def test_history_shows_the_cancelled_state(ui):
    open_record, _, state = ui
    at = open_record(_record(cancelled_at="2026-10-20T09:45:00", cancelled_by_pharmacist_id=1))
    del at.session_state["qc_id"]
    at.run()
    history = _texts(at)
    assert "Cancelado" in history and "Glicose" in history and "Em preenchimento" not in history
