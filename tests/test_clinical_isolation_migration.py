"""One-time migration of a database from before clinical establishment ownership (layout of e87c1a3):
deterministic, atomic, idempotent; never guesses (unresolved records stop it with nothing changed);
patients attended in two establishments are split into independent records. Fictitious data only."""
import hashlib
import sqlite3

import pytest
from sqlalchemy import inspect, text

from backend import clinical_isolation
from backend.auth import create_pharmacist
from backend.clinical_isolation import ClinicalOwnershipError, _fingerprint
from backend.crypto import get_crypto
from backend.database import get_engine, init_db
from backend.establishments import create_establishment
from backend.patients import CPF_INDEX_PURPOSE, NAME_INDEX_PURPOSE, name_prefix_indexes, normalize_name

PASSWORD = "senha-segura-1"
LEGACY_DDL = [
    """CREATE TABLE patients (
        id INTEGER NOT NULL, full_name_encrypted TEXT NOT NULL, full_name_index VARCHAR(64) NOT NULL,
        date_of_birth_encrypted TEXT NOT NULL, cpf_encrypted TEXT NOT NULL, cpf_index VARCHAR(64) NOT NULL,
        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (cpf_index))""",
    "CREATE INDEX ix_patients_full_name_index ON patients (full_name_index)",
    """CREATE TABLE consultations (
        id INTEGER NOT NULL, patient_id INTEGER NOT NULL, pharmacist_id INTEGER NOT NULL, consultation_date DATE NOT NULL,
        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, closed_at DATETIME, PRIMARY KEY (id),
        FOREIGN KEY(patient_id) REFERENCES patients (id), FOREIGN KEY(pharmacist_id) REFERENCES pharmacists (id))""",
    "CREATE INDEX ix_consultations_patient_id ON consultations (patient_id)",
    "CREATE INDEX ix_consultations_pharmacist_id ON consultations (pharmacist_id)",
    """CREATE TABLE generated_documents (
        id INTEGER NOT NULL, consultation_id INTEGER NOT NULL, pharmacist_id INTEGER NOT NULL,
        document_type VARCHAR(30) NOT NULL, created_at DATETIME NOT NULL, pdf_encrypted BLOB, pdf_size INTEGER,
        PRIMARY KEY (id), FOREIGN KEY(consultation_id) REFERENCES consultations (id),
        FOREIGN KEY(pharmacist_id) REFERENCES pharmacists (id))""",
    "CREATE INDEX ix_generated_documents_consultation_id ON generated_documents (consultation_id)",
]
PATIENTS = {  # id: (name, date of birth, CPF)
    1: ("Paciente Só Alfa", "1960-05-01", "52998224725"),
    2: ("Paciente Compartilhado Exemplo", "1971-07-15", "11144477735"),
    3: ("Paciente Sem Atendimento", "1985-02-20", "39053344705"),
}


def _direct():
    """A dedicated connection (not from the pool): these helpers rebuild tables the pooled
    connections may have compiled statements for."""
    return sqlite3.connect(get_engine().url.database)


def make_legacy_layout():
    """Turn the (empty) clinical tables into the layout of e87c1a3: no ownership, CPF globally unique."""
    raw = _direct()
    raw.execute("PRAGMA foreign_keys=OFF")
    for (trigger,) in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        raw.execute(f"DROP TRIGGER {trigger}")
    for t in ("generated_documents", "consultations", "patients"):
        raw.execute(f"DROP TABLE {t}")
    for ddl in LEGACY_DDL:
        raw.execute(ddl)
    raw.commit()
    raw.execute("PRAGMA foreign_keys=ON")
    raw.close()


