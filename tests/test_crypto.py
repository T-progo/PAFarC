import pytest
from cryptography.fernet import Fernet

from backend.config import get_settings
from backend.crypto import CryptoConfigError, CryptoService, DecryptionError

VALID_INDEX_KEY = "i" * 32


def test_encrypt_decrypt_round_trip():
    crypto = CryptoService(Fernet.generate_key().decode(), VALID_INDEX_KEY)
    ciphertext = crypto.encrypt("Maria José da Silva")
    assert "Maria" not in ciphertext
    assert crypto.decrypt(ciphertext) == "Maria José da Silva"


def test_encryption_is_randomized():
    crypto = CryptoService(Fernet.generate_key().decode(), VALID_INDEX_KEY)
    assert crypto.encrypt("12345678909") != crypto.encrypt("12345678909")


def test_wrong_key_cannot_decrypt():
    ciphertext = CryptoService(Fernet.generate_key().decode(), VALID_INDEX_KEY).encrypt("x")
    other = CryptoService(Fernet.generate_key().decode(), VALID_INDEX_KEY)
    with pytest.raises(DecryptionError):
        other.decrypt(ciphertext)


def test_blind_index_is_keyed_and_purpose_separated():
    key = Fernet.generate_key().decode()
    a = CryptoService(key, "a" * 32)
    b = CryptoService(key, "b" * 32)
    assert a.blind_index("cpf", "12345678909") == a.blind_index("cpf", "12345678909")
    assert a.blind_index("cpf", "12345678909") != b.blind_index("cpf", "12345678909")
    assert a.blind_index("cpf", "x") != a.blind_index("name", "x")
    assert "12345678909" not in a.blind_index("cpf", "12345678909")


@pytest.mark.parametrize("bad_key", ["", "not-a-fernet-key", "a" * 44])
def test_invalid_encryption_key_fails(bad_key):
    with pytest.raises(CryptoConfigError):
        CryptoService(bad_key, VALID_INDEX_KEY)


def test_short_blind_index_key_fails():
    with pytest.raises(CryptoConfigError):
        CryptoService(Fernet.generate_key().decode(), "too-short")


def test_missing_encryption_key_fails_startup_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # make sure no local .env is picked up
    monkeypatch.delenv("PHARMATECH_DATA_ENCRYPTION_KEY")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PHARMATECH_DATA_ENCRYPTION_KEY"):
            get_settings()
    finally:
        get_settings.cache_clear()
