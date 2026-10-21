"""Establishment ownership of clinical data (patients, consultations, documents).

Each establishment is an independent clinical silo: a patient record, its consultations (with
their SOAP and exam results) and its documents belong permanently to one establishment. The same
person attended in two establishments has two independent records.

This module holds the database guards that keep ownership consistent and immutable, and the
one-time migration of a database from before ownership existed. The migration never guesses:
each legacy consultation must be resolved from audited evidence (or the database must have a
single establishment); otherwise it stops, rolls everything back and names the internal ids.
"""

import hashlib
import sqlite3

from sqlalchemy import Engine, inspect
from sqlalchemy.schema import CreateIndex, CreateTable

# Database-level guards (SQLite triggers): ownership is mandatory, never changes, and every
# consultation/document belongs to the same establishment as its patient/consultation.
GUARDS = {
    "trg_patients_establishment_fixed": """
        CREATE TRIGGER IF NOT EXISTS trg_patients_establishment_fixed
        BEFORE UPDATE OF establishment_id ON patients
        WHEN NEW.establishment_id IS NOT OLD.establishment_id
        BEGIN SELECT RAISE(ABORT, 'patient establishment cannot change'); END""",
    "trg_consultations_establishment_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_consultations_establishment_insert
        BEFORE INSERT ON consultations
        WHEN NEW.establishment_id IS NULL
          OR NEW.establishment_id IS NOT (SELECT establishment_id FROM patients WHERE id = NEW.patient_id)
        BEGIN SELECT RAISE(ABORT, 'consultation establishment must be its patient''s'); END""",
    "trg_consultations_establishment_update": """
        CREATE TRIGGER IF NOT EXISTS trg_consultations_establishment_update
        BEFORE UPDATE OF establishment_id, patient_id ON consultations
        WHEN NEW.establishment_id IS NOT OLD.establishment_id
          OR NEW.establishment_id IS NOT (SELECT establishment_id FROM patients WHERE id = NEW.patient_id)
        BEGIN SELECT RAISE(ABORT, 'consultation establishment cannot change'); END""",
    "trg_documents_establishment_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_documents_establishment_insert
        BEFORE INSERT ON generated_documents
        WHEN NEW.establishment_id IS NULL
          OR NEW.establishment_id IS NOT (SELECT establishment_id FROM consultations WHERE id = NEW.consultation_id)
        BEGIN SELECT RAISE(ABORT, 'document establishment must be its consultation''s'); END""",
    "trg_documents_establishment_update": """
        CREATE TRIGGER IF NOT EXISTS trg_documents_establishment_update
        BEFORE UPDATE OF establishment_id, consultation_id ON generated_documents
        WHEN NEW.establishment_id IS NOT OLD.establishment_id
          OR NEW.establishment_id IS NOT (SELECT establishment_id FROM consultations WHERE id = NEW.consultation_id)
        BEGIN SELECT RAISE(ABORT, 'document establishment cannot change'); END""",
}

