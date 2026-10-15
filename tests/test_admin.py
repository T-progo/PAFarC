import io
import sys

import pytest
from pypdf import PdfReader
from sqlalchemy import inspect, select, text

from backend.auth import backfill_crf_uf, create_pharmacist
from backend.database import get_engine, init_db
from backend.models import ROLE_ADMINISTRATOR, ROLE_PHARMACIST, Pharmacist, parse_legacy_crf

PASSWORD = "senha-segura-1"
PATIENT = {"full_name": "Paciente Ficticio Admin", "date_of_birth": "1970-01-15", "cpf": "12345678909"}


def _login(client, login, password=PASSWORD):
    return client.post("/auth/login", data={"username": login, "password": password})


def _headers(client, login, password=PASSWORD):
    return {"Authorization": f"Bearer {_login(client, login, password).json()['access_token']}"}


@pytest.fixture
def admin(client, db):
    create_pharmacist(db, full_name="Admin Teste", crf="00001", crf_uf="RJ", login="admin@pafarc.test",
                      password=PASSWORD, role=ROLE_ADMINISTRATOR)
    return _headers(client, "admin@pafarc.test")


@pytest.fixture
def pharmacist(client, db):
    create_pharmacist(db, full_name="Farmacêutica Teste", crf="CRF-SP 11111", login="farm.teste",
                      password=PASSWORD)
    return _headers(client, "farm.teste")


def _est(client, headers) -> int:
    """Id of the default establishment (migrated from the configuration at startup)."""
    return client.get("/admin/establishments", headers=headers).json()[0]["id"]


def _user_id(db, login):
    return db.scalar(select(Pharmacist.id).where(Pharmacist.login == login))


def pdf_text(data):
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)


# --- roles and migration --------------------------------------------------------------------

def test_new_accounts_default_to_pharmacist(client, db, pharmacist):
    me = client.get("/auth/me", headers=pharmacist).json()
    assert (me["role"], me["is_admin"], me["clinical_access"]) == (ROLE_PHARMACIST, False, True)


def test_existing_database_upgrade_defaults_role_and_extracts_uf(client):
    with get_engine().begin() as connection:  # recreate the pre-roles table with legacy rows
        connection.execute(text("DROP TABLE pharmacists"))
        connection.execute(text(
            "CREATE TABLE pharmacists (id INTEGER PRIMARY KEY, full_name VARCHAR(200), crf VARCHAR(30), "
            "login VARCHAR(50) UNIQUE, password_hash VARCHAR(255), active BOOLEAN, created_at DATETIME)"))
        connection.execute(text(
            "INSERT INTO pharmacists VALUES (1, 'Darlan Ferreira de Souza', 'CRF/RJ 16906', "
            "'darlan.teste@exemplo.test', 'x', 1, '2026-10-03'), (2, 'Outro', 'CRF 1', 'outro', 'x', 1, '2026-10-03')"))
    init_db()
    from backend.database import get_sessionmaker
    with get_sessionmaker()() as db:
        assert backfill_crf_uf(db) == 1
        darlan, other = db.get(Pharmacist, 1), db.get(Pharmacist, 2)
        assert (darlan.role, darlan.crf, darlan.crf_uf) == (ROLE_PHARMACIST, "CRF/RJ 16906", "RJ")
        assert darlan.crf_display == "CRF/RJ 16906" and darlan.crf_number == "16906"
        assert (other.role, other.crf_uf, other.crf_display) == (ROLE_PHARMACIST, None, "CRF 1")
        assert backfill_crf_uf(db) == 0  # idempotent
    assert {"role", "crf_uf"} <= {c["name"] for c in inspect(get_engine()).get_columns("pharmacists")}


@pytest.mark.parametrize("value,expected", [
    ("CRF/RJ 16906", ("16906", "RJ")), ("CRF-SP 00001", ("00001", "SP")), ("crf rj 123", ("123", "RJ")),
    ("CRF 16906/RJ", ("16906", "RJ")), ("CRF-UF 00000", None), ("16906", None), ("CRF 1", None),
])
def test_parse_legacy_crf(value, expected):
    assert parse_legacy_crf(value) == expected


def test_crf_and_uf_stored_separately_for_new_users(client, admin):
    est = _est(client, admin)
    response = client.post("/admin/users", headers=admin, json={
        "full_name": "Novo Farmacêutico", "crf": "16906", "crf_uf": "rj", "login": "novo@pafarc.test",
        "password": PASSWORD, "establishment_id": est})
    assert response.status_code == 201
    body = response.json()
    assert (body["crf"], body["crf_number"], body["crf_uf"], body["role"]) == ("CRF/RJ 16906", "16906", "RJ", "pharmacist")
    for bad in ({"crf": "16906", "crf_uf": "XX"}, {"crf": "CRF/RJ 16906", "crf_uf": "RJ"}, {"crf": "", "crf_uf": "RJ"}):
        r = client.post("/admin/users", headers=admin, json={
            "full_name": "X", "login": "x@pafarc.test", "password": PASSWORD, "establishment_id": est, **bad})
        assert r.status_code == 422, bad