@pytest.fixture
def legacy(client, db):
    """Accounts on the current schema, clinical tables in the legacy layout (empty)."""
    create_pharmacist(db, full_name="Admin Mig", crf="", login="admin@mig.test", password=PASSWORD, role="administrator")
    est1 = create_establishment(db, name="Estabelecimento Um Migração").id
    est2 = create_establishment(db, name="Estabelecimento Dois Migração").id
    p1 = create_pharmacist(db, full_name="Farm Um", crf="1", crf_uf="SP", login="p1@mig.test", password=PASSWORD,
                           establishment_id=est1).id
    p2 = create_pharmacist(db, full_name="Farm Dois", crf="2", crf_uf="SP", login="p2@mig.test", password=PASSWORD,
                           establishment_id=est2).id
    db.close()
    make_legacy_layout()
    return {"est1": est1, "est2": est2, "p1": p1, "p2": p2, "client": client}


def fill(env, patients=(1, 2, 3)):
    """Legacy clinical data written as the old code stored it: patient 2 attended in both establishments."""
    c = get_crypto()
    raw = _direct()
    for pid in patients:
        name, dob, cpf = PATIENTS[pid]
        raw.execute("INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, c.encrypt(name), c.blind_index(NAME_INDEX_PURPOSE, normalize_name(name)), c.encrypt(dob),
                     c.encrypt(cpf), c.blind_index(CPF_INDEX_PURPOSE, cpf), f"2026-10-0{pid} 09:00:00.000000",
                     f"2026-10-0{pid} 09:00:00.000000"))
        raw.executemany("INSERT INTO patient_name_tokens (patient_id, token_index) VALUES (?, ?)",
                        [(pid, t) for t in sorted(name_prefix_indexes(name))])
    rows = [  # consultation: patient, account, created, closed
        (1, 1, env["p1"], "2026-10-05 10:00:00.000000", "2026-10-06 10:00:00.000000"),
        (2, 1, env["p1"], "2026-10-06 10:00:00.000000", None),
        (3, 2, env["p1"], "2026-10-07 10:00:00.000000", "2026-10-08 10:00:00.000000"),
        (4, 2, env["p2"], "2026-10-08 10:00:00.000000", "2026-10-09 10:00:00.000000"),
        (5, 2, env["p2"], "2026-10-09 10:00:00.000000", "2026-10-09 12:00:00.000000"),
        (6, 2, env["p1"], "2026-10-10 10:00:00.000000", None),
    ]
    for cid, pid, acc, created, closed in rows:
        if pid not in patients:
            continue
        raw.execute("INSERT INTO consultations VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cid, pid, acc, created[:10], created, created, closed))
        raw.execute("INSERT INTO soap_records (consultation_id, subjective_encrypted, objective_encrypted, "
                    "assessment_encrypted, plan_encrypted) VALUES (?, ?, ?, ?, ?)",
                    (cid, c.encrypt(f"S{cid}"), c.encrypt(f"O{cid}"), c.encrypt(f"A{cid}"), c.encrypt(f"P{cid}")))
        raw.execute("INSERT INTO exam_results (consultation_id, exam_name_encrypted, result_encrypted, unit_encrypted, "
                    "reference_range_encrypted, notes_encrypted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cid, c.encrypt("Glicemia"), c.encrypt(str(90 + cid)), c.encrypt("mg/dL"), c.encrypt("70-99"),
                     c.encrypt(""), created))
        pdf = f"%PDF-1.4 historical document of consultation {cid} %%EOF".encode()
        raw.execute("INSERT INTO generated_documents VALUES (?, ?, ?, 'referral', ?, ?, ?)",
                    (cid, cid, acc, created, c.encrypt_bytes(pdf), len(pdf)))
    raw.commit()
    raw.close()
    return {cid: (pid, acc, created) for cid, pid, acc, created, _ in rows if pid in patients}


def audit(monkeypatch, env, rows, owner):
    """Simulate the audited mapping for these rows (as built from evidence for production)."""
    monkeypatch.setattr(clinical_isolation, "AUDITED_CONSULTATIONS",
                        {cid: (_fingerprint(cid, pid, acc, created), owner(acc), "header") for cid, (pid, acc, created) in rows.items()})
    monkeypatch.setattr(clinical_isolation, "AUDITED_DOCUMENTS", {})
    monkeypatch.setattr(clinical_isolation, "AUDITED_ACCOUNTS", {})


