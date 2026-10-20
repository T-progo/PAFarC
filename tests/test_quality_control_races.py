"""Concurrency of quality-control drafts and finalization: once finalization commits, no change may
commit to the record. Same deterministic technique as the consultation races: one request is paused
at the write-lock step (just before taking it, or while holding it) while another runs; a trigger
records any update to an already finalized record. Fictitious data only."""
import threading
from io import BytesIO

import pytest
from pypdf import PdfReader
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend import consultations
from backend.auth import create_pharmacist
from backend.database import get_engine
from backend.main import app
from backend.models import ROLE_ADMINISTRATOR

PASSWORD = "senha-segura-1"
BASE = {"control_type": "CIQ", "exam_analyte": "Glicose", "equipment_methodology": "Fotometria",
        "provider_manufacturer": "Fab", "control_lot": "L1", "control_expiry": "03/2027", "value_found": "98",
        "unit": "mg/dL", "target_mean": "100", "standard_deviation": "3", "acceptable_range_min": "94",
        "acceptable_range_max": "106", "evaluation_status": "APPROVED"}
NC = {"nc_root_cause": "CALIBRATION", "nc_corrective_action": "Recalibrado.", "nc_repeat_value": "101",
      "nc_repeat_status": "APPROVED", "nc_patient_decision": "APPROVED_FOR_PATIENT_TESTING"}
REJECTED = {**BASE, "value_found": "121", "evaluation_status": "REJECTED", **NC}
ORIGINAL_BEGIN_WRITE = consultations._begin_write


@pytest.fixture
def env(client, db):
    raw = get_engine().raw_connection()
    raw.executescript("""
        CREATE TABLE qc_late_writes (record_id INTEGER);
        CREATE TRIGGER qc_lw AFTER UPDATE ON quality_controls WHEN OLD.finalized_at IS NOT NULL
          BEGIN INSERT INTO qc_late_writes VALUES (NEW.id); END;
    """)
    raw.commit()
    raw.close()
    create_pharmacist(db, full_name="Admin", crf="", login="adm@qcr.test", password=PASSWORD, role=ROLE_ADMINISTRATOR)
    token = lambda u: client.post("/auth/login", data={"username": u, "password": PASSWORD}).json()["access_token"]
    admin = {"Authorization": f"Bearer {token('adm@qcr.test')}"}
    est = client.post("/admin/establishments", headers=admin, json={"name": "Farmácia Corrida QC"}).json()["id"]
    client.post("/admin/users", headers=admin, json={"full_name": "Farm Corrida QC", "crf": "1", "crf_uf": "SP",
                                                     "login": "f@qcr.test", "password": PASSWORD, "establishment_id": est})
    yield {"h": {"Authorization": f"Bearer {token('f@qcr.test')}"}, "c1": client, "c2": TestClient(app)}
    raw = get_engine().raw_connection()
    raw.execute("DROP TABLE IF EXISTS qc_late_writes")
    raw.commit()
    raw.close()


def late_writes():
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT record_id FROM qc_late_writes")).fetchall()


class Gate:
    """Pauses the first request reaching the write-lock step after arming: 'before' = just before
    taking the lock (its access checks already passed), 'after' = while holding it."""

    def __init__(self, mode):
        self.mode, self.armed = mode, False
        self.lock = threading.Lock()
        self.reached, self.go, self.other_entered = threading.Event(), threading.Event(), threading.Event()

    def __call__(self, db):
        with self.lock:
            mine, self.armed = self.armed, False
        if not mine:
            self.other_entered.set()
            return ORIGINAL_BEGIN_WRITE(db)
        if self.mode == "before":
            self.reached.set()
            assert self.go.wait(10)
            return ORIGINAL_BEGIN_WRITE(db)
        ORIGINAL_BEGIN_WRITE(db)
        self.reached.set()
        assert self.go.wait(10)


