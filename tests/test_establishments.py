"""Multiple establishments: migration, administration, pharmacist links and document headers.
Clinical data belongs to the establishment where it was created (complete isolation, see
test_clinical_isolation*.py)."""
import io

import pytest
from pypdf import PdfReader
from sqlalchemy import inspect, select, text

from backend.auth import create_pharmacist
from backend.database import get_engine
from backend.establishments import migrate_legacy_establishment
from backend.models import ROLE_ADMINISTRATOR, Establishment, EstablishmentSettings, Pharmacist

PASSWORD = "senha-segura-1"
EST_A = {"name": "Farmácia Fictícia A", "cnpj": "11.111.111/0001-11", "address": "Rua A, 10 - Centro",
         "phone": "(21) 1111-1111"}
EST_B = {"name": "Farmácia Fictícia B", "cnpj": "22.222.222/0001-22", "address": "Avenida B, 20 - Bairro",
         "phone": "(11) 2222-2222"}
PATIENT = {"full_name": "Paciente Ficticio Estab", "date_of_birth": "1975-05-05", "cpf": "12345678909"}
DOC_PAYLOADS = {
    "prescription": {"items": [{"medication": "Item de teste"}]},
    "exam-request": {"exams": ["Ureia"], "clinical_justification": "Justificativa de teste."},
    "referral": {"destination": "Nefrologia", "case_summary": "Resumo.", "prm": "PRM.", "suggested_conduct": "Conduta."},
}


