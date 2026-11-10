"""UI Phase 2.1: one CPF mask everywhere (***.***.***-NN) and a confirmation before “Novo atendimento” when an
open consultation of the patient was started by ANOTHER pharmacist (the backend then closes it — that rule is
unchanged and stays the backend's). API stubbed (AppTest) except the backend-rule tests at the end."""
import pytest

from tests.test_clinical_isolation import iso, new_patient  # noqa: F401  (fixture)
from tests.test_ui_patient_phase2 import (  # noqa: F401  (fixture; also puts frontend/ on sys.path)
    COLLEAGUE, FULL, MASKED, MASKED8, ME, _consultation, _patient, _text, app,
)

import api_client  # noqa: E402  (the same module app.py imports)
import ui  # noqa: E402

WARNING = ("Já existe um atendimento em andamento para este paciente, iniciado por outro farmacêutico. Ao iniciar "
           "um novo atendimento, o atendimento anterior será encerrado. Deseja continuar?")


@pytest.fixture
def creating(app, monkeypatch):  # noqa: F811
    """The app fixture plus a recording create_consultation and the consultation page it opens."""
    run, state = app
    created = []

    def create(self, pid):
        created.append(pid)
        return {"id": 50}

    opened = {**_consultation(50), "soap": None, "exam_results": [], "documents": []}
    monkeypatch.setattr(api_client.PharmaTechAPI, "create_consultation", create)
    monkeypatch.setattr(api_client.PharmaTechAPI, "get_consultation",
                        lambda self, pid, cid: opened if cid == 50 else {**_consultation(cid, COLLEAGUE), "soap": None,
                                                                         "exam_results": [], "documents": []})
    monkeypatch.setattr(api_client.PharmaTechAPI, "previous_exams", lambda self, pid, cid: [])
    return run, state, created


def _warnings(at):
    return [w.value for w in at.warning]


def _confirm_keys(at):
    return [k for k in at.session_state.keys() if str(k).startswith("new_consultation_confirm_")]


# ---- CPF mask -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, shown", [
    ("52998224725", "***.***.***-25"), ("529.982.247-25", "***.***.***-25"), (" 529 982 247 25 ", "***.***.***-25"),
    ("5299822472599", "***.***.***-99"), ("12", "***.***.***-12"), ("1", "***.***.***-**"), ("", "***.***.***-**"),
    (None, "***.***.***-**"), ("abc", "***.***.***-**"),
])
def test_one_mask_never_shows_more_than_two_digits(value, shown):
    assert ui.mask_cpf_last2(value) == shown
    assert sum(ch.isdigit() for ch in ui.mask_cpf_last2(value)) <= 2


def test_search_profile_and_caption_use_the_same_mask(creating):
    run, state, _ = creating
    state["search"] = [{k: v for k, v in _patient(7).items() if k not in ("phone", "address")}]
    at = run()
    at.text_input(key="search_query").set_value("Paciente").run()
    at.button(key="FormSubmitter:search_form-Buscar").click().run()
    assert MASKED in _text(at).replace("\\", "") and FULL not in _text(at)  # search row
    at.button(key="open_7").click().run()
    assert _text(at).count(MASKED) == 2 and FULL not in _text(at)  # banner + Dados cadastrais
    at.button(key="cpf_toggle_7").click().run()
    assert _text(at).count(FULL) == 2 and MASKED not in _text(at)  # Mostrar CPF unchanged
    at.button(key="cpf_toggle_7").click().run()
    assert _text(at).count(MASKED) == 2 and FULL not in _text(at)  # Ocultar CPF unchanged
    at.button(key="new_consultation").click().run()  # no open consultation: starts normally, opens it
    captions = " ".join(str(c.value) for c in at.caption).replace("\\", "")
    assert at.session_state["consultation_id"] == 50 and MASKED in captions and FULL not in _text(at)


# ---- warning before closing a colleague's open consultation ----------------------------------------------

