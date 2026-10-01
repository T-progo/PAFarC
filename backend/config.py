from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="PHARMATECH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./pharmatech.db"
    # No default on purpose: the app must not start with a guessable secret.
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=60, gt=0)
    jwt_algorithm: str = "HS256"
    # Sensitive patient data protection (validated by backend.crypto).
    data_encryption_key: str
    blind_index_key: str
    # Establishment identification printed on documents. Empty values are omitted.
    establishment_name: str = "Programa de Aperfeiçoamento em Farmácia Clínica - PAFarC"
    establishment_address: str = ""
    establishment_cnpj: str = ""
    establishment_phone: str = ""
    establishment_city: str = ""  # e.g. "Cidade - UF"; used in the date/location line


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(
            "Invalid PharmaTech configuration. PHARMATECH_SECRET_KEY, "
            "PHARMATECH_DATA_ENCRYPTION_KEY and PHARMATECH_BLIND_INDEX_KEY must be set "
            "(environment or .env); see .env.example. "
            f"Details: {exc}"
        ) from None