def snapshot():
    with get_engine().connect() as conn:
        q = lambda sql: conn.execute(text(sql)).fetchall()
        return {
            "patients": q("SELECT id, full_name_encrypted, full_name_index, date_of_birth_encrypted, cpf_encrypted, "
                          "cpf_index, created_at, updated_at FROM patients ORDER BY id"),
            "tokens": q("SELECT patient_id, token_index FROM patient_name_tokens ORDER BY id"),
            "consultations": q("SELECT id, pharmacist_id, consultation_date, created_at, updated_at, closed_at "
                               "FROM consultations ORDER BY id"),
            "soap": q("SELECT * FROM soap_records ORDER BY id"),
            "exams": q("SELECT * FROM exam_results ORDER BY id"),
            "documents": q("SELECT id, consultation_id, pharmacist_id, document_type, created_at, pdf_encrypted, pdf_size "
                           "FROM generated_documents ORDER BY id"),
        }


def whole_db():
    with get_engine().connect() as conn:
        schema = conn.execute(text("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")).fetchall()
        data = {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY rowid")).fetchall()
                for (t,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    return hashlib.sha256(repr((schema, data)).encode()).hexdigest()


def login(client, user):
    token = client.post("/auth/login", data={"username": user, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_migration_assigns_splits_and_preserves_everything(legacy, monkeypatch):
    rows = fill(legacy, patients=(1, 2))
    audit(monkeypatch, legacy, rows, lambda acc: legacy["est1"] if acc == legacy["p1"] else legacy["est2"])
    before = snapshot()

    result = clinical_isolation.migrate(get_engine())
    assert result["split"] == [(2, legacy["est2"], 3)]  # patient 2's establishment-2 consultations get record 3
    after = snapshot()
    with get_engine().connect() as conn:
        q = lambda sql: conn.execute(text(sql)).fetchall()
        owners = dict(q("SELECT id, establishment_id FROM patients"))
        assert owners == {1: legacy["est1"], 2: legacy["est1"], 3: legacy["est2"]}  # original stays with the earliest
        cons = {r[0]: r[1:] for r in q("SELECT id, patient_id, establishment_id FROM consultations")}
        assert cons == {1: (1, legacy["est1"]), 2: (1, legacy["est1"]), 3: (2, legacy["est1"]), 4: (3, legacy["est2"]),
                        5: (3, legacy["est2"]), 6: (2, legacy["est1"])}
        assert dict(q("SELECT id, establishment_id FROM generated_documents")) == {c: e for c, (_, e) in cons.items()}
        new = q("SELECT full_name_encrypted, full_name_index, date_of_birth_encrypted, cpf_encrypted, cpf_index FROM patients WHERE id = 3")[0]
        orig = q("SELECT full_name_encrypted, full_name_index, date_of_birth_encrypted, cpf_encrypted, cpf_index FROM patients WHERE id = 2")[0]
        tokens = {pid: {t for p, t in q("SELECT patient_id, token_index FROM patient_name_tokens") if p == pid} for pid in (2, 3)}
        uniques = [(r[1], r[2]) for r in q("PRAGMA index_list(patients)")]
        assert q("PRAGMA foreign_key_check") == []
    # originals byte-identical; the split copy: fresh ciphertext, same identity and digests, own tokens
    assert after["patients"][:2] == before["patients"]
    c = get_crypto()
    assert [c.decrypt(v) for v in (new[0], new[2], new[3])] == [c.decrypt(v) for v in (orig[0], orig[2], orig[3])]
    assert new[0] != orig[0] and new[3] != orig[3] and (new[1], new[4]) == (orig[1], orig[4])
    assert tokens[3] == tokens[2] == set(name_prefix_indexes(PATIENTS[2][0]))
    assert after["tokens"][:len(before["tokens"])] == before["tokens"]
    # clinical content, status and documents untouched
    for key in ("consultations", "soap", "exams", "documents"):
        assert after[key] == before[key], key
    # CPF unique per establishment only (no global uniqueness left)
    with get_engine().connect() as conn:
        unique_cols = [[r[2] for r in conn.execute(text(f"PRAGMA index_info({name})"))] for name, u in uniques if u]
    assert unique_cols == [["establishment_id", "cpf_index"]]
    # idempotent: a second startup changes nothing
    digest = whole_db()
    init_db()
    assert clinical_isolation.migrate(get_engine()) is None and whole_db() == digest


def test_migrated_data_is_isolated_and_reprints_unchanged(legacy, monkeypatch):
    rows = fill(legacy, patients=(1, 2))
    audit(monkeypatch, legacy, rows, lambda acc: legacy["est1"] if acc == legacy["p1"] else legacy["est2"])
    init_db()
    client = legacy["client"]
    h1, h2 = login(client, "p1@mig.test"), login(client, "p2@mig.test")
    search = lambda h, q: [p["id"] for p in client.post("/patients/search", headers=h, json={"query": q}).json()]
    cpf = PATIENTS[2][2]
    assert search(h1, cpf) == [2] and search(h2, cpf) == [3] and search(h1, "compartilhado") == [2]
    assert search(h2, "compartilhado") == [3] and search(h2, "alfa") == []
    assert [x["id"] for x in client.get("/patients/2/consultations", headers=h1).json()] == [6, 3]
    assert [x["id"] for x in client.get("/patients/3/consultations", headers=h2).json()] == [5, 4]
    assert client.get("/patients/2/consultations/4", headers=h1).status_code == 404
    assert client.get("/patients/3/consultations/4", headers=h1).status_code == 404
    for cid, h, pid in ((3, h1, 2), (4, h2, 3)):
        expected = f"%PDF-1.4 historical document of consultation {cid} %%EOF".encode()
        assert client.get(f"/patients/{pid}/consultations/{cid}/documents/{cid}", headers=h).content == expected
    assert client.get("/patients/3/consultations/4/documents/4", headers=h1).status_code == 404
    # the same CPF now exists once in each establishment; a duplicate inside one is still refused
    dup = client.post("/patients", headers=h1, json={"full_name": "Outro", "date_of_birth": "1990-01-01", "cpf": cpf})
    assert dup.status_code == 409
    # new records are written normally right after the migration (application connections)
    new = client.post("/patients", headers=h2, json={"full_name": "Novo Depois", "date_of_birth": "1990-01-01",
                                                     "cpf": PATIENTS[1][2]})
    assert new.status_code == 201 and search(h2, PATIENTS[1][2]) == [new.json()["id"]] and search(h1, PATIENTS[1][2]) == [1]
    # the open consultation of each record is untouched; starting one in establishment 2 closes only its own
    assert client.get("/patients/2/consultations/6", headers=h1).json()["closed_at"] is None
    client.post("/patients/3/consultations", headers=h2, json={})
    assert client.get("/patients/2/consultations/6", headers=h1).json()["closed_at"] is None


@pytest.mark.parametrize("case", ["no evidence", "row changed since the audit", "audited header disagrees",
                                  "patient without consultation", "account moved after the audit"])
def test_unresolvable_ownership_aborts_without_any_change(legacy, monkeypatch, case):
    rows = fill(legacy, patients=(1, 2, 3) if case == "patient without consultation" else (1, 2))
    owner = lambda acc: legacy["est1"] if acc == legacy["p1"] else legacy["est2"]
    audit(monkeypatch, legacy, rows, owner)
    expected = None
    if case == "no evidence":
        monkeypatch.setattr(clinical_isolation, "AUDITED_CONSULTATIONS", {})
        expected = "atendimentos [1, 2, 3, 4, 5, 6]"
    elif case == "row changed since the audit":
        fps = dict(clinical_isolation.AUDITED_CONSULTATIONS)
        fps[4] = ("0000000000000000", *fps[4][1:])
        monkeypatch.setattr(clinical_isolation, "AUDITED_CONSULTATIONS", fps)
        expected = "atendimentos [4]"
    elif case == "audited header disagrees":
        pid, acc, created = rows[4]
        monkeypatch.setattr(clinical_isolation, "AUDITED_DOCUMENTS", {4: (_fingerprint(4, 4, created), legacy["est1"])})
        expected = "documentos em conflito [4]"
    elif case == "patient without consultation":
        expected = "pacientes sem atendimento [3]"
    elif case == "account moved after the audit":
        later = dict(clinical_isolation.AUDITED_CONSULTATIONS)
        del later[5]  # started after the audit by p2, who has since been moved
        with get_engine().connect() as conn:
            p2_created = conn.execute(text("SELECT created_at FROM pharmacists WHERE id = :i"), {"i": legacy["p2"]}).scalar()
        monkeypatch.setattr(clinical_isolation, "AUDITED_CONSULTATIONS", later)
        monkeypatch.setattr(clinical_isolation, "AUDIT_CUTOFF", "2026-10-09 00:00:00.000000")
        monkeypatch.setattr(clinical_isolation, "AUDITED_ACCOUNTS",
                            {legacy["p2"]: (_fingerprint(legacy["p2"], p2_created, "pharmacist"), legacy["est1"])})
        expected = "atendimentos [5]"
    digest = whole_db()
    with pytest.raises(ClinicalOwnershipError) as error:
        clinical_isolation.migrate(get_engine())
    assert expected in str(error.value), str(error.value)
    assert whole_db() == digest  # nothing changed (one transaction, rolled back)
    assert "establishment_id" not in {c["name"] for c in inspect(get_engine()).get_columns("patients")}
    with pytest.raises(ClinicalOwnershipError):  # the application refuses to start on it
        init_db()


def test_consultation_started_after_the_audit_by_an_unmoved_account_is_resolved(legacy, monkeypatch):
    rows = fill(legacy, patients=(1, 2))
    owner = lambda acc: legacy["est1"] if acc == legacy["p1"] else legacy["est2"]
    audit(monkeypatch, legacy, rows, owner)
    later = dict(clinical_isolation.AUDITED_CONSULTATIONS)
    del later[5]
    with get_engine().connect() as conn:
        p2_created = conn.execute(text("SELECT created_at FROM pharmacists WHERE id = :i"), {"i": legacy["p2"]}).scalar()
    monkeypatch.setattr(clinical_isolation, "AUDITED_CONSULTATIONS", later)
    monkeypatch.setattr(clinical_isolation, "AUDIT_CUTOFF", "2026-10-09 00:00:00.000000")
    monkeypatch.setattr(clinical_isolation, "AUDITED_ACCOUNTS",
                        {legacy["p2"]: (_fingerprint(legacy["p2"], p2_created, "pharmacist"), legacy["est2"])})
    clinical_isolation.migrate(get_engine())
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT establishment_id FROM consultations WHERE id = 5")).scalar() == legacy["est2"]


def test_single_establishment_database_migrates_without_evidence(client, db):
    """Any installation that only ever had one establishment: everything belongs to it."""
    create_pharmacist(db, full_name="Farm Única", crf="1", crf_uf="SP", login="u@mig.test", password=PASSWORD)
    pid = db.execute(text("SELECT id FROM pharmacists")).scalar()
    db.close()
    make_legacy_layout()
    rows = fill({"p1": pid, "p2": pid})
    assert len(rows) == 6
    init_db()
    with get_engine().connect() as conn:
        est = conn.execute(text("SELECT id FROM establishments")).scalar()
        assert {r[0] for r in conn.execute(text("SELECT establishment_id FROM patients"))} == {est}
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3  # nothing to split
        assert {r[0] for r in conn.execute(text("SELECT establishment_id FROM consultations"))} == {est}
