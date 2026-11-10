"""Optional patient phone and address: encrypted at rest, kept as typed, never searched, never printed
on documents, and visible only under the patient's normal (establishment-scoped) access."""
import hashlib
import io

import pytest
from pypdf import PdfReader
from sqlalchemy import inspect, text

from backend.database import get_engine, init_db
from backend.patients import name_prefix_indexes
from tests.test_clinical_isolation import CPF, iso, new_patient  # noqa: F401  (fixture)
from tests.test_clinical_isolation_migration import legacy  # noqa: F401  (fixture)

PHONE = "+55 21 99999-9999"
ADDRESS = "Rua das Flores, 123 - Apto 4, Niterói/RJ"
NAME = "Paciente Contato Exemplo"


def create(env, h, cpf=CPF, **contact):
    r = env["c"].post("/patients", headers=h, json={"full_name": NAME, "date_of_birth": "1970-01-15", "cpf": cpf, **contact})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("sent, stored", [
    ({"phone": PHONE, "address": ADDRESS}, (PHONE, ADDRESS)),
    ({"phone": "(21) 99999-9999"}, ("(21) 99999-9999", None)),
    ({"address": ADDRESS}, (None, ADDRESS)),
    ({}, (None, None)),
    ({"phone": "", "address": ""}, (None, None)),
    ({"phone": "   ", "address": "  \t "}, (None, None)),
    ({"phone": "  (21) 3333-0000  ", "address": "  Av. Brasil, 500  "}, ("(21) 3333-0000", "Av. Brasil, 500")),
])
def test_optional_contact_is_kept_as_typed(iso, sent, stored):  # noqa: F811
    patient = create(iso, iso["a1"], **sent)
    assert (patient["phone"], patient["address"]) == stored
    again = iso["c"].get(f"/patients/{patient['id']}", headers=iso["a2"]).json()  # colleague, same establishment
    assert (again["phone"], again["address"]) == stored


def test_contact_length_limits(iso):  # noqa: F811
    c = iso["c"]
    base = {"full_name": NAME, "date_of_birth": "1970-01-15", "cpf": CPF}
    assert c.post("/patients", headers=iso["a1"], json={**base, "phone": "1" * 41}).status_code == 422
    assert c.post("/patients", headers=iso["a1"], json={**base, "address": "a" * 301}).status_code == 422
    assert c.post("/patients", headers=iso["a1"], json={**base, "phone": "1" * 40, "address": "a" * 300}).status_code == 201


def test_contact_is_encrypted_at_rest(iso):  # noqa: F811
    patient = create(iso, iso["a1"], phone=PHONE, address=ADDRESS)
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT * FROM patients WHERE id = :i"), {"i": patient["id"]}).mappings().one()
        raw = repr(conn.execute(text("SELECT * FROM patients")).fetchall())
    assert PHONE not in raw and "99999-9999" not in raw and "Flores" not in raw and "Niterói" not in raw
    assert row["phone_encrypted"] and row["address_encrypted"] and row["phone_encrypted"] != row["address_encrypted"]


def test_contact_follows_patient_isolation(iso):  # noqa: F811
    c = iso["c"]
    pa = create(iso, iso["a1"], phone=PHONE, address=ADDRESS)["id"]
    assert c.get(f"/patients/{pa}", headers=iso["b1"]).status_code == 404  # other establishment
    assert c.post("/patients/search", headers=iso["b1"], json={"query": CPF}).json() == []
    assert c.get(f"/patients/{pa}", headers=iso["admin"]).status_code == 403
    assert c.get(f"/patients/{pa}").status_code == 401
    iso["move"]("a1@iso.test", "B")
    assert c.get(f"/patients/{pa}", headers=iso["a1"]).status_code == 404  # moved pharmacist loses it
    assert c.get(f"/patients/{pa}", headers=iso["a2"]).json()["phone"] == PHONE  # colleague keeps it


def test_cpf_rules_unchanged(iso):  # noqa: F811
    c = iso["c"]
    a = create(iso, iso["a1"], phone=PHONE)
    dup = c.post("/patients", headers=iso["a2"], json={"full_name": "Outro", "date_of_birth": "1980-01-01", "cpf": CPF,
                                                       "phone": "(11) 1111-1111"})
    assert dup.status_code == 409  # a different phone does not make it another patient
    b = create(iso, iso["b1"], address="Outro endereço")  # same CPF in the other establishment: independent record
    assert b["id"] != a["id"] and (b["phone"], b["address"]) == (None, "Outro endereço")
    assert c.get(f"/patients/{a['id']}", headers=iso["a1"]).json()["address"] is None


