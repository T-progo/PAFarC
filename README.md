# PharmaTech — PAFarC

PharmaTech is the clinical pharmacy application of **PAFarC** (Programa de Aperfeiçoamento em Farmácia Clínica).

Current stage: pharmacist login → patient registration/search → patient profile → clinical consultations
(SOAP + manually entered exam results) → consultation history.

- **Backend:** FastAPI + SQLAlchemy + SQLite (`backend/`)
- **UI:** Streamlit (`frontend/`), which talks only to the FastAPI backend

## Requirements

- Python 3.10+

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in the required values in `.env` (generation commands are in `.env.example`). The backend refuses to start if any key is missing or invalid. `.env` is ignored by Git — never commit it.

| Variable | Default | Description |
|---|---|---|
| `PHARMATECH_DATABASE_URL` | `sqlite:///./pharmatech.db` | SQLAlchemy database URL |
| `PHARMATECH_SECRET_KEY` | *(required)* | Token signing secret, ≥ 32 characters |
| `PHARMATECH_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Bearer token lifetime |
| `PHARMATECH_DATA_ENCRYPTION_KEY` | *(required)* | Fernet key encrypting patient data |
| `PHARMATECH_BLIND_INDEX_KEY` | *(required)* | HMAC key for CPF/name search indexes, ≥ 32 characters |
| `PHARMATECH_API_URL` | `http://127.0.0.1:8000` | Backend address used by the Streamlit UI |

**Back up `PHARMATECH_DATA_ENCRYPTION_KEY` and `PHARMATECH_BLIND_INDEX_KEY` securely.** Without them, stored patient data cannot be read or searched.

## Patient data protection

- Full name, date of birth and CPF are encrypted at rest with Fernet (authenticated encryption), in `backend/crypto.py`.
- Search uses keyed HMAC-SHA256 digests of the normalized CPF (digits only) and normalized full name (trimmed, single spaces, case- and accent-insensitive). No plaintext is stored.
- Name search is exact on the full name; partial-name search is not supported.
- Duplicate CPFs are blocked by a unique constraint on the CPF digest.
- Search is a `POST` so CPFs and names never appear in URLs or server logs.
- Clinical content is encrypted at rest with the same service: all four SOAP sections and every exam
  field (name, result, unit, reference range, notes). Clinical fields are not searchable, so they have no indexes.

## Consultations

- A consultation always belongs to one patient and to the authenticated pharmacist who created it
  (taken from the login token, never from the request).
- Any pharmacist can read a patient's consultations; only the responsible pharmacist can change
  the SOAP or exam results of a consultation.
- All consultation routes are nested under the patient and return 404 if the consultation
  does not belong to that patient.

## Create a pharmacist

There is no public registration. Accounts are created from the command line:

```bash
python -m scripts.create_pharmacist --full-name "Ana Souza" --crf "CRF-SP 12345" --login ana.souza
```

The password is prompted for interactively and stored only as a bcrypt hash.

## Run

Backend, in one terminal:

```bash
uvicorn backend.main:app
```

UI, in another terminal, from the project root:

```bash
streamlit run frontend/app.py
```

The UI opens at http://localhost:8501. API docs: http://127.0.0.1:8000/docs

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | – | Status check |
| POST | `/auth/login` | – | Form fields `username`, `password` → bearer token |
| GET | `/auth/me` | Bearer | Current pharmacist |
| POST | `/patients` | Bearer | Register a patient (`full_name`, `date_of_birth` YYYY-MM-DD, `cpf`) |
| POST | `/patients/search` | Bearer | `{"query": "<full name or CPF>"}` → matching patients |
| GET | `/patients/{id}` | Bearer | Patient profile |
| POST | `/patients/{id}/consultations` | Bearer | Start a consultation (optional `consultation_date`, default today) |
| GET | `/patients/{id}/consultations` | Bearer | Consultation history, newest first, with pharmacist name and CRF |
| GET | `/patients/{id}/consultations/{cid}` | Bearer | Consultation with SOAP and exam results |
| PUT | `/patients/{id}/consultations/{cid}/soap` | Bearer | Save SOAP (`subjective`, `objective`, `assessment`, `plan`) |
| POST | `/patients/{id}/consultations/{cid}/exams` | Bearer | Add exam result (`exam_name`, `result`, `unit`, `reference_range`, `notes`) |
| PUT | `/patients/{id}/consultations/{cid}/exams/{eid}` | Bearer | Edit exam result |
| DELETE | `/patients/{id}/consultations/{cid}/exams/{eid}` | Bearer | Delete exam result |

## Tests

```bash
pytest
```

Tests use an isolated temporary database and test-only keys; they never touch the local database or `.env`.