# ---- audited legacy ownership of the production database (2026-10-21) ----------------------
# Built from a copy of production: each consultation's establishment comes from the establishment
# printed in its own stored documents ("header"), else from its pharmacist's proven assignment at
# that time ("account-history"; the only account ever moved changed establishment between
# 2026-10-17 03:12:39 and 03:14:40), or from the explicit audited decision ("override":
# consultation 24 -> establishment 2, whose own document carries that establishment). An entry
# applies only to the exact row it was audited on (fingerprint of id, patient, pharmacist and
# creation time), so it can never be applied to another database.
AUDIT_CUTOFF = "2026-10-20 13:02:49.105219"
AUDITED_CONSULTATIONS = {  # id: (fingerprint, establishment, evidence)
    1: ("9c923e25ae715aba", 1, "account-history"),
    2: ("bb1f6e17e37f5b77", 1, "account-history"),
    3: ("69e3f96cf50733b6", 1, "account-history"),
    4: ("1288451029f453e8", 1, "account-history"),
    5: ("e0fa14dd2ef47c83", 1, "account-history"),
    6: ("adf342eb1251a30c", 1, "account-history"),
    7: ("a32fab287a8dd8bf", 1, "account-history"),
    8: ("0c9826ec6402cf7a", 1, "account-history"),
    9: ("7e73610a04524fd7", 1, "account-history"),
    10: ("b1722871e17e8b67", 1, "account-history"),
    11: ("f7b279016503aa5e", 1, "account-history"),
    12: ("d6f50c9938ea7463", 1, "account-history"),
    13: ("ae5f9edd70a04f30", 1, "header"),
    14: ("c3b2fba913c88eab", 1, "account-history"),
    15: ("39c711b95d7bca39", 1, "account-history"),
    16: ("5944648dbba3345b", 1, "account-history"),
    17: ("f7a8828c1e0086f1", 1, "account-history"),
    18: ("099c1071a7805e5a", 1, "header"),
    19: ("14416710417be5fd", 1, "header"),
    20: ("1d1322ab61fc3e45", 1, "header"),
    21: ("3b343ee80e149014", 1, "account-history"),
    22: ("f46048ce93099583", 1, "account-history"),
    23: ("3f0cba533d90b874", 1, "header"),
    24: ("d53ba9dbb2833018", 2, "override"),
    25: ("a6ab406707693f91", 2, "header"),
    26: ("4166c740e8da8c1a", 1, "account-history"),
    27: ("901b2573256ee52c", 1, "account-history"),
    28: ("65a1920dceef3103", 1, "header"),
    29: ("b1eca72e3f6e0e0e", 2, "header"),
    30: ("b9e40e0411d19d91", 2, "header"),
    31: ("c045323fcfd3bcce", 1, "account-history"),
    32: ("be8a4ef0343c83dd", 2, "header"),
    33: ("e5c2c76447bf93b1", 1, "account-history"),
    34: ("875825ab836e8043", 1, "account-history"),
    35: ("cc3820163a513beb", 2, "account-history"),
    36: ("3b4f869301236653", 1, "account-history"),
    37: ("18cf2669b277442d", 1, "account-history"),
    38: ("ebef18d8fa2799ed", 2, "header"),
    39: ("1e48132d45e1cfb7", 1, "account-history"),
}
AUDITED_DOCUMENTS = {  # id: (fingerprint, establishment printed in the stored PDF's header)
    1: ("e428544d976acffd", 1),
    2: ("0051e6bfba4da4be", 1),
    3: ("8612edd34ee1f9fc", 1),
    4: ("899d981a6832477d", 1),
    5: ("44892de0bc478df4", 1),
    6: ("8eb69114482e075a", 1),
    7: ("e1859b687c795981", 1),
    8: ("47a621cdcce9a396", 2),
    9: ("76a1094655ae5d39", 2),
    10: ("a330924a5c24f3ab", 1),
    11: ("ea5ba4d03852a3eb", 2),
    12: ("a584e9ec68629582", 2),
    13: ("97df88e7a06bd3b7", 2),
    14: ("11683cadf01dc14e", 2),
    15: ("473168afed4c4e7c", 2),
    16: ("0f5db6b881129687", 2),
}
# Pharmacists' establishments at the audit. A consultation started after the audit by one of them
# is resolved only if that pharmacist is still in the same establishment.
AUDITED_ACCOUNTS = {  # id: (fingerprint, establishment)
    1: ("b1918bef50870409", 1),
    2: ("53d2260ddfcae709", 2),
    4: ("21b8ceac9892d305", 1),
}


class ClinicalOwnershipError(RuntimeError):
    """The legacy data cannot be assigned to establishments without guessing."""


