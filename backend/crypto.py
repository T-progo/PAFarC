"""Centralized protection of sensitive data.

- Authenticated encryption (Fernet: AES-128-CBC + HMAC-SHA256) for stored values.
- Keyed blind indexes (HMAC-SHA256) for exact-match lookups without plaintext.

All other code goes through this module; nothing else touches keys or ciphers.
"""

import hashlib
import hmac
from datetime import date
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary, Text
from sqlalchemy.types import TypeDecorator

from backend.config import get_settings

MIN_BLIND_INDEX_KEY_LENGTH = 32


class CryptoConfigError(RuntimeError):
    pass


class DecryptionError(RuntimeError):
    pass


class CryptoService:
    def __init__(self, encryption_key: str, blind_index_key: str) -> None:
        try:
            self._fernet = Fernet(encryption_key)
        except (ValueError, TypeError):
            raise CryptoConfigError(
                "PHARMATECH_DATA_ENCRYPTION_KEY is not a valid Fernet key "
                "(32 url-safe base64-encoded bytes)."
            ) from None
        if len(blind_index_key) < MIN_BLIND_INDEX_KEY_LENGTH:
            raise CryptoConfigError(
                f"PHARMATECH_BLIND_INDEX_KEY must be at least "
                f"{MIN_BLIND_INDEX_KEY_LENGTH} characters."
            )
        self._blind_index_key = blind_index_key.encode("utf-8")

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def encrypt_bytes(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken:
            raise DecryptionError("Stored value could not be decrypted with the configured key.") from None

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise DecryptionError("Stored value could not be decrypted with the configured key.") from None

    def blind_index(self, purpose: str, normalized_value: str) -> str:
        # The purpose separates index domains so equal inputs for different
        # fields (e.g. name vs CPF) never produce the same digest.
        message = f"{purpose}\x00{normalized_value}".encode("utf-8")
        return hmac.new(self._blind_index_key, message, hashlib.sha256).hexdigest()


@lru_cache
def get_crypto() -> CryptoService:
    settings = get_settings()
    return CryptoService(settings.data_encryption_key, settings.blind_index_key)


class EncryptedString(TypeDecorator):
    """String column stored encrypted; Python code sees plaintext."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return None if value is None else get_crypto().encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        return None if value is None else get_crypto().decrypt(value)


class EncryptedBinary(TypeDecorator):
    """Binary column stored encrypted (e.g. issued PDFs); Python code sees plaintext bytes."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: bytes | None, dialect) -> bytes | None:
        return None if value is None else get_crypto().encrypt_bytes(value)

    def process_result_value(self, value: bytes | None, dialect) -> bytes | None:
        return None if value is None else get_crypto().decrypt_bytes(value)


class EncryptedDate(TypeDecorator):
    """Date column stored encrypted (as ISO text); Python code sees a date."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: date | None, dialect) -> str | None:
        return None if value is None else get_crypto().encrypt(value.isoformat())

    def process_result_value(self, value: str | None, dialect) -> date | None:
        return None if value is None else date.fromisoformat(get_crypto().decrypt(value))
