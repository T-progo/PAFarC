"""“Cancelar lançamento” in the quality-control editor: shown only on the user's own draft, asks for
confirmation, “Voltar” changes nothing, confirming discards the draft and returns to the QC screen.
The API is replaced by a stub (the API rules themselves are tested in test_quality_control*.py)."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
APP = str(FRONTEND / "app.py")
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402  (the same module app.py imports)

CONFIRM = "Deseja cancelar este lançamento? Os dados preenchidos neste registro serão descartados."
RID = 7


def _record(**changes):
    record = {"id": RID, "control_type": "CIQ", "pharmacist_id": 1, "establishment_id": 1,
              "execution_at": "2026-10-20T09:00:00", "finalized_at": None, "nc_event_at": None,
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
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_quality_control", lambda self, rid: state["record"])
    monkeypatch.setattr(api_client.PharmaTechAPI, "list_quality_controls", lambda self: [])
    monkeypatch.setattr(api_client.PharmaTechAPI, "cancel_quality_control", lambda self, rid: calls.append(rid))
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

    return open_record, calls


def _labels(at):
    return [b.label for b in at.button]


def test_confirming_discards_the_draft_and_returns_to_the_qc_screen(ui):
    open_record, calls = ui
    at = open_record()
    assert "Cancelar lançamento" in _labels(at)
    at.button(key=f"qc_cancel_{RID}").click().run()
    assert any(w.value == CONFIRM for w in at.warning)
    assert {"Voltar", "Sim, cancelar lançamento"} <= set(_labels(at))
    assert calls == []  # nothing happens before confirmation
    at.button(key=f"qc_cancel_yes_{RID}").click().run()
    assert not at.exception, at.exception
    assert calls == [RID]
    assert "qc_id" not in at.session_state
    assert [s.value for s in at.success] == ["Lançamento cancelado."]
    assert [t.label for t in at.tabs] == ["Novo registro", "Histórico"]
    assert f"qc_cancel_confirm_{RID}" not in at.session_state


def test_back_changes_nothing(ui):
    open_record, calls = ui
    at = open_record()
    at.text_input(key=f"qc_{RID}_exam_analyte").set_value("Ureia digitada").run()
    at.button(key=f"qc_cancel_{RID}").click().run()
    at.button(key=f"qc_cancel_no_{RID}").click().run()
    assert calls == []
    assert not any(w.value == CONFIRM for w in at.warning)
    assert at.session_state["qc_id"] == RID
    assert at.text_input(key=f"qc_{RID}_exam_analyte").value == "Ureia digitada"  # typed value kept
    assert "Cancelar lançamento" in _labels(at)


@pytest.mark.parametrize("record", [_record(finalized_at="2026-10-20T09:30:00"), _record(pharmacist_id=2)],
                         ids=["finalized", "colleague draft"])
def test_not_offered_outside_the_users_own_draft(ui, record):
    open_record, _ = ui
    at = open_record(record)
    assert "Cancelar lançamento" not in _labels(at)
