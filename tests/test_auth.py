import pytest
from sqlalchemy import text

from backend.auth import create_pharmacist
from backend.database import get_engine
from backend.models import Pharmacist
from backend.security import create_access_token

PASSWORD = "correct-horse-42"


@pytest.fixture
def pharmacist(db) -> Pharmacist:
    return create_pharmacist(
        db, full_name="Ana Souza", crf="CRF-SP 12345", login="ana.souza", password=PASSWORD
    )


def _login(client, login: str, password: str):
    return client.post("/auth/login", data={"username": login, "password": password})


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_pharmacist(db, pharmacist):
    stored = db.get(Pharmacist, pharmacist.id)
    assert stored.full_name == "Ana Souza"
    assert stored.crf == "CRF-SP 12345"
    assert stored.login == "ana.souza"
    assert stored.active is True
    assert stored.created_at is not None


def test_password_is_stored_hashed(db, pharmacist):
    stored = db.get(Pharmacist, pharmacist.id)
    assert stored.password_hash != PASSWORD
    assert PASSWORD not in stored.password_hash
    assert stored.password_hash.startswith("$2")


def test_duplicate_login_is_rejected(db, pharmacist):
    with pytest.raises(ValueError, match="already in use"):
        create_pharmacist(
            db, full_name="Other", crf="CRF-SP 999", login="ANA.Souza", password=PASSWORD
        )


def test_valid_login_succeeds(client, pharmacist):
    response = _login(client, "ana.souza", PASSWORD)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_wrong_password_fails(client, pharmacist):
    response = _login(client, "ana.souza", "wrong-password")
    assert response.status_code == 401
    assert "access_token" not in response.json()


def test_unknown_user_fails(client, pharmacist):
    response = _login(client, "nobody", PASSWORD)
    assert response.status_code == 401


def test_me_requires_authentication(client):
    assert client.get("/auth/me").status_code == 401


def test_me_rejects_invalid_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


def test_me_returns_authenticated_pharmacist(client, pharmacist):
    token = _login(client, "ana.souza", PASSWORD).json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pharmacist.id
    assert body["full_name"] == "Ana Souza"
    assert body["crf"] == "CRF-SP 12345"
    assert body["login"] == "ana.souza"
    assert "password_hash" not in body


def test_inactive_pharmacist_cannot_authenticate(client, db, pharmacist):
    pharmacist.active = False
    db.commit()
    response = _login(client, "ana.souza", PASSWORD)
    assert response.status_code == 403
    assert "access_token" not in response.json()


def test_deactivated_pharmacist_token_is_rejected(client, db, pharmacist):
    token = create_access_token(pharmacist.id)
    pharmacist.active = False
    db.commit()
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_sqlite_foreign_keys_enabled():
    with get_engine().connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_email_address_can_be_used_as_login(client, db):
    create_pharmacist(
        db, full_name="Farmacêutico Email", crf="CRF/RJ 00000", login=" Nome.Teste@Exemplo.com ",
        password=PASSWORD,
    )
    response = client.post("/auth/login", data={"username": "NOME.TESTE@exemplo.com", "password": PASSWORD})
    assert response.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}).json()
    assert me["login"] == "nome.teste@exemplo.com"
    assert (me["full_name"], me["crf"]) == ("Farmacêutico Email", "CRF/RJ 00000")


def test_invalid_logins_are_rejected(db):
    for bad in ("ab", "with space@x.com", "acentuação", "a" * 51, "semi;colon"):
        with pytest.raises(ValueError, match="Login must be"):
            create_pharmacist(db, full_name="X", crf="CRF 1", login=bad, password=PASSWORD)
