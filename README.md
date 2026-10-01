# PharmaTech — PAFarC

PharmaTech is the clinical pharmacy application of **PAFarC** (Programa de Aperfeiçoamento em Farmácia Clínica).

Current stage: backend foundation — FastAPI, SQLite, pharmacist accounts and authentication.

## Requirements

- Python 3.10+

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `PHARMATECH_SECRET_KEY` to a random value of at least 32 characters:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The application refuses to start without a valid secret. `.env` is ignored by Git — never commit it.

| Variable | Default | Description |
|---|---|---|
| `PHARMATECH_DATABASE_URL` | `sqlite:///./pharmatech.db` | SQLAlchemy database URL |
| `PHARMATECH_SECRET_KEY` | *(required)* | Token signing secret, ≥ 32 characters |
| `PHARMATECH_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Bearer token lifetime |

## Create a pharmacist

There is no public registration. Accounts are created from the command line:

```bash
python -m scripts.create_pharmacist --full-name "Ana Souza" --crf "CRF-SP 12345" --login ana.souza
```

The password is prompted for interactively and stored only as a bcrypt hash.

## Run the API

```bash
uvicorn backend.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Status check |
| POST | `/auth/login` | Form fields `username`, `password` → bearer token |
| GET | `/auth/me` | Current pharmacist (requires `Authorization: Bearer <token>`) |

## Tests

```bash
pytest
```

Tests use an isolated temporary database and never touch the local database.