# --- authorization ----------------------------------------------------------------------------

ADMIN_CALLS = [
    ("get", "/admin/users", None),
    ("post", "/admin/users", {"full_name": "X", "crf": "1", "crf_uf": "RJ", "login": "xx@x.test", "password": PASSWORD}),
    ("patch", "/admin/users/1", {"full_name": "Y"}),
    ("post", "/admin/users/1/deactivate", None),
    ("post", "/admin/users/1/activate", None),
    ("post", "/admin/users/1/password", {"password": PASSWORD}),
    ("get", "/admin/establishments", None),
    ("post", "/admin/establishments", {"name": "Outro"}),
    ("patch", "/admin/establishments/1", {"name": "Outro"}),
]


@pytest.mark.parametrize("method,url,body", ADMIN_CALLS)
def test_pharmacist_gets_403_on_every_admin_endpoint(client, pharmacist, method, url, body):
    kwargs = {"headers": pharmacist} | ({"json": body} if body is not None else {})
    assert getattr(client, method)(url, **kwargs).status_code == 403


@pytest.mark.parametrize("method,url,body", ADMIN_CALLS)
def test_admin_endpoints_require_authentication(client, method, url, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(client, method)(url, **kwargs).status_code == 401


def test_admin_lists_users_without_password_hashes(client, admin, pharmacist):
    response = client.get("/admin/users", headers=admin)
    assert response.status_code == 200
    users = response.json()
    assert {u["login"] for u in users} == {"admin@pafarc.test", "farm.teste"}
    assert all("password" not in key for u in users for key in u)
    assert "$2" not in response.text


def test_administrator_has_no_clinical_access(client, admin):
    me = client.get("/auth/me", headers=admin).json()
    assert (me["is_admin"], me["clinical_access"]) == (True, False)


def _clinical_routes():
    """Every clinical route, read from the API itself (new routes are covered automatically)."""
    from backend.main import app
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/patients"):
            for method in methods:
                yield method, path


CLINICAL_ROUTES = sorted(_clinical_routes())


def test_clinical_route_list_is_complete():
    assert len(CLINICAL_ROUTES) >= 15  # patients, consultations, SOAP, exams, documents, reprint


@pytest.mark.parametrize("method,path", CLINICAL_ROUTES)
def test_administrator_denied_on_every_clinical_route(client, admin, method, path):
    url = path.replace("{patient_id}", "1").replace("{consultation_id}", "1")
    url = url.replace("{exam_id}", "1").replace("{document_id}", "1")
    response = client.request(method.upper(), url, headers=admin, json={})
    assert response.status_code == 403, (method, path, response.status_code)


def test_administrator_cannot_reach_existing_patient_data(client, admin, pharmacist):
    pid = client.post("/patients", json=PATIENT, headers=pharmacist).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=pharmacist).json()["id"]
    base = f"/patients/{pid}/consultations/{cid}"
    for method, url, body in (
        ("GET", f"/patients/{pid}", None),
        ("POST", "/patients/search", {"query": PATIENT["cpf"]}),
        ("GET", f"/patients/{pid}/consultations", None),
        ("GET", base, None),
        ("POST", f"/patients/{pid}/consultations", {}),
        ("PUT", f"{base}/soap", {"subjective": "x"}),
        ("POST", f"{base}/exams", {"exam_name": "Ureia", "result": "1"}),
        ("POST", f"{base}/documents/prescription", {"items": [{"medication": "x"}]}),
        ("POST", f"{base}/documents/exam-request", {"exams": ["Ureia"], "clinical_justification": "x"}),
        ("POST", f"{base}/documents/referral", {"destination": "x", "case_summary": "x", "prm": "x",
                                                 "suggested_conduct": "x"}),
    ):
        response = client.request(method, url, headers=admin, json=body)
        assert response.status_code == 403, (method, url)
        assert "Paciente Ficticio" not in response.text
    # the pharmacist's own work is untouched and still accessible to the pharmacist
    assert client.get(base, headers=pharmacist).json()["soap"] is None


