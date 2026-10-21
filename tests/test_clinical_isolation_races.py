"""A pharmacist moved to another establishment while one of their writes is in flight: the write
re-checks access under the write lock, so a transfer committed first is seen and nothing is written
to the old establishment (404); a write already holding the lock commits before the transfer.
Fictitious data only."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend import consultations, documents
from backend.database import get_engine
from backend.main import app
from tests.test_clinical_isolation import EXAM, SOAP, iso, new_patient  # noqa: F401  (fixture)

ORIGINAL = consultations._begin_write


class PauseBeforeLock:
    """Pauses the first request reaching the write-lock step, after its access checks passed."""

    def __init__(self, hold_lock: bool):
        self.hold_lock, self.armed = hold_lock, True
        self.reached, self.go = threading.Event(), threading.Event()

    def __call__(self, db):
        mine, self.armed = self.armed, False
        if not mine:
            return ORIGINAL(db)
        if self.hold_lock:
            ORIGINAL(db)
        self.reached.set()
        assert self.go.wait(10)
        if not self.hold_lock:
            ORIGINAL(db)


def run_with_transfer(monkeypatch, iso, request, hold_lock=False):  # noqa: F811
    gate = PauseBeforeLock(hold_lock)
    monkeypatch.setattr(consultations, "_begin_write", gate)
    monkeypatch.setattr(documents, "_begin_write", gate)
    out = {}
    worker = threading.Thread(target=lambda: out.setdefault("write", request(TestClient(app))))
    worker.start()
    assert gate.reached.wait(10)
    if hold_lock:  # the transfer waits for the lock held by the write
        mover = threading.Thread(target=lambda: iso["move"]("a1@iso.test", "B"))
        mover.start()
        time.sleep(0.5)
        gate.go.set()
        mover.join(30)
    else:  # the transfer commits while the write waits to take the lock
        iso["move"]("a1@iso.test", "B")
        gate.go.set()
    worker.join(30)
    return out["write"]


def counts():
    with get_engine().connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                for t in ("consultations", "soap_records", "exam_results", "generated_documents")}


def _open(iso):  # noqa: F811
    pid = new_patient(iso, iso["a1"])
    cid = iso["c"].post(f"/patients/{pid}/consultations", headers=iso["a1"], json={}).json()["id"]
    return pid, cid


WRITES = {
    "soap": lambda h, pid, cid: lambda c: c.put(f"/patients/{pid}/consultations/{cid}/soap", headers=h, json=SOAP),
    "exam": lambda h, pid, cid: lambda c: c.post(f"/patients/{pid}/consultations/{cid}/exams", headers=h, json=EXAM),
    "consultation": lambda h, pid, cid: lambda c: c.post(f"/patients/{pid}/consultations", headers=h, json={}),
    "document": lambda h, pid, cid: lambda c: c.post(f"/patients/{pid}/consultations/{cid}/documents/referral", headers=h,
                                                     json={"destination": "D", "case_summary": "R", "prm": "P",
                                                           "suggested_conduct": "C"}),
}


@pytest.mark.parametrize("kind", list(WRITES))
def test_transfer_committed_first_blocks_the_stale_write(iso, monkeypatch, kind):  # noqa: F811
    pid, cid = _open(iso)
    before = counts()
    r = run_with_transfer(monkeypatch, iso, WRITES[kind](iso["a1"], pid, cid))
    assert r.status_code == 404, r.text
    assert counts() == before  # nothing written to the old establishment
    assert iso["c"].get(f"/patients/{pid}/consultations/{cid}", headers=iso["a2"]).json()["soap"] is None


@pytest.mark.parametrize("kind", list(WRITES))
def test_write_holding_the_lock_commits_before_the_transfer(iso, monkeypatch, kind):  # noqa: F811
    pid, cid = _open(iso)
    before = counts()
    r = run_with_transfer(monkeypatch, iso, WRITES[kind](iso["a1"], pid, cid), hold_lock=True)
    assert r.status_code in (200, 201), r.text
    assert sum(counts().values()) == sum(before.values()) + 1
    # the transfer then applied: the old establishment is no longer reachable
    assert iso["c"].get(f"/patients/{pid}", headers=iso["a1"]).status_code == 404
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM consultations c JOIN patients p ON p.id = c.patient_id "
                                 "WHERE c.establishment_id != p.establishment_id")).scalar() == 0