def test_search_results_never_carry_contact_data(iso):  # noqa: F811
    c = iso["c"]
    created = create(iso, iso["a1"], phone=PHONE, address=ADDRESS)
    assert (created["phone"], created["address"]) == (PHONE, ADDRESS)  # registration response keeps them
    detail = c.get(f"/patients/{created['id']}", headers=iso["a1"]).json()
    assert (detail["phone"], detail["address"]) == (PHONE, ADDRESS)  # and so does the patient record
    for query in (CPF, "contato", "PACIENTE exemplo"):
        found = c.post("/patients/search", headers=iso["a1"], json={"query": query}).json()
        assert [p["id"] for p in found] == [created["id"]], query
        assert set(found[0]) == {"id", "full_name", "date_of_birth", "cpf", "created_at", "updated_at"}, query
        assert PHONE not in str(found) and "Flores" not in str(found)
    assert c.post("/patients/search", headers=iso["b1"], json={"query": CPF}).json() == []  # isolation unchanged
    assert c.post("/patients/search", headers=iso["admin"], json={"query": CPF}).status_code == 403
    assert c.post("/patients/search", json={"query": CPF}).status_code == 401


def test_contact_is_not_searchable_and_tokens_unchanged(iso):  # noqa: F811
    c = iso["c"]
    pid = create(iso, iso["a1"], phone=PHONE, address=ADDRESS)["id"]
    for query in ("Flores", "Niteroi", "apto flores"):
        assert c.post("/patients/search", headers=iso["a1"], json={"query": query}).json() == []
    assert [p["id"] for p in c.post("/patients/search", headers=iso["a1"], json={"query": "contato"}).json()] == [pid]
    with get_engine().connect() as conn:
        tokens = {r[0] for r in conn.execute(text("SELECT token_index FROM patient_name_tokens WHERE patient_id = :i"), {"i": pid})}
    assert tokens == name_prefix_indexes(NAME)  # derived from the name only


def test_documents_do_not_print_contact(iso):  # noqa: F811
    c = iso["c"]
    pid = create(iso, iso["a1"], phone=PHONE, address=ADDRESS)["id"]
    cid = c.post(f"/patients/{pid}/consultations", headers=iso["a1"], json={}).json()["id"]
    pdf = c.post(f"/patients/{pid}/consultations/{cid}/documents/prescription", headers=iso["a1"],
                 json={"items": [{"medication": "Item de teste"}]}).content
    body = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    assert NAME in body and "99999-9999" not in body and "Flores" not in body


def _whole_db():
    with get_engine().connect() as conn:
        schema = conn.execute(text("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")).fetchall()
        data = {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY rowid")).fetchall()
                for (t,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    return hashlib.sha256(repr((schema, data)).encode()).hexdigest()


def test_migration_adds_two_nullable_columns_only_and_is_idempotent(iso):  # noqa: F811
    """A database in the layout of 3d1097b (no contact columns) gains them, empty, with nothing else changed."""
    full_record_patient = new_patient(iso, iso["a1"])
    iso["c"].post(f"/patients/{full_record_patient}/consultations", headers=iso["a1"], json={})
    raw = get_engine().raw_connection()
    raw.execute("ALTER TABLE patients DROP COLUMN phone_encrypted")
    raw.execute("ALTER TABLE patients DROP COLUMN address_encrypted")
    raw.commit()
    raw.close()
    with get_engine().connect() as conn:
        before = {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY rowid")).fetchall()
                  for (t,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    init_db()
    cols = {c["name"]: c for c in inspect(get_engine()).get_columns("patients")}
    assert cols["phone_encrypted"]["nullable"] and cols["address_encrypted"]["nullable"]
    with get_engine().connect() as conn:
        after = {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY rowid")).fetchall() for t in before}
        new_values = conn.execute(text("SELECT phone_encrypted, address_encrypted FROM patients")).fetchall()
    assert all(v == (None, None) for v in new_values)
    assert {t: rows for t, rows in after.items() if t != "patients"} == {t: rows for t, rows in before.items() if t != "patients"}
    assert [tuple(r)[:-2] for r in after["patients"]] == [tuple(r) for r in before["patients"]]  # existing values untouched
    digest = _whole_db()
    init_db()
    assert _whole_db() == digest  # second run: no change
    created = create(iso, iso["a1"], cpf="11144477735", phone=PHONE)
    assert iso["c"].get(f"/patients/{created['id']}", headers=iso["a1"]).json()["phone"] == PHONE


def test_refused_legacy_migration_still_changes_nothing(legacy, monkeypatch):  # noqa: F811
    """On a database from before establishment ownership, contact columns arrive with the patients
    rebuild; a migration that refuses to run leaves the database exactly as it was."""
    from backend import clinical_isolation
    from tests.test_clinical_isolation_migration import fill, whole_db

    fill(legacy, patients=(1, 2))  # no audited mapping for these rows: the migration must refuse
    digest = whole_db()
    with pytest.raises(clinical_isolation.ClinicalOwnershipError):
        init_db()
    assert whole_db() == digest
    assert "phone_encrypted" not in {c["name"] for c in inspect(get_engine()).get_columns("patients")}