def test_pharmacist_keeps_full_clinical_access(client, pharmacist):
    me = client.get("/auth/me", headers=pharmacist).json()
    assert (me["is_admin"], me["clinical_access"]) == (False, True)
    pid = client.post("/patients", json=PATIENT, headers=pharmacist).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=pharmacist).json()["id"]
    base = f"/patients/{pid}/consultations/{cid}"
    assert client.put(f"{base}/soap", json={"subjective": "s"}, headers=pharmacist).status_code == 200
    assert client.post(f"{base}/exams", json={"exam_name": "Ureia", "result": "38"}, headers=pharmacist).status_code == 201
    assert client.post("/patients/search", json={"query": "Paciente"}, headers=pharmacist).json()[0]["id"] == pid


def test_role_change_takes_effect_immediately(client, admin, pharmacist, db):
    uid = _user_id(db, "farm.teste")
    assert client.post("/patients", json=PATIENT, headers=pharmacist).status_code == 201
    client.patch(f"/admin/users/{uid}", headers=admin, json={"role": "administrator"})
    # same token: now an administrator, so clinical routes are refused and admin routes allowed
    assert client.post("/patients/search", json={"query": "Paciente"}, headers=pharmacist).status_code == 403
    assert client.get("/admin/users", headers=pharmacist).status_code == 200


# --- user management ----------------------------------------------------------------------------

def test_create_edit_block_reactivate_and_reset(client, db, admin):
    created = client.post("/admin/users", headers=admin, json={
        "full_name": "Ana Lima", "crf": "12345", "crf_uf": "SP", "login": "ana@pafarc.test", "password": PASSWORD,
        "establishment_id": _est(client, admin)})
    assert created.status_code == 201
    uid = created.json()["id"]
    assert _login(client, "ana@pafarc.test").status_code == 200

    edited = client.patch(f"/admin/users/{uid}", headers=admin, json={
        "full_name": "Ana Lima Souza", "crf": "54321", "crf_uf": "MG", "login": "ana.souza@pafarc.test"})
    assert edited.status_code == 200
    assert (edited.json()["full_name"], edited.json()["crf"], edited.json()["login"]) == (
        "Ana Lima Souza", "CRF/MG 54321", "ana.souza@pafarc.test")
    assert _login(client, "ana@pafarc.test").status_code == 401
    session = _headers(client, "ana.souza@pafarc.test")

    blocked = client.post(f"/admin/users/{uid}/deactivate", headers=admin)
    assert blocked.status_code == 200 and blocked.json()["active"] is False
    assert _login(client, "ana.souza@pafarc.test").status_code == 403
    assert client.get("/auth/me", headers=session).status_code == 401  # existing session ends too

    reactivated = client.post(f"/admin/users/{uid}/activate", headers=admin)
    assert reactivated.json()["active"] is True
    assert _login(client, "ana.souza@pafarc.test").status_code == 200

    assert client.post(f"/admin/users/{uid}/password", headers=admin, json={"password": "nova-senha-22"}).status_code == 204
    assert _login(client, "ana.souza@pafarc.test").status_code == 401
    assert _login(client, "ana.souza@pafarc.test", "nova-senha-22").status_code == 200
    stored = db.get(Pharmacist, uid)
    db.refresh(stored)
    assert stored.password_hash.startswith("$2") and "nova-senha-22" not in stored.password_hash


def test_user_validation_and_uniqueness(client, admin, pharmacist, db):
    dup = client.post("/admin/users", headers=admin, json={
        "full_name": "Dup", "crf": "1", "crf_uf": "RJ", "login": "FARM.TESTE", "password": PASSWORD,
        "establishment_id": _est(client, admin)})
    assert dup.status_code == 409
    uid = _user_id(db, "farm.teste")
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"login": "admin@pafarc.test"}).status_code == 409
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"login": "bad login"}).status_code == 422
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"role": "superuser"}).status_code == 422
    assert client.post(f"/admin/users/{uid}/password", headers=admin, json={"password": "curta"}).status_code == 422
    assert client.patch("/admin/users/9999", headers=admin, json={"full_name": "X"}).status_code == 404


def test_admin_cannot_lock_themselves_out(client, admin, db):
    me = _user_id(db, "admin@pafarc.test")
    assert client.post(f"/admin/users/{me}/deactivate", headers=admin).status_code == 409
    assert client.patch(f"/admin/users/{me}", headers=admin, json={"role": "pharmacist"}).status_code == 409
    assert client.get("/admin/users", headers=admin).status_code == 200  # still an administrator