def test_no_open_consultation_starts_directly(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(3, closed=True)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    assert created == [7] and WARNING not in _warnings(at) and at.session_state["consultation_id"] == 50


def test_own_open_consultation_continues_without_warning(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(9)]
    at = run(patient_id=7)
    assert at.button(key="continue_consultation").proto.type == "primary" and WARNING not in _warnings(at)
    at.button(key="continue_consultation").click().run()
    assert created == [] and at.session_state["consultation_id"] == 9


def test_colleague_open_consultation_asks_first_and_cancel_changes_nothing(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(8, COLLEAGUE), _consultation(3, closed=True)]
    at = run(patient_id=7)
    assert WARNING not in _warnings(at)  # merely viewing never warns
    assert ui.badge_md("consultation_open") in _text(at) and "Colega" in _text(at)  # still shown as in progress
    at.button(key="new_consultation").click().run()
    assert _warnings(at) == [WARNING] and created == []  # first click: nothing created, nothing closed
    assert not [b for b in at.button if b.key == "new_consultation"]  # the warning carries the action meanwhile
    assert _confirm_keys(at) == ["new_consultation_confirm_7"]
    assert {"new_consultation_cancel_7", "new_consultation_yes_7", "open_consultation_8"} <= {b.key for b in at.button}
    at.button(key="new_consultation_cancel_7").click().run()
    assert WARNING not in _warnings(at) and created == [] and _confirm_keys(at) == []
    assert ui.badge_md("consultation_open") in _text(at)  # the colleague's consultation is still open


def test_confirm_creates_exactly_once(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="new_consultation_yes_7").click().run()
    assert created == [7] and at.session_state["consultation_id"] == 50 and _confirm_keys(at) == []
    assert not [b for b in at.button if str(b.key).startswith("new_consultation_yes_")]  # cannot be clicked twice
    at.run()  # further reruns create nothing more
    assert created == [7]


def test_colleague_consultation_stays_readable_before_confirming(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="open_consultation_8").click().run()  # read-only access is untouched
    assert at.session_state["consultation_id"] == 8 and created == []
    assert any("Somente leitura" in i.value for i in at.info)


def test_own_and_colleague_open_warns_only_on_the_secondary_new_consultation(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(9), _consultation(8, COLLEAGUE)]  # inconsistent legacy data
    at = run(patient_id=7)
    assert at.button(key="continue_consultation").proto.type == "primary" and WARNING not in _warnings(at)
    at.button(key="new_consultation").click().run()
    assert _warnings(at) == [WARNING] and created == []  # it would close the colleague's one as well


def test_confirmation_is_per_patient_and_cleared_with_the_patient(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.session_state["patient_id"] = 8  # another patient in the same session: not asked there
    at.run()
    assert WARNING not in _warnings(at)
    at.button(key="back").click().run()  # close_patient()
    assert _confirm_keys(at) == [] and created == []


def test_logout_clears_the_confirmation(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="logout").click().run()
    assert _confirm_keys(at) == [] and "token" not in at.session_state and created == []


def test_establishment_change_while_asking_creates_nothing(creating):
    run, state, created = creating
    state["history"][7] = [_consultation(8, COLLEAGUE)]
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    state["me"] = {**at.session_state["pharmacist"], "establishment_id": 2, "establishment_name": "Farmácia B"}
    at.button(key="new_consultation_yes_7").click().run()
    assert created == [] and "patient_id" not in at.session_state and _confirm_keys(at) == []


def test_create_404_drops_the_stale_patient(creating, monkeypatch):
    run, state, _ = creating
    state["history"][7] = [_consultation(8, COLLEAGUE)]

    def gone(self, pid):
        raise api_client.ApiError(404, "Paciente não encontrado.")

    monkeypatch.setattr(api_client.PharmaTechAPI, "create_consultation", gone)
    at = run(patient_id=7)
    at.button(key="new_consultation").click().run()
    at.button(key="new_consultation_yes_7").click().run()
    assert "patient_id" not in at.session_state and any("não encontrado" in e.value for e in at.error)


# ---- backend rule unchanged (real API) -----------------------------------------------------------------

def test_backend_closes_the_colleagues_open_consultation_and_keeps_access_rules(iso):  # noqa: F811
    c = iso["c"]
    pid = new_patient(iso, iso["a1"])
    col = c.post(f"/patients/{pid}/consultations", headers=iso["a2"], json={}).json()["id"]
    assert c.post(f"/patients/{pid}/consultations", headers=iso["b1"], json={}).status_code == 404  # other establishment
    assert c.post(f"/patients/{pid}/consultations", headers=iso["admin"], json={}).status_code == 403
    assert c.post(f"/patients/{pid}/consultations", json={}).status_code == 401
    assert c.get(f"/patients/{pid}/consultations/{col}", headers=iso["a2"]).json()["closed_at"] is None  # untouched so far
    assert c.get(f"/patients/{pid}/consultations/{col}", headers=iso["a1"]).status_code == 200  # colleague can read it
    new = c.post(f"/patients/{pid}/consultations", headers=iso["a1"], json={}).json()["id"]
    history = c.get(f"/patients/{pid}/consultations", headers=iso["a1"]).json()
    assert [h["id"] for h in history if h["closed_at"] is None] == [new]  # exactly one open: the new one
    assert len(history) == 2
