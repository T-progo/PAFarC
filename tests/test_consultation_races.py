"""Concurrency: once a consultation's closing commits, no SOAP/exam change may commit to it.
Deterministic interleavings: one request is paused at the write-lock step (just before taking the
lock, or while holding it) while another request runs; database triggers record any clinical write
that lands on an already closed consultation. Fictitious data only."""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend import consultations
from backend.auth import create_pharmacist
from backend.database import get_engine
from backend.main import app

PASSWORD = "senha-segura-1"
SOAP = {"subjective": "S0", "objective": "O0", "assessment": "A0", "plan": "P0"}
EXAM = {"exam_name": "Glicemia", "result": "98", "unit": "mg/dL", "manufacturer": "Fab", "lot": "L1",
        "expiry_date": "2027-01-01"}
ORIGINAL_BEGIN_WRITE = consultations._begin_write
TRIGGERS = """
CREATE TABLE late_writes (tbl TEXT, op TEXT, consultation_id INTEGER);
CREATE TRIGGER lw_soap_i AFTER INSERT ON soap_records
  WHEN (SELECT closed_at FROM consultations WHERE id = NEW.consultation_id) IS NOT NULL
  BEGIN INSERT INTO late_writes VALUES ('soap', 'insert', NEW.consultation_id); END;
CREATE TRIGGER lw_soap_u AFTER UPDATE ON soap_records
  WHEN (SELECT closed_at FROM consultations WHERE id = NEW.consultation_id) IS NOT NULL
  BEGIN INSERT INTO late_writes VALUES ('soap', 'update', NEW.consultation_id); END;
CREATE TRIGGER lw_exam_i AFTER INSERT ON exam_results
  WHEN (SELECT closed_at FROM consultations WHERE id = NEW.consultation_id) IS NOT NULL
  BEGIN INSERT INTO late_writes VALUES ('exam', 'insert', NEW.consultation_id); END;
CREATE TRIGGER lw_exam_u AFTER UPDATE ON exam_results
  WHEN (SELECT closed_at FROM consultations WHERE id = NEW.consultation_id) IS NOT NULL
  BEGIN INSERT INTO late_writes VALUES ('exam', 'update', NEW.consultation_id); END;
CREATE TRIGGER lw_exam_d AFTER DELETE ON exam_results
  WHEN (SELECT closed_at FROM consultations WHERE id = OLD.consultation_id) IS NOT NULL
  BEGIN INSERT INTO late_writes VALUES ('exam', 'delete', OLD.consultation_id); END;
"""


@pytest.fixture
def env(client, db):
    raw = get_engine().raw_connection()
    raw.executescript(TRIGGERS)
    raw.commit()
    raw.close()
    create_pharmacist(db, full_name="Farm Corrida", crf="1", crf_uf="SP", login="corrida@x.test", password=PASSWORD)
    token = client.post("/auth/login", data={"username": "corrida@x.test", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/patients", headers=h, json={"full_name": "Paciente Corrida", "date_of_birth": "1970-01-01",
                                                    "cpf": "52998224725"}).json()["id"]
    yield {"h": h, "pid": pid, "c1": client, "c2": TestClient(app)}
    raw = get_engine().raw_connection()
    raw.execute("DROP TABLE IF EXISTS late_writes")
    raw.commit()
    raw.close()


def late_writes():
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT tbl, op FROM late_writes")).fetchall()


def fresh(env):
    c, h, pid = env["c1"], env["h"], env["pid"]
    cid = c.post(f"/patients/{pid}/consultations", headers=h, json={}).json()["id"]
    base = f"/patients/{pid}/consultations/{cid}"
    assert c.put(f"{base}/soap", headers=h, json=SOAP).status_code == 200
    exam_id = c.post(f"{base}/exams", headers=h, json=EXAM).json()["id"]
    return base, exam_id


class Gate:
    """Pauses the first request that reaches the write-lock step after `arm()`: 'before' = just
    before taking the lock (its access checks already passed), 'after' = while holding it."""

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
    """slow() is paused at the write-lock step while fast() runs; returns (slow, fast) responses."""
    gate = Gate(mode)
    monkeypatch.setattr(consultations, "_begin_write", gate)
    out = {}
    gate.armed = True
    t_slow = threading.Thread(target=lambda: out.__setitem__("slow", slow()))
    t_slow.start()
    assert gate.reached.wait(10), "slow request never reached the lock step"
    if mode == "before":
        out["fast"] = fast()  # runs to completion: the slow request does not hold the lock yet
        gate.go.set()
    else:
        t_fast = threading.Thread(target=lambda: out.__setitem__("fast", fast()))
        t_fast.start()
        assert gate.other_entered.wait(10)  # the fast request is now waiting for the lock
        gate.go.set()
        t_fast.join(30)
    t_slow.join(30)
    monkeypatch.setattr(consultations, "_begin_write", ORIGINAL_BEGIN_WRITE)
    return out["slow"], out["fast"]