def test_last_active_administrator_is_protected(client, admin, db):
    second = client.post("/admin/users", headers=admin, json={
        "full_name": "Admin Dois", "crf": "2", "crf_uf": "RJ", "login": "admin2@pafarc.test",
        "password": PASSWORD, "role": "administrator"}).json()["id"]
    first = _user_id(db, "admin@pafarc.test")
    other = _headers(client, "admin2@pafarc.test")
    # with two administrators, one may block the other...
    assert client.post(f"/admin/users/{first}/deactivate", headers=other).status_code == 200
    # ...but then the remaining one cannot be removed (CLI path, no actor)
    from backend.admin import AdminRuleError, set_role
    with pytest.raises(AdminRuleError):
        set_role(db, db.get(Pharmacist, second), ROLE_PHARMACIST)


def test_promoting_a_pharmacist_grants_admin_access(client, admin, pharmacist, db):
    uid = _user_id(db, "farm.teste")
    assert client.get("/admin/users", headers=pharmacist).status_code == 403
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"role": "administrator"}).status_code == 200
    assert client.get("/admin/users", headers=pharmacist).status_code == 200  # same token, role read per request


# --- command-line script ------------------------------------------------------------------------

def _run_cli(monkeypatch, *args, password=PASSWORD):
    import getpass
    from scripts import create_pharmacist as cli
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": password)
    monkeypatch.setattr(sys, "argv", ["create_pharmacist", *args])
    cli.main()


def test_cli_creates_administrator_and_sets_roles(client, db, monkeypatch, capsys):
    _run_cli(monkeypatch, "--full-name", "Primeiro Admin", "--crf", "16906", "--uf", "RJ",
             "--login", "primeiro@pafarc.test", "--role", "administrator")
    _run_cli(monkeypatch, "--full-name", "Farm CLI", "--crf", "CRF/SP 777", "--login", "farm.cli")
    db.expire_all()
    admin_user = db.scalar(select(Pharmacist).where(Pharmacist.login == "primeiro@pafarc.test"))
    farm = db.scalar(select(Pharmacist).where(Pharmacist.login == "farm.cli"))
    assert (admin_user.role, admin_user.crf_display) == (ROLE_ADMINISTRATOR, "CRF/RJ 16906")
    assert (farm.role, farm.crf_uf) == (ROLE_PHARMACIST, "SP")
    _run_cli(monkeypatch, "--set-role", "FARM.CLI", "--role", "administrator")
    db.expire_all()
    assert db.get(Pharmacist, farm.id).role == ROLE_ADMINISTRATOR
    assert "role=administrator" in capsys.readouterr().out


# --- documents --------------------------------------------------------------------------------------

def _issue(client, headers, kind="prescription", payload=None):
    pid = client.post("/patients", json=PATIENT, headers=headers).json()["id"]
    cid = client.post(f"/patients/{pid}/consultations", json={}, headers=headers).json()["id"]
    payload = payload or {"items": [{"medication": "Item de teste"}]}
    response = client.post(f"/patients/{pid}/consultations/{cid}/documents/{kind}", json=payload, headers=headers)
    assert response.status_code == 200
    return pid, cid, response.content


DOC_PAYLOADS = {
    "prescription": {"items": [{"medication": "Item de teste"}]},
    "exam-request": {"exams": ["Ureia"], "clinical_justification": "Justificativa de teste."},
    "referral": {"destination": "Nefrologia", "case_summary": "Resumo.", "prm": "PRM.", "suggested_conduct": "Conduta."},
}


@pytest.mark.parametrize("kind", list(DOC_PAYLOADS))
def test_documents_use_authenticated_pharmacist_and_database_establishment(client, admin, db, kind):
    est = client.post("/admin/establishments", headers=admin, json={
        "name": "Estabelecimento Fictício de Teste", "cnpj": "11.111.111/1111-11",
        "address": "Rua Fictícia, 100", "phone": "(21) 0000-0000"}).json()["id"]
    client.post("/admin/users", headers=admin, json={
        "full_name": "Darlan Teste Documento", "crf": "16906", "crf_uf": "RJ", "login": "doc@pafarc.test",
        "password": PASSWORD, "establishment_id": est})
    _, _, pdf = _issue(client, _headers(client, "doc@pafarc.test"), kind, DOC_PAYLOADS[kind])
    body = pdf_text(pdf)
    for value in ("Darlan Teste Documento", "CRF/RJ 16906", "Estabelecimento Fictício de Teste",
                  "CNPJ 11.111.111/1111-11", "Rua Fictícia, 100", "Tel. (21) 0000-0000"):
        assert value in body, (kind, value)
    assert "Admin Teste" not in body


def test_documents_use_migrated_default_establishment(client, pharmacist):
    _, _, pdf = _issue(client, pharmacist)
    body = pdf_text(pdf)
    assert "Programa de Aperfeiçoamento em Farmácia Clínica - PAFarC" in body
    assert "CNPJ" not in body and "Tel." not in body
    assert "Farmacêutica Teste" in body and "CRF-SP 11111" in body  # legacy CRF shown exactly as stored


