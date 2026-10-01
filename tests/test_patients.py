from pathlib import Path

import pytest
from sqlalchemy import text

from backend.auth import create_pharmacist
from backend.database import get_engine
from backend.patients import normalize_cpf, normalize_name

# Fictitious data; the CPFs are checksum-valid test numbers.
CPF = "12345678909"
CPF_FORMATTED = "123.456.789-09"
OTHER_CPF = "98765432100"
NAME = "Maria José da Silva"
DOB = "1980-05-17"


@pytest.fixture
def auth_headers(client, db):
    create_pharmacist(
        db, full_name="Ana Souza", crf="CRF-SP 12345", login="ana.souza", password="pw-123456"
    )
    response = client.post("/auth/login", data={"username": "ana.souza", "password": "pw-123456"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create(client, headers, name=NAME, cpf=CPF_FORMATTED, dob=DOB):
    return client.post(
        "/patients", json={"full_name": name, "date_of_birth": dob, "cpf": cpf}, headers=headers
    )


def _search(client, headers, query):
    return client.post("/patients/search", json={"query": query}, headers=headers)


def test_normalize_cpf():
    assert normalize_cpf(" 123.456.789-09 ") == CPF
    assert normalize_cpf(CPF) == CPF
    for bad in ["", "123", "123456789099", "12345678900", "11111111111", "123.456.789-0a"]:
        with pytest.raises(ValueError):
            normalize_cpf(bad)


def test_normalize_name():
    assert normalize_name("  MARIA   JOSÉ  da Silva ") == normalize_name("maria jose da silva")


def test_create_patient(client, auth_headers):
    response = _create(client, auth_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == NAME
    assert body["date_of_birth"] == DOB
    assert body["cpf"] == CPF  # stored normalized
    assert body["created_at"] and body["updated_at"]


def test_create_patient_requires_authentication(client):
    assert _create(client, {}).status_code == 401


def test_patient_endpoints_reject_invalid_token(client):
    headers = {"Authorization": "Bearer not-a-token"}
    assert _search(client, headers, CPF).status_code == 401
    assert client.get("/patients/1", headers=headers).status_code == 401


def test_duplicate_cpf_is_rejected(client, auth_headers):
    assert _create(client, auth_headers).status_code == 201
    response = _create(client, auth_headers, name="Outra Pessoa", cpf=CPF)
    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": "   ", "date_of_birth": DOB, "cpf": CPF},
        {"full_name": NAME, "date_of_birth": "17/05/1980", "cpf": CPF},
        {"full_name": NAME, "date_of_birth": "2999-01-01", "cpf": CPF},
        {"full_name": NAME, "date_of_birth": DOB, "cpf": "12345678900"},
        {"full_name": NAME, "date_of_birth": DOB, "cpf": "1234567890"},
        {"date_of_birth": DOB, "cpf": CPF},
    ],
)
def test_invalid_patient_data_is_rejected(client, auth_headers, payload):
    response = client.post("/patients", json=payload, headers=auth_headers)
    assert response.status_code == 422
    # Submitted values must not be echoed back in error responses.
    assert "1234567890" not in response.text
    assert "Maria" not in response.text


def test_search_by_cpf(client, auth_headers):
    created = _create(client, auth_headers).json()
    _create(client, auth_headers, name="Outra Pessoa", cpf=OTHER_CPF)
    for query in (CPF, CPF_FORMATTED):
        response = _search(client, auth_headers, query)
        assert response.status_code == 200
        assert [p["id"] for p in response.json()] == [created["id"]]


def test_search_by_normalized_name(client, auth_headers):
    created = _create(client, auth_headers).json()
    _create(client, auth_headers, name="Outra Pessoa", cpf=OTHER_CPF)
    response = _search(client, auth_headers, "  maria   JOSE da silva ")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == [created["id"]]


def test_search_without_match_returns_empty(client, auth_headers):
    _create(client, auth_headers)
    assert _search(client, auth_headers, "Maria").json() == []  # exact match only
    assert _search(client, auth_headers, OTHER_CPF).json() == []


def test_search_with_malformed_cpf_is_rejected(client, auth_headers):
    assert _search(client, auth_headers, "123.456").status_code == 422


def test_get_patient_profile(client, auth_headers):
    created = _create(client, auth_headers).json()
    response = client.get(f"/patients/{created['id']}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert (body["full_name"], body["date_of_birth"], body["cpf"]) == (NAME, DOB, CPF)


def test_get_unknown_patient_returns_404(client, auth_headers):
    assert client.get("/patients/9999", headers=auth_headers).status_code == 404


def test_stored_values_are_not_plaintext(client, auth_headers):
    assert _create(client, auth_headers).status_code == 201
    with get_engine().connect() as connection:
        row = connection.execute(text("SELECT * FROM patients")).mappings().one()
    stored = " ".join(str(v) for v in row.values())
    for plaintext in (CPF, CPF_FORMATTED, NAME, "Maria", "Silva", DOB):
        assert plaintext not in stored

    # And not anywhere in the raw SQLite file either.
    get_engine().dispose()
    raw = Path(get_engine().url.database).read_bytes()
    assert raw  # the database file was actually written
    for plaintext in (CPF, NAME, "Maria", "Silva", DOB):
        assert plaintext.encode() not in raw
