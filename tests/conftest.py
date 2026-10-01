import os
import tempfile
from pathlib import Path

# Point the app at an isolated database and test secret *before* anything
# from backend is imported, so the local/production database is never touched.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="pharmatech-tests-"))
os.environ["PHARMATECH_DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}"
os.environ["PHARMATECH_SECRET_KEY"] = "test-secret-key-not-for-production-0123456789"
os.environ["PHARMATECH_ACCESS_TOKEN_EXPIRE_MINUTES"] = "5"
# Fixed, test-only keys (valid Fernet key format; never used outside tests).
os.environ["PHARMATECH_DATA_ENCRYPTION_KEY"] = "dGVzdC1vbmx5LWZlcm5ldC1rZXktMDAwMDAwMDAwMDA="
os.environ["PHARMATECH_BLIND_INDEX_KEY"] = "test-blind-index-key-not-for-production-0123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.database import Base, get_engine, get_sessionmaker, init_db  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    assert Path(get_engine().url.database).parent == _TEST_DIR
    init_db()
    yield
    Base.metadata.drop_all(get_engine())


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