def test_reprint_unchanged_after_establishment_and_user_edits(client, admin, pharmacist, db):
    pid, cid, original = _issue(client, pharmacist)
    doc_id = client.get(f"/patients/{pid}/consultations/{cid}", headers=pharmacist).json()["documents"][0]["id"]
    client.patch(f"/admin/establishments/{_est(client, admin)}", headers=admin,
                 json={"name": "Nome Novo", "cnpj": "22.222.222/2222-22"})
    uid = _user_id(db, "farm.teste")
    client.patch(f"/admin/users/{uid}", headers=admin, json={"full_name": "Nome Alterado", "crf": "999", "crf_uf": "BA"})
    reprint = client.get(f"/patients/{pid}/consultations/{cid}/documents/{doc_id}", headers=pharmacist)
    assert reprint.content == original
    assert "Nome Novo" not in pdf_text(reprint.content)


# --- CRF/UF: optional for administrators, required for pharmacists -----------------------------

def test_administrator_can_be_created_without_crf(client, admin, db):
    response = client.post("/admin/users", headers=admin, json={
        "full_name": "Administrador Sem CRF", "login": "adm.semcrf@pafarc.test", "password": PASSWORD,
        "role": "administrator"})
    assert response.status_code == 201
    body = response.json()
    assert (body["crf"], body["crf_number"], body["crf_uf"], body["role"]) == ("", "", None, "administrator")
    new_admin = _headers(client, "adm.semcrf@pafarc.test")
    assert client.get("/admin/users", headers=new_admin).status_code == 200
    # still no clinical access
    assert client.post("/patients/search", json={"query": "abc"}, headers=new_admin).status_code == 403
    assert client.post("/patients", json=PATIENT, headers=new_admin).status_code == 403


@pytest.mark.parametrize("crf_fields", [{}, {"crf": ""}, {"crf": "16906"}, {"crf_uf": "RJ"}, {"crf": "", "crf_uf": "RJ"}])
def test_pharmacist_cannot_be_created_without_crf_and_uf(client, admin, crf_fields):
    response = client.post("/admin/users", headers=admin, json={
        "full_name": "Farmacêutico Sem CRF", "login": "farm.semcrf@pafarc.test", "password": PASSWORD,
        "role": "pharmacist", "establishment_id": _est(client, admin), **crf_fields})
    assert response.status_code == 422, crf_fields


def test_crf_rules_on_edit(client, admin, db):
    created = client.post("/admin/users", headers=admin, json={
        "full_name": "Adm Dois", "login": "adm2@pafarc.test", "password": PASSWORD, "role": "administrator"}).json()
    uid = created["id"]
    # an administrator without CRF cannot become a pharmacist unless a CRF/UF is given
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"role": "pharmacist"}).status_code == 422
    db.expire_all()
    assert db.get(Pharmacist, uid).role == ROLE_ADMINISTRATOR
    ok = client.patch(f"/admin/users/{uid}", headers=admin, json={
        "role": "pharmacist", "crf": "4321", "crf_uf": "ES", "establishment_id": _est(client, admin)})
    assert ok.status_code == 200 and ok.json()["crf"] == "CRF/ES 4321"


def test_cli_creates_administrator_without_crf(client, db, monkeypatch):
    _run_cli(monkeypatch, "--full-name", "Admin CLI", "--login", "admin.cli@pafarc.test", "--role", "administrator")
    db.expire_all()
    user = db.scalar(select(Pharmacist).where(Pharmacist.login == "admin.cli@pafarc.test"))
    assert (user.role, user.crf, user.crf_uf, user.crf_display) == (ROLE_ADMINISTRATOR, "", None, "")


def test_existing_pharmacist_accounts_unchanged_by_crf_rules(client, admin, pharmacist, db):
    uid = _user_id(db, "farm.teste")
    before = db.get(Pharmacist, uid)
    snapshot = (before.crf, before.crf_uf, before.role, before.crf_display)
    # editing other fields of a legacy account does not re-validate or rewrite its CRF
    assert client.patch(f"/admin/users/{uid}", headers=admin, json={"full_name": "Farmacêutica Renomeada"}).status_code == 200
    db.expire_all()
    after = db.get(Pharmacist, uid)
    assert (after.crf, after.crf_uf, after.role, after.crf_display) == snapshot == ("CRF-SP 11111", "SP", "pharmacist", "CRF-SP 11111")
    assert _login(client, "farm.teste").status_code == 200