def interleave(monkeypatch, mode, slow, fast):
    gate = Gate(mode)
    monkeypatch.setattr(consultations, "_begin_write", gate)
    out = {}
    gate.armed = True
    t_slow = threading.Thread(target=lambda: out.__setitem__("slow", slow()))
    t_slow.start()
    assert gate.reached.wait(10), "slow request never reached the lock step"
    if mode == "before":
        out["fast"] = fast()
        gate.go.set()
    else:
        t_fast = threading.Thread(target=lambda: out.__setitem__("fast", fast()))
        t_fast.start()
        assert gate.other_entered.wait(10)
        gate.go.set()
        t_fast.join(30)
    t_slow.join(30)
    monkeypatch.setattr(consultations, "_begin_write", ORIGINAL_BEGIN_WRITE)
    return out["slow"], out["fast"]


def create(env, data):
    return env["c1"].post("/quality-controls", headers=env["h"], json=data).json()["id"]


finalize = lambda env, c, rid: c.post(f"/quality-controls/{rid}/finalize", headers=env["h"])
EDITS = {  # name -> (initial record, change)
    "draft update": (BASE, {**BASE, "value_found": "99,9"}),
    "status change": (BASE, {**BASE, "evaluation_status": "REJECTED", **NC}),
    "non-conformity edit": (REJECTED, {**REJECTED, "nc_corrective_action": "Ação corretiva ALTERADA."}),
}


@pytest.mark.parametrize("mode", ["before", "after"])
def test_finalize_vs_finalize(env, monkeypatch, mode):
    rid = create(env, BASE)
    slow, fast = interleave(monkeypatch, mode, lambda: finalize(env, env["c1"], rid), lambda: finalize(env, env["c2"], rid))
    assert sorted([slow.status_code, fast.status_code]) == [200, 409]
    winner = slow if slow.status_code == 200 else fast
    record = env["c1"].get(f"/quality-controls/{rid}", headers=env["h"]).json()
    assert record["finalized_at"] == winner.json()["finalized_at"]
    pdf = env["c1"].get(f"/quality-controls/{rid}/pdf", headers=env["h"]).content
    assert pdf.startswith(b"%PDF-") and env["c2"].get(f"/quality-controls/{rid}/pdf", headers=env["h"]).content == pdf
    assert late_writes() == []


@pytest.mark.parametrize("name", list(EDITS))
def test_finalization_first_then_edit_is_rejected(env, monkeypatch, name):
    initial, change = EDITS[name]
    rid = create(env, initial)
    before = env["c1"].get(f"/quality-controls/{rid}", headers=env["h"]).json()
    slow, fast = interleave(monkeypatch, "before",
                            lambda: env["c1"].put(f"/quality-controls/{rid}", headers=env["h"], json=change),
                            lambda: finalize(env, env["c2"], rid))
    assert fast.status_code == 200 and slow.status_code == 409
    after = env["c1"].get(f"/quality-controls/{rid}", headers=env["h"]).json()
    assert {k: v for k, v in after.items() if k not in ("finalized_at", "updated_at")} == \
        {k: v for k, v in before.items() if k not in ("finalized_at", "updated_at")}
    assert late_writes() == []


@pytest.mark.parametrize("name", list(EDITS))
def test_edit_holding_the_lock_commits_before_finalization(env, monkeypatch, name):
    initial, change = EDITS[name]
    rid = create(env, initial)
    slow, fast = interleave(monkeypatch, "after",
                            lambda: env["c1"].put(f"/quality-controls/{rid}", headers=env["h"], json=change),
                            lambda: finalize(env, env["c2"], rid))
    assert slow.status_code == 200
    record = env["c1"].get(f"/quality-controls/{rid}", headers=env["h"]).json()
    # finalization waited for the change and validated the record as changed (incl. a new rejected status)
    assert fast.status_code == 200 and record["finalized_at"]
    pdf = env["c1"].get(f"/quality-controls/{rid}/pdf", headers=env["h"]).content
    t = " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(BytesIO(pdf)).pages)
    expected = {"draft update": "Valor encontrado: 99,9", "status change": "REJEITADO / INADEQUADO",
                "non-conformity edit": "Ação corretiva ALTERADA."}[name]
    assert expected in t  # the PDF reflects the change that committed first
    assert late_writes() == []