def _headers(client, login, password=PASSWORD):
    token = client.post("/auth/login", data={"username": login, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def pdf_text(data):
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture
def admin(client, db):
    create_pharmacist(db, full_name="Admin Estab", crf="", login="admin@estab.test", password=PASSWORD,
                      role=ROLE_ADMINISTRATOR)
    return _headers(client, "admin@estab.test")


@pytest.fixture
def two(client, admin):
    """Establishments A and B, pharmacist A linked to A and pharmacist B linked to B."""
    a = client.post("/admin/establishments", headers=admin, json=EST_A).json()["id"]
    b = client.post("/admin/establishments", headers=admin, json=EST_B).json()["id"]
    for login, name, crf, est in (("farm.a@estab.test", "Farmacêutica A", "10001", a),
                                  ("farm.b@estab.test", "Farmacêutico B", "10002", b)):
        r = client.post("/admin/users", headers=admin, json={
            "full_name": name, "crf": crf, "crf_uf": "RJ", "login": login, "password": PASSWORD,
            "role": "pharmacist", "establishment_id": est})
        assert r.status_code == 201, r.text
    return {"a": a, "b": b, "ha": _headers(client, "farm.a@estab.test"), "hb": _headers(client, "farm.b@estab.test")}


def _issue(client, headers, kind="prescription", extra=None):
    pid = client.post("/patients", json=PATIENT, headers=headers).json().get("id")
    if pid is None:  # patient already registered in this establishment: find it
        pid = client.post("/patients/search", json={"query": PATIENT["cpf"]}, headers=headers).json()[0]["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=headers).json()["id"]
    payload = {**DOC_PAYLOADS[kind], **(extra or {})}
    response = client.post(f"/patients/{pid}/consultations/{cid}/documents/{kind}", json=payload, headers=headers)
    return pid, cid, response


# --- migration ------------------------------------------------------------------------------------

def _reset_to_single_establishment_model(db):
    """Simulate a database from before multiple establishments existed."""
    db.query(Pharmacist).update({Pharmacist.establishment_id: None})
    db.query(Establishment).delete()
    db.commit()


def test_startup_migrates_configuration_and_links_existing_pharmacists(client, db):
    # the client fixture ran the startup sequence on an empty database: one establishment
    # created from the configuration (no invented CNPJ/address/phone)
    [est] = db.scalars(select(Establishment)).all()
    assert (est.name, est.cnpj, est.address, est.phone) == (
        "Programa de Aperfeiçoamento em Farmácia Clínica - PAFarC", "", "", "")
    farm = create_pharmacist(db, full_name="Farm Legado", crf="CRF/RJ 16906", login="legado", password=PASSWORD)
    assert farm.establishment_id == est.id


def test_legacy_saved_establishment_record_is_migrated(client, db):
    farm = create_pharmacist(db, full_name="Farm Legado", crf="CRF/RJ 16906", login="legado", password=PASSWORD)
    adm = create_pharmacist(db, full_name="Adm", crf="", login="adm", password=PASSWORD, role=ROLE_ADMINISTRATOR)
    _reset_to_single_establishment_model(db)
    db.add(EstablishmentSettings(id=1, name="Nome Salvo Legado", cnpj="00.000.000/0000-00",
                                 address="Endereço Legado, 1", phone="(00) 0000-0000"))
    db.commit()

    migrated = migrate_legacy_establishment(db)
    assert (migrated.name, migrated.cnpj, migrated.address, migrated.phone) == (
        "Nome Salvo Legado", "00.000.000/0000-00", "Endereço Legado, 1", "(00) 0000-0000")
    db.expire_all()
    assert db.get(Pharmacist, farm.id).establishment_id == migrated.id
    assert db.get(Pharmacist, adm.id).establishment_id is None  # administrators stay unlinked
    assert db.get(EstablishmentSettings, 1).name == "Nome Salvo Legado"  # legacy record kept (rollback)
    assert db.get(Pharmacist, farm.id).crf == "CRF/RJ 16906" and db.get(Pharmacist, farm.id).role == "pharmacist"


def test_migration_is_idempotent(client, db):
    farm = create_pharmacist(db, full_name="Farm", crf="CRF/RJ 1", login="farm", password=PASSWORD)
    before = [(e.id, e.name) for e in db.scalars(select(Establishment)).all()]
    for _ in range(3):
        assert migrate_legacy_establishment(db) is None
    assert [(e.id, e.name) for e in db.scalars(select(Establishment)).all()] == before
    db.expire_all()
    assert db.get(Pharmacist, farm.id).establishment_id == before[0][0]


def test_migration_leaves_clinical_records_untouched(client, db):
    """A database from before establishments (and before clinical ownership): the establishment is
    created from the configuration and owns every clinical record, whose content is untouched."""
    from backend.database import init_db
    from tests.test_clinical_isolation_migration import fill, make_legacy_layout, snapshot

    farm = create_pharmacist(db, full_name="Farm", crf="CRF/RJ 1", login="farm", password=PASSWORD).id
    make_legacy_layout()
    fill({"p1": farm, "p2": farm})
    before = snapshot()
    _reset_to_single_establishment_model(db)
    db.close()
    init_db()
    assert snapshot() == before
    with get_engine().connect() as c:
        [(est,)] = c.execute(text("SELECT id FROM establishments")).all()
        for table in ("patients", "consultations", "generated_documents"):
            assert {r[0] for r in c.execute(text(f"SELECT establishment_id FROM {table}"))} == {est}, table
        assert c.execute(text("SELECT establishment_id FROM pharmacists WHERE id = :i"), {"i": farm}).scalar() == est


def test_old_database_layout_gets_establishment_column(client):
    with get_engine().begin() as c:
        c.execute(text("DROP TABLE pharmacists"))
        c.execute(text(
            "CREATE TABLE pharmacists (id INTEGER PRIMARY KEY, full_name VARCHAR(200), crf VARCHAR(30), "
            "login VARCHAR(50) UNIQUE, password_hash VARCHAR(255), active BOOLEAN, created_at DATETIME)"))
    from backend.database import init_db
    init_db()
    assert "establishment_id" in {col["name"] for col in inspect(get_engine()).get_columns("pharmacists")}


# --- establishment administration --------------------------------------------------------------------

def test_admin_lists_creates_and_edits_establishments(client, admin):
    created = client.post("/admin/establishments", headers=admin, json={**EST_A, "cnpj": "11111111000111"})
    assert created.status_code == 201
    body = created.json()
    assert body["cnpj"] == "11.111.111/0001-11"  # stored formatted
    edited = client.patch(f"/admin/establishments/{body['id']}", headers=admin, json={"phone": "(21) 9999-9999"})
    assert edited.status_code == 200 and edited.json()["phone"] == "(21) 9999-9999"
    assert edited.json()["name"] == EST_A["name"]  # untouched fields kept
    names = [e["name"] for e in client.get("/admin/establishments", headers=admin).json()]
    assert EST_A["name"] in names and "Programa de Aperfeiçoamento em Farmácia Clínica - PAFarC" in names


def test_cnpj_rules(client, admin):
    a = client.post("/admin/establishments", headers=admin, json=EST_A).json()["id"]
    # same CNPJ in another format -> conflict
    assert client.post("/admin/establishments", headers=admin, json={**EST_B, "cnpj": "11111111000111"}).status_code == 409
    b = client.post("/admin/establishments", headers=admin, json=EST_B).json()["id"]
    assert client.patch(f"/admin/establishments/{b}", headers=admin, json={"cnpj": EST_A["cnpj"]}).status_code == 409
    # an establishment may keep its own CNPJ on edit; several may have none
    assert client.patch(f"/admin/establishments/{a}", headers=admin, json={"cnpj": EST_A["cnpj"]}).status_code == 200
    for name in ("Sem CNPJ 1", "Sem CNPJ 2"):
        assert client.post("/admin/establishments", headers=admin, json={"name": name}).status_code == 201
    for bad in ("123", "11.111.111/0001-1X", "1" * 15):
        assert client.post("/admin/establishments", headers=admin, json={"name": "X", "cnpj": bad}).status_code == 422
    assert client.post("/admin/establishments", headers=admin, json={"name": "  "}).status_code == 422
    assert client.patch("/admin/establishments/9999", headers=admin, json={"name": "X"}).status_code == 404


def test_pharmacist_cannot_manage_establishments(client, two):
    for method, url, body in (("get", "/admin/establishments", None), ("post", "/admin/establishments", EST_A),
                              ("patch", f"/admin/establishments/{two['a']}", {"name": "Invasor"})):
        kwargs = {"headers": two["ha"]} | ({"json": body} if body else {})
        assert getattr(client, method)(url, **kwargs).status_code == 403


# --- user / establishment links -------------------------------------------------------------------------

def test_new_pharmacist_requires_valid_establishment(client, admin):
    base = {"full_name": "Farm", "crf": "123", "crf_uf": "RJ", "login": "f@estab.test", "password": PASSWORD}
    assert client.post("/admin/users", headers=admin, json=base).status_code == 422
    assert client.post("/admin/users", headers=admin, json={**base, "establishment_id": 9999}).status_code == 422
    est = client.post("/admin/establishments", headers=admin, json=EST_A).json()["id"]
    created = client.post("/admin/users", headers=admin, json={**base, "establishment_id": est})
    assert created.status_code == 201
    assert (created.json()["establishment_id"], created.json()["establishment_name"]) == (est, EST_A["name"])


def test_administrator_needs_no_establishment(client, admin):
    base = {"full_name": "Adm", "login": "adm2@estab.test", "password": PASSWORD, "role": "administrator"}
    created = client.post("/admin/users", headers=admin, json=base)
    assert created.status_code == 201 and created.json()["establishment_id"] is None
    est = client.post("/admin/establishments", headers=admin, json=EST_A).json()["id"]
    linked = client.post("/admin/users", headers=admin, json={**base, "login": "adm3@estab.test", "establishment_id": est})
    assert linked.status_code == 422


def test_admin_changes_pharmacist_establishment_and_pharmacist_cannot(client, admin, two, db):
    uid = db.scalar(select(Pharmacist.id).where(Pharmacist.login == "farm.a@estab.test"))
    moved = client.patch(f"/admin/users/{uid}", headers=admin, json={"establishment_id": two["b"]})
    assert moved.status_code == 200 and moved.json()["establishment_name"] == EST_B["name"]
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"establishment_id": 9999}).status_code == 422
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"establishment_id": None}).status_code == 422
    # the pharmacist has no way to change it (administration routes are refused)
    assert client.patch(f"/admin/users/{uid}", headers=two["ha"], json={"establishment_id": two["a"]}).status_code == 403
    me = client.get("/auth/me", headers=two["ha"]).json()
    assert me["establishment_id"] == two["b"]