MUTATIONS = {
    "soap": (lambda c, h, b, e: c.put(f"{b}/soap", headers=h, json={**SOAP, "plan": "CHANGED"}), 200),
    "exam add": (lambda c, h, b, e: c.post(f"{b}/exams", headers=h, json={**EXAM, "exam_name": "CHANGED"}), 201),
    "exam edit": (lambda c, h, b, e: c.put(f"{b}/exams/{e}", headers=h, json={**EXAM, "result": "CHANGED"}), 200),
    "exam delete": (lambda c, h, b, e: c.delete(f"{b}/exams/{e}", headers=h), 204),
    "kit edit": (lambda c, h, b, e: c.put(f"{b}/exams/{e}", headers=h, json={**EXAM, "lot": "CHANGED"}), 200),
}
CLOSERS = {
    "finalize": (lambda env, base: env["c2"].post(f"{base}/finalize", headers=env["h"]), 200),
    "new consultation": (lambda env, base: env["c2"].post(f"/patients/{env['pid']}/consultations",
                                                          headers=env["h"], json={}), 201),
}


def changed(env, base, kind, exam_id):
    body = env["c1"].get(base, headers=env["h"]).json()
    exams = body["exam_results"]
    return {"soap": body["soap"]["plan"] == "CHANGED",
            "exam add": any(e["exam_name"] == "CHANGED" for e in exams),
            "exam edit": any(e["result"] == "CHANGED" for e in exams),
            "exam delete": not any(e["id"] == exam_id for e in exams),
            "kit edit": any(e["lot"] == "CHANGED" for e in exams)}[kind], body["closed_at"]


@pytest.mark.parametrize("closer", list(CLOSERS))
@pytest.mark.parametrize("kind", list(MUTATIONS))
def test_closing_first_then_mutation_is_rejected(env, monkeypatch, kind, closer):
    """The mutation passed its access checks before the closing committed (the former stale window)."""
    base, exam_id = fresh(env)
    mutate, _ = MUTATIONS[kind]
    close, close_ok = CLOSERS[closer]
    slow, fast = interleave(monkeypatch, "before", lambda: mutate(env["c1"], env["h"], base, exam_id),
                            lambda: close(env, base))
    assert fast.status_code == close_ok
    assert slow.status_code == 409
    assert slow.json()["detail"] == ("Atendimento encerrado. Os dados clínicos deste atendimento são somente "
                                     "para consulta.")
    applied, closed_at = changed(env, base, kind, exam_id)
    assert not applied and closed_at is not None
    assert late_writes() == []


@pytest.mark.parametrize("closer", list(CLOSERS))
@pytest.mark.parametrize("kind", list(MUTATIONS))
def test_mutation_holding_the_lock_commits_before_the_closing(env, monkeypatch, kind, closer):
    """The mutation holds the write lock; the closing waits for it and commits afterwards."""
    base, exam_id = fresh(env)
    mutate, mutate_ok = MUTATIONS[kind]
    close, close_ok = CLOSERS[closer]
    slow, fast = interleave(monkeypatch, "after", lambda: mutate(env["c1"], env["h"], base, exam_id),
                            lambda: close(env, base))
    assert slow.status_code == mutate_ok
    assert fast.status_code == close_ok
    applied, closed_at = changed(env, base, kind, exam_id)
    assert applied and closed_at is not None
    assert late_writes() == []


@pytest.mark.parametrize("mode", ["before", "after"])
def test_finalize_vs_finalize_exactly_one_succeeds(env, monkeypatch, mode):
    base, _ = fresh(env)
    slow, fast = interleave(monkeypatch, mode, lambda: env["c1"].post(f"{base}/finalize", headers=env["h"]),
                            lambda: env["c2"].post(f"{base}/finalize", headers=env["h"]))
    assert sorted([slow.status_code, fast.status_code]) == [200, 409]
    winner = slow if slow.status_code == 200 else fast
    assert env["c1"].get(base, headers=env["h"]).json()["closed_at"] == winner.json()["closed_at"]


@pytest.mark.parametrize("mode", ["before", "after"])
def test_two_new_consultations_leave_exactly_one_open(env, monkeypatch, mode):
    base, _ = fresh(env)
    new = lambda c: c.post(f"/patients/{env['pid']}/consultations", headers=env["h"], json={})
    slow, fast = interleave(monkeypatch, mode, lambda: new(env["c1"]), lambda: new(env["c2"]))
    assert slow.status_code == fast.status_code == 201
    listing = env["c1"].get(f"/patients/{env['pid']}/consultations", headers=env["h"]).json()
    assert sum(c["closed_at"] is None for c in listing) == 1


@pytest.mark.parametrize("closer", list(CLOSERS))
def test_concurrent_stress_no_late_clinical_write(env, closer):
    """Real concurrency (no pauses): 8 mutations racing one closing, repeated; zero late writes, no 5xx."""
    close, _ = CLOSERS[closer]
    codes = []
    for trial in range(10):
        base, exam_id = fresh(env)
        barrier = threading.Barrier(9)
        kinds = list(MUTATIONS)

        def mutate(i):
            barrier.wait()
            fn, _ = MUTATIONS[kinds[i % len(kinds)]]
            codes.append(fn(env["c1"], env["h"], base, exam_id).status_code)

        def closing():
            barrier.wait()
            codes.append(close(env, base).status_code)

        threads = [threading.Thread(target=mutate, args=(i,)) for i in range(8)] + [threading.Thread(target=closing)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert env["c1"].get(base, headers=env["h"]).json()["closed_at"] is not None
    assert late_writes() == []
    assert all(code < 500 for code in codes), codes
    assert set(codes) <= {200, 201, 204, 404, 409}, set(codes)
