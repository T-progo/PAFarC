from datetime import date

import pytest
from cryptography.fernet import Fernet

from backend.auth import MAX_FAILED_LOGINS, create_pharmacist
from backend.config import get_settings
from backend.crypto import get_crypto
from backend.patients import create_patient, verify_keys_match_existing_data

PASSWORD = "correct-horse-42"


def _login(client, login, password):
    return client.post("/auth/login", data={"username": login, "password": password})


def test_repeated_failed_logins_lock_the_account(client, db):
    create_pharmacist(db, full_name="Ana Souza", crf="CRF-SP 1", login="ana.souza", password=PASSWORD)
    for _ in range(MAX_FAILED_LOGINS):
        assert _login(client, "ana.souza", "wrong-password").status_code == 401
    # Locked now, even with the right password (and regardless of case/spaces).
    assert _login(client, " ANA.SOUZA ", PASSWORD).status_code == 429
    # Other logins are unaffected.
    create_pharmacist(db, full_name="Bia Lima", crf="CRF-SP 2", login="bia.lima", password=PASSWORD)
    assert _login(client, "bia.lima", PASSWORD).status_code == 200


def test_successful_login_resets_failure_count(client, db):
    create_pharmacist(db, full_name="Ana Souza", crf="CRF-SP 1", login="ana.souza", password=PASSWORD)
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login(client, "ana.souza", "wrong-password")
    assert _login(client, "ana.souza", PASSWORD).status_code == 200
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login(client, "ana.souza", "wrong-password")
    assert _login(client, "ana.souza", PASSWORD).status_code == 200


@pytest.fixture
def stored_patient(db):
    create_patient(db, full_name="Paciente Teste", date_of_birth=date(1970, 1, 1),
                   cpf="12345678909")


@pytest.fixture
def swap_key(monkeypatch):
    def swap(name, value):
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        get_crypto.cache_clear()

    yield swap
    monkeypatch.undo()
    get_settings.cache_clear()
    get_crypto.cache_clear()


def test_matching_keys_pass_startup_check(db, stored_patient):
    verify_keys_match_existing_data(db)


def test_empty_database_passes_startup_check(db):
    verify_keys_match_existing_data(db)


def test_changed_encryption_key_is_refused(db, stored_patient, swap_key):
    swap_key("PHARMATECH_DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db.expunge_all()
    with pytest.raises(RuntimeError, match="PHARMATECH_DATA_ENCRYPTION_KEY does not match"):
        verify_keys_match_existing_data(db)


def test_changed_blind_index_key_is_refused(db, stored_patient, swap_key):
    swap_key("PHARMATECH_BLIND_INDEX_KEY", "another-blind-index-key-0123456789-abcdef")
    db.expunge_all()
    with pytest.raises(RuntimeError, match="PHARMATECH_BLIND_INDEX_KEY does not match"):
        verify_keys_match_existing_data(db)