def test_role_changes_and_establishment(client, admin, two, db):
    adm = client.post("/admin/users", headers=admin, json={
        "full_name": "Adm Muda", "login": "adm.muda@estab.test", "password": PASSWORD, "role": "administrator"}).json()["id"]
    to_pharmacist = {"role": "pharmacist", "crf": "4321", "crf_uf": "ES"}
    assert client.patch(f"/admin/users/{adm}", headers=admin, json=to_pharmacist).status_code == 422  # no establishment
    assert client.patch(f"/admin/users/{adm}", headers=admin, json={"role": "pharmacist", "establishment_id": two["a"]}).status_code == 422  # no CRF
    ok = client.patch(f"/admin/users/{adm}", headers=admin, json={**to_pharmacist, "establishment_id": two["a"]})
    assert ok.status_code == 200 and ok.json()["establishment_id"] == two["a"]
    # pharmacist -> administrator clears the link
    back = client.patch(f"/admin/users/{adm}", headers=admin, json={"role": "administrator"})
    assert back.status_code == 200 and back.json()["establishment_id"] is None


# --- documents -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", list(DOC_PAYLOADS))
def test_each_pharmacist_documents_use_their_own_establishment(client, two, kind):
    for headers, own, other, name, crf, other_crf in (
        (two["ha"], EST_A, EST_B, "Farmacêutica A", "CRF/RJ 10001", "CRF/RJ 10002"),
        (two["hb"], EST_B, EST_A, "Farmacêutico B", "CRF/RJ 10002", "CRF/RJ 10001"),
    ):
        _, _, response = _issue(client, headers, kind)
        assert response.status_code == 200
        body = pdf_text(response.content)
        for value in (own["name"], f"CNPJ {own['cnpj']}", own["address"], f"Tel. {own['phone']}", name, crf):
            assert value in body, (kind, value)
        for value in (other["name"], other["cnpj"], other["address"], other_crf):
            assert value not in body, (kind, value)