def _fingerprint(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def install_guards(engine: Engine) -> None:
    """Idempotent (IF NOT EXISTS): on a new or migrated database."""
    with engine.begin() as connection:
        for ddl in GUARDS.values():
            connection.exec_driver_sql(ddl)


def needs_migration(engine: Engine) -> bool:
    insp = inspect(engine)
    return "patients" in insp.get_table_names() and "establishment_id" not in {
        c["name"] for c in insp.get_columns("patients")}


def _resolve(cur: sqlite3.Cursor) -> tuple[dict[int, int], dict[int, int]]:
    """Establishment of every legacy consultation, and the owning establishment of every patient
    (establishment of the patient's earliest consultation). Raises if anything is unresolved."""
    establishments = [r[0] for r in cur.execute("SELECT id FROM establishments ORDER BY id")]
    single = establishments[0] if len(establishments) == 1 else None
    accounts = {r[0]: r for r in cur.execute("SELECT id, created_at, role, establishment_id FROM pharmacists")}
    owner: dict[int, int] = {}
    unresolved: list[int] = []
    for cid, pid, acc, created in cur.execute(
            "SELECT id, patient_id, pharmacist_id, created_at FROM consultations ORDER BY id").fetchall():
        audited = AUDITED_CONSULTATIONS.get(cid)
        account = accounts.get(acc)
        audited_account = AUDITED_ACCOUNTS.get(acc)
        if audited and audited[0] == _fingerprint(cid, pid, acc, created):
            owner[cid] = audited[1]
        elif single is not None:  # only one establishment has ever existed: nothing to decide
            owner[cid] = single
        elif (audited_account and account and created > AUDIT_CUTOFF
              and audited_account[0] == _fingerprint(acc, account[1], account[2])
              and account[3] == audited_account[1]):
            owner[cid] = audited_account[1]  # started after the audit; pharmacist never moved since
        else:
            unresolved.append(cid)
    # documents: each belongs to its consultation; audited headers must agree
    conflicts = []
    for did, cid, created in cur.execute("SELECT id, consultation_id, created_at FROM generated_documents").fetchall():
        audited = AUDITED_DOCUMENTS.get(did)
        if audited and audited[0] == _fingerprint(did, cid, created) and cid in owner and owner[cid] != audited[1]:
            conflicts.append(did)
    patient_owner: dict[int, int] = {}
    orphans: list[int] = []
    for (pid,) in cur.execute("SELECT id FROM patients ORDER BY id").fetchall():
        first = cur.execute("SELECT id FROM consultations WHERE patient_id = ? ORDER BY created_at, id LIMIT 1",
                            (pid,)).fetchone()
        if first is not None and first[0] in owner:
            patient_owner[pid] = owner[first[0]]
        elif first is None and single is not None:
            patient_owner[pid] = single
        elif first is None:
            orphans.append(pid)
    if unresolved or conflicts or orphans:
        raise ClinicalOwnershipError(
            "Migração de estabelecimentos interrompida (nada foi alterado). Sem evidência autoritativa: "
            f"atendimentos {unresolved}; pacientes sem atendimento {orphans}; documentos em conflito {conflicts}.")
    return owner, patient_owner


def migrate(engine: Engine) -> dict | None:
    """One-time, atomic: add ownership to a legacy database. Does nothing on a migrated/new one."""
    if not needs_migration(engine):
        return None
    if engine.dialect.name != "sqlite":  # the table rebuild below is SQLite's procedure
        raise ClinicalOwnershipError("Migração de estabelecimentos disponível apenas para SQLite.")
    from backend.crypto import get_crypto
    from backend.models import Patient
    from backend.patients import name_prefix_indexes

    with engine.connect() as connection:
        has_establishment = connection.exec_driver_sql("SELECT count(*) FROM establishments").scalar()
    if not has_establishment:
        # database from before multiple establishments: create the establishment first (existing step)
        from sqlalchemy.orm import Session

        from backend.establishments import migrate_legacy_establishment
        with Session(engine) as session:
            migrate_legacy_establishment(session)

    crypto = get_crypto()
    table = Patient.__table__
    create_patients = str(CreateTable(table).compile(dialect=engine.dialect)).strip()
    assert create_patients.startswith("CREATE TABLE patients ")
    create_patients_new = create_patients.replace("CREATE TABLE patients ", "CREATE TABLE patients_new ", 1)
    patient_indexes = [str(CreateIndex(ix).compile(dialect=engine.dialect)) for ix in table.indexes]
    old_columns = ("id", "full_name_encrypted", "full_name_index", "date_of_birth_encrypted", "cpf_encrypted",
                   "cpf_index", "created_at", "updated_at")

    raw = engine.raw_connection()
    conn: sqlite3.Connection = raw.driver_connection
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # explicit transaction control
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys=OFF")  # required for the table rebuild; checked before commit
        cur.execute("BEGIN EXCLUSIVE")
        if "establishment_id" in {r[1] for r in cur.execute("PRAGMA table_info(patients)")}:
            cur.execute("ROLLBACK")  # migrated meanwhile by another process
            return None
        before = {t: cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("patients", "consultations", "generated_documents", "patient_name_tokens")}
        owner, patient_owner = _resolve(cur)

        # consultations and documents: ownership columns (added, then filled)
        cur.execute("ALTER TABLE consultations ADD COLUMN establishment_id INTEGER REFERENCES establishments(id)")
        cur.executemany("UPDATE consultations SET establishment_id = ? WHERE id = ?",
                        [(est, cid) for cid, est in owner.items()])
        cur.execute("ALTER TABLE generated_documents ADD COLUMN establishment_id INTEGER REFERENCES establishments(id)")
        cur.execute("UPDATE generated_documents SET establishment_id = "
                    "(SELECT establishment_id FROM consultations WHERE consultations.id = generated_documents.consultation_id)")
        cur.execute("CREATE INDEX ix_consultations_establishment_id ON consultations (establishment_id)")
        cur.execute("CREATE INDEX ix_generated_documents_establishment_id ON generated_documents (establishment_id)")

        # patients: rebuilt with ownership and per-establishment CPF uniqueness; every original row
        # (ciphertext, digests, timestamps, id) copied byte for byte
        cur.execute("CREATE TEMP TABLE patient_owner (patient_id INTEGER PRIMARY KEY, establishment_id INTEGER NOT NULL)")
        cur.executemany("INSERT INTO patient_owner VALUES (?, ?)", list(patient_owner.items()))
        cur.execute(create_patients_new)
        cols = ", ".join(old_columns)
        cur.execute(f"INSERT INTO patients_new ({cols}, establishment_id) SELECT {', '.join('p.' + c for c in old_columns)}, "
                    "o.establishment_id FROM patients p JOIN patient_owner o ON o.patient_id = p.id")
        cur.execute("DROP TABLE patients")
        cur.execute("ALTER TABLE patients_new RENAME TO patients")
        for ddl in patient_indexes:
            cur.execute(ddl)
        cur.execute("DROP TABLE patient_owner")

        # patients attended in more than one establishment: an independent record per extra establishment
        splits = []
        shared = cur.execute(
            "SELECT c.patient_id, c.establishment_id FROM consultations c JOIN patients p ON p.id = c.patient_id "
            "WHERE c.establishment_id != p.establishment_id GROUP BY c.patient_id, c.establishment_id "
            "ORDER BY c.patient_id, c.establishment_id").fetchall()
        for pid, est in shared:
            name_ct, name_ix, dob_ct, cpf_ct, cpf_ix, created, updated = cur.execute(
                "SELECT full_name_encrypted, full_name_index, date_of_birth_encrypted, cpf_encrypted, cpf_index, "
                "created_at, updated_at FROM patients WHERE id = ?", (pid,)).fetchone()
            full_name = crypto.decrypt(name_ct)
            cur.execute(
                "INSERT INTO patients (establishment_id, full_name_encrypted, full_name_index, date_of_birth_encrypted, "
                "cpf_encrypted, cpf_index, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (est, crypto.encrypt(full_name), name_ix, crypto.encrypt(crypto.decrypt(dob_ct)),
                 crypto.encrypt(crypto.decrypt(cpf_ct)), cpf_ix, created, updated))
            new_id = cur.lastrowid
            cur.executemany("INSERT INTO patient_name_tokens (patient_id, token_index) VALUES (?, ?)",
                            [(new_id, t) for t in sorted(name_prefix_indexes(full_name))])
            cur.execute("UPDATE consultations SET patient_id = ? WHERE patient_id = ? AND establishment_id = ?",
                        (new_id, pid, est))
            splits.append((pid, est, new_id))

        for ddl in GUARDS.values():
            cur.execute(ddl)

        # verification before commit: nothing unowned, everything consistent, foreign keys intact
        one = lambda sql: cur.execute(sql).fetchone()[0]
        problems = {
            "consultations without establishment": one("SELECT count(*) FROM consultations WHERE establishment_id IS NULL"),
            "documents without establishment": one("SELECT count(*) FROM generated_documents WHERE establishment_id IS NULL"),
            "consultation/patient mismatch": one("SELECT count(*) FROM consultations c JOIN patients p ON p.id = c.patient_id "
                                                 "WHERE c.establishment_id != p.establishment_id"),
            "document/consultation mismatch": one("SELECT count(*) FROM generated_documents d JOIN consultations c "
                                                  "ON c.id = d.consultation_id WHERE d.establishment_id != c.establishment_id"),
            "foreign key violations": len(cur.execute("PRAGMA foreign_key_check").fetchall()),
            "patients count": one("SELECT count(*) FROM patients") - before["patients"] - len(splits),
            "consultations count": one("SELECT count(*) FROM consultations") - before["consultations"],
            "documents count": one("SELECT count(*) FROM generated_documents") - before["generated_documents"],
        }
        if any(problems.values()):
            raise ClinicalOwnershipError(f"Migração de estabelecimentos interrompida (nada foi alterado): {problems}")
        cur.execute("COMMIT")
        return {"consultations": len(owner), "patients": before["patients"], "split": splits}
    except BaseException:
        if conn.in_transaction:
            cur.execute("ROLLBACK")
        raise
    finally:
        cur.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = previous_isolation
        raw.close()