def test_concurrent_stress_no_write_after_finalization(env):
    codes = []
    for _ in range(10):
        rid = create(env, REJECTED)
        barrier = threading.Barrier(7)

        def edit(i):
            barrier.wait()
            codes.append(env["c1"].put(f"/quality-controls/{rid}", headers=env["h"],
                                       json={**REJECTED, "nc_corrective_action": f"Ação {i}"}).status_code)

        def fin():
            barrier.wait()
            codes.append(finalize(env, env["c2"], rid).status_code)

        threads = [threading.Thread(target=edit, args=(i,)) for i in range(6)] + [threading.Thread(target=fin)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert env["c1"].get(f"/quality-controls/{rid}", headers=env["h"]).json()["finalized_at"]
    assert late_writes() == []
    assert set(codes) <= {200, 409}, set(codes)


# --- "Cancelar lançamento" against finalization / editing / a second cancel --------------------

cancel = lambda env, c, rid: c.post(f"/quality-controls/{rid}/cancel", headers=env["h"])


def row_count(rid):
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM quality_controls WHERE id = :i"), {"i": rid}).scalar()


@pytest.mark.parametrize("mode", ["before", "after"])
@pytest.mark.parametrize("slow_is", ["cancel", "finalize"])
def test_cancel_vs_finalize_exactly_one_wins(env, monkeypatch, mode, slow_is):
    rid = create(env, REJECTED)
    do_cancel, do_finalize = lambda: cancel(env, env["c1"], rid), lambda: finalize(env, env["c2"], rid)
    slow, fast = interleave(monkeypatch, mode, *((do_cancel, do_finalize) if slow_is == "cancel" else (do_finalize, do_cancel)))
    cancel_r, finalize_r = (slow, fast) if slow_is == "cancel" else (fast, slow)
    # "before": the fast request takes the lock first; "after": the slow one already holds it
    cancel_won = (slow_is == "cancel") == (mode == "after")
    if cancel_won:
        assert cancel_r.status_code == 204 and finalize_r.status_code == 404
        assert row_count(rid) == 0
    else:
        assert finalize_r.status_code == 200 and cancel_r.status_code == 409
        assert row_count(rid) == 1
        record = env["c1"].get(f"/quality-controls/{rid}", headers=env["h"]).json()
        assert record["finalized_at"] == finalize_r.json()["finalized_at"]
        assert env["c1"].get(f"/quality-controls/{rid}/pdf", headers=env["h"]).content.startswith(b"%PDF-")
    assert late_writes() == []


@pytest.mark.parametrize("mode", ["before", "after"])
def test_cancel_vs_cancel(env, monkeypatch, mode):
    rid = create(env, BASE)
    slow, fast = interleave(monkeypatch, mode, lambda: cancel(env, env["c1"], rid), lambda: cancel(env, env["c2"], rid))
    assert sorted([slow.status_code, fast.status_code]) == [204, 404]
    assert row_count(rid) == 0


def test_cancel_holding_the_lock_then_edit_finds_nothing(env, monkeypatch):
    rid = create(env, BASE)
    slow, fast = interleave(monkeypatch, "after", lambda: cancel(env, env["c1"], rid),
                            lambda: env["c2"].put(f"/quality-controls/{rid}", headers=env["h"], json={**BASE, "value_found": "1"}))
    assert slow.status_code == 204 and fast.status_code == 404
    assert row_count(rid) == 0


def test_concurrent_stress_cancel_and_finalize(env):
    codes = []
    for _ in range(10):
        rid = create(env, BASE)
        barrier = threading.Barrier(4)

        def run(fn):
            barrier.wait()
            codes.append(fn().status_code)

        threads = [threading.Thread(target=run, args=(f,)) for f in (
            lambda: cancel(env, env["c1"], rid), lambda: cancel(env, env["c2"], rid),
            lambda: finalize(env, env["c1"], rid), lambda: finalize(env, env["c2"], rid))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        record = env["c1"].get(f"/quality-controls/{rid}", headers=env["h"])
        # either gone, or finalized with its PDF: never a half state
        assert record.status_code == 404 or (record.json()["finalized_at"] and
                                             env["c1"].get(f"/quality-controls/{rid}/pdf", headers=env["h"]).status_code == 200)
    assert set(codes) <= {200, 204, 404, 409}, set(codes)
    assert late_writes() == []