def test_payload_cannot_spoof_establishment(client, two):
    spoof = {"establishment_id": two["b"], "establishment": EST_B, "establishment_name": EST_B["name"],
             "cnpj": EST_B["cnpj"], "pharmacist_id": 1}
    _, _, response = _issue(client, two["ha"], "referral", spoof)
    assert response.status_code == 200
    body = pdf_text(response.content)
    assert EST_A["name"] in body and EST_B["name"] not in body and EST_B["cnpj"] not in body


def test_establishment_change_affects_only_new_documents(client, admin, two, db):
    pid, cid, first = _issue(client, two["ha"])
    assert EST_A["name"] in pdf_text(first.content)
    doc_id = client.get(f"/patients/{pid}/consultations/{cid}", headers=two["ha"]).json()["documents"][0]["id"]
    client.post("/admin/users", headers=admin, json={"full_name": "Colega A", "crf": "10003", "crf_uf": "RJ",
                                                     "login": "colega.a@estab.test", "password": PASSWORD,
                                                     "role": "pharmacist", "establishment_id": two["a"]})
    colleague = _headers(client, "colega.a@estab.test")
    uid = db.scalar(select(Pharmacist.id).where(Pharmacist.login == "farm.a@estab.test"))
    client.patch(f"/admin/users/{uid}", headers=admin, json={"establishment_id": two["b"]})
    client.patch(f"/admin/establishments/{two['a']}", headers=admin, json={"name": "A Renomeada"})

    _, _, new = _issue(client, two["ha"])  # now in B: an independent record of the same person in B
    assert EST_B["name"] in pdf_text(new.content) and EST_A["name"] not in pdf_text(new.content)
    url = f"/patients/{pid}/consultations/{cid}/documents/{doc_id}"
    assert client.get(url, headers=two["ha"]).status_code == 404  # A's records stayed in A
    assert client.get(url, headers=colleague).content == first.content  # historical PDF byte-identical


def test_unlinked_pharmacist_cannot_issue_documents(client, two, db):
    pid = client.post("/patients", json=PATIENT, headers=two["ha"]).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=two["ha"]).json()["id"]
    farm = db.scalar(select(Pharmacist).where(Pharmacist.login == "farm.a@estab.test"))
    farm.establishment_id = None  # e.g. an unexpected legacy state
    db.commit()
    response = client.post(f"/patients/{pid}/consultations/{cid}/documents/prescription",
                           json=DOC_PAYLOADS["prescription"], headers=two["ha"])
    assert response.status_code == 409
    assert "estabelecimento" in response.json()["detail"]
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM generated_documents")).scalar() == 0


# --- clinical data is separated by establishment (client decision) ------------------------------------

def test_clinical_data_is_not_shared_across_establishments(client, two):
    pid = client.post("/patients", json=PATIENT, headers=two["ha"]).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=two["ha"]).json()["id"]
    # a pharmacist of establishment B neither finds nor reads establishment A's patient and consultation
    assert client.post("/patients/search", json={"query": "Paciente Ficticio"}, headers=two["hb"]).json() == []
    assert client.get(f"/patients/{pid}/consultations/{cid}", headers=two["hb"]).status_code == 404
    assert client.get(f"/patients/{pid}/consultations", headers=two["hb"]).status_code == 404


def test_establishment_column_on_owning_clinical_tables_only(client):
    insp = inspect(get_engine())
    for table in ("patients", "consultations", "generated_documents"):
        assert "establishment_id" in {c["name"] for c in insp.get_columns(table)}, table
    for table in ("soap_records", "exam_results", "patient_name_tokens"):  # inherit through their parent
        assert "establishment_id" not in {c["name"] for c in insp.get_columns(table)}, table


def test_admin_still_denied_clinical_and_pharmacist_denied_admin(client, admin, two):
    assert client.post("/patients/search", json={"query": "abc"}, headers=admin).status_code == 403
    assert client.get("/admin/users", headers=two["ha"]).status_code == 403
