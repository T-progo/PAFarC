# PharmaTech — PAFarC

PharmaTech is the clinical pharmacy web application of **PAFarC** (Programa de Aperfeiçoamento em Farmácia Clínica).

Pharmacists log in individually and can register and search patients, record consultations (SOAP and manually
entered laboratory/exam results), review each patient's history and issue PDF documents: Prescrição / Plano de
Cuidado, Solicitação de Exames Laboratoriais and Encaminhamento / Interconsulta.

## Architecture

```
Browser ──HTTPS──> Caddy (reverse proxy) ──> Streamlit UI (frontend/, port 8501)
                                                   │  HTTP, localhost only
                                                   v
                                             FastAPI backend (backend/, port 8000) ──> SQLite file
```

- **Backend** (`backend/`): FastAPI + SQLAlchemy + SQLite. Authentication, validation, encryption, business rules
  and PDF generation. Only the backend touches the database and the keys.
- **UI** (`frontend/`): Streamlit. Presentation only; it talks to the backend over HTTP and keeps the login token
  in server memory (never in the browser).
- **Data protection**: patient name, date of birth and CPF, all SOAP sections and all exam fields are encrypted at
  rest (Fernet). Search uses keyed HMAC indexes of the normalized CPF and full name. Passwords are bcrypt hashes.
- **Documents**: generated in memory (`backend/pdf.py`, `backend/documents.py`), downloaded by the browser, never
  written to disk. Only metadata (type, consultation, pharmacist, time) is stored.

Python **3.10+** (tested with 3.12).

## Installation

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt        # production
pip install -r requirements-dev.txt    # development/tests (includes requirements.txt)
cp .env.example .env
```

Dependencies are pinned to the tested versions.

## Configuration (`.env`)

All settings are environment variables; the backend also reads `.env` from its working directory.
`.env.example` documents every setting and how to generate each secret.

| Variable | Required | Description |
|---|---|---|
| `PHARMATECH_DATABASE_URL` | – | SQLite URL. Production: absolute path, e.g. `sqlite:////var/lib/pharmatech/pharmatech.db` |
| `PHARMATECH_SECRET_KEY` | yes | Token signing secret (≥ 32 chars) |
| `PHARMATECH_DATA_ENCRYPTION_KEY` | yes | Fernet key encrypting patient/clinical data |
| `PHARMATECH_BLIND_INDEX_KEY` | yes | HMAC key for CPF/name search (≥ 32 chars) |
| `PHARMATECH_ACCESS_TOKEN_EXPIRE_MINUTES` | – | Login lifetime (default 60; `.env.example` suggests 480 = one shift) |
| `PHARMATECH_ESTABLISHMENT_NAME` | – | Defaults to "Programa de Aperfeiçoamento em Farmácia Clínica - PAFarC" |
| `PHARMATECH_ESTABLISHMENT_ADDRESS` / `_CNPJ` / `_PHONE` / `_CITY` | – | Printed on documents; omitted when empty |
| `PHARMATECH_API_URL` | – | UI only: backend address (default `http://127.0.0.1:8000`) |

The backend refuses to start if a required key is missing or invalid.

> ### ⚠ Encryption keys must never change for an existing database
> `PHARMATECH_DATA_ENCRYPTION_KEY` and `PHARMATECH_BLIND_INDEX_KEY` belong to the database they were used with.
> If either is lost, the patient and clinical data **cannot be recovered**. If either is changed, the backend
> **refuses to start** (it checks the keys against the stored data at startup) instead of silently mixing keys or
> breaking search and duplicate-CPF detection. Keep a secure copy of both keys (e.g. a password manager or sealed
> offline copy), stored **separately** from database backups. Never generate new keys for a database that has data.

## First pharmacist / account administration

There is no public registration. Create accounts on the server (the password is asked interactively):

```bash
python -m scripts.create_pharmacist --full-name "Nome Completo" --crf "CRF-UF 00000" --login nome.sobrenome
```

Deactivate a pharmacist (they can no longer log in; their records remain):

```bash
python -c "from backend.database import get_sessionmaker; from backend.models import Pharmacist; db=get_sessionmaker()(); p=db.query(Pharmacist).filter_by(login='LOGIN').one(); p.active=False; db.commit()"
```

Reset a password:

```bash
python -c "from getpass import getpass; from backend.database import get_sessionmaker; from backend.models import Pharmacist; from backend.security import hash_password; db=get_sessionmaker()(); p=db.query(Pharmacist).filter_by(login='LOGIN').one(); p.password_hash=hash_password(getpass('Nova senha: ')); db.commit()"
```

## Running locally (development)

```bash
uvicorn backend.main:app                  # terminal 1 → http://127.0.0.1:8000/docs
streamlit run frontend/app.py             # terminal 2 → http://localhost:8501
```

## Production deployment (single Linux server)

Example files are in `deploy/` (systemd units and a Caddyfile). Steps:

1. Create a system user `pharmatech`; install the project in `/opt/pharmatech` with a virtualenv and
   `pip install -r requirements.txt`.
2. Create `/var/lib/pharmatech/` (persistent disk), owned by `pharmatech`, mode `700`.
3. Create `/opt/pharmatech/.env` (mode `600`, owner `pharmatech`) with fresh secrets, the absolute
   `PHARMATECH_DATABASE_URL`, and the establishment fields. **Back up the two encryption keys now.**
4. Create the first pharmacist (as the `pharmatech` user, from `/opt/pharmatech`).
5. Install `deploy/pharmatech-api.service` and `deploy/pharmatech-ui.service` into `/etc/systemd/system/`,
   then `systemctl enable --now pharmatech-api pharmatech-ui`.
6. Install Caddy, put your domain in `deploy/Caddyfile`, copy it to `/etc/caddy/Caddyfile`, reload Caddy.
   Caddy provides HTTPS automatically. Open only ports 80/443 in the firewall.

Equivalent manual commands (from the project root):

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-server-header
PHARMATECH_API_URL=http://127.0.0.1:8000 streamlit run frontend/app.py \
    --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Notes:

- Run the backend with **exactly one worker** (SQLite and the login-attempt limiter assume one process).
- The backend must **not** be reachable from the internet; only the Streamlit UI is exposed through Caddy.
- Give the UI process only `PHARMATECH_API_URL` — it must not receive the encryption keys.
- In containers or platforms with ephemeral disks, the database directory **must** be a persistent volume,
  otherwise all data is lost on restart/redeploy.

## Database, backup and restore

The database is the single SQLite file at `PHARMATECH_DATABASE_URL`. A complete backup needs:

1. a copy of the database file, and
2. the `.env` values — above all `PHARMATECH_DATA_ENCRYPTION_KEY` and `PHARMATECH_BLIND_INDEX_KEY`
   (stored securely and separately from the database copies).

**Backup** (safe while the application is running; writes a timestamped, integrity-checked copy):

```bash
python -m scripts.backup_database /var/backups/pharmatech
```

Schedule it (e.g. daily via cron as the `pharmatech` user) and copy the backup files to another machine or
storage. Database copies are encrypted at the field level but still contain metadata; keep them access-restricted.

**Restore**:

1. `systemctl stop pharmatech-ui pharmatech-api`
2. Copy the chosen backup over the database path (keep the old file aside).
3. Make sure `.env` contains the **same** encryption and blind-index keys used when the backup was made.
4. `systemctl start pharmatech-api pharmatech-ui` — the backend verifies the keys against the data at startup
   and refuses to start if they do not match.

## Installable app (PWA)

The UI publishes a web-app manifest and icons (`frontend/static/`), so Chrome/Edge (desktop and Android) offer
"Install app", and iOS Safari offers "Add to Home Screen". It then opens in its own window like an app.
Installation requires HTTPS (or localhost). There is deliberately **no service worker and no offline mode**: no
patient data, clinical data, PDFs or tokens are cached on the device; the app needs a connection.

## Security notes

- Five wrong passwords for a login block that login for 15 minutes.
- Logging out (or closing the tab) ends the session; tokens expire after `PHARMATECH_ACCESS_TOKEN_EXPIRE_MINUTES`.
- Any pharmacist can read all patients and consultations; only the responsible pharmacist can edit a consultation
  or issue documents for it.
- SOAP text is saved with "Salvar SOAP". The screen shows when there are unsaved changes, asks for confirmation
  before leaving the consultation, and the browser warns before closing or reloading the tab.
- The browser shows only an error type, never stack traces or messages (`.streamlit/config.toml`).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a temporary database and test-only keys; they never touch the real database or `.env`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Backend exits: "Invalid PharmaTech configuration" | A required key is missing in `.env` / environment. |
| Backend exits: "… KEY does not match the existing database" | Wrong key for this database. Restore the original key; never replace it. |
| UI: "Não foi possível conectar ao servidor PharmaTech." | Backend not running or `PHARMATECH_API_URL` wrong. |
| UI: "Muitas tentativas de login…" | Login blocked for 15 minutes after 5 wrong passwords. |
| UI logs out with "Sessão expirada" | Token expired; increase `PHARMATECH_ACCESS_TOKEN_EXPIRE_MINUTES` if needed. |
| Empty database after restart | `PHARMATECH_DATABASE_URL` is relative or on non-persistent storage; use an absolute persistent path. |
| "Install app" not offered | The site must be served over HTTPS. |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Status check |
| POST | `/auth/login` | Form fields `username`, `password` → bearer token |
| GET | `/auth/me` | Current pharmacist |
| POST | `/patients` | Register a patient |
| POST | `/patients/search` | `{"query": "<full name or CPF>"}` (POST keeps CPF/names out of URLs and logs) |
| GET | `/patients/{id}` | Patient profile |
| POST / GET | `/patients/{id}/consultations` | Start a consultation / history (newest first) |
| GET | `/patients/{id}/consultations/{cid}` | Consultation with SOAP, exams and issued documents |
| PUT | `/patients/{id}/consultations/{cid}/soap` | Save SOAP |
| POST / PUT / DELETE | `/patients/{id}/consultations/{cid}/exams[/{eid}]` | Add / edit / delete exam result |
| POST | `/patients/{id}/consultations/{cid}/documents/{prescription,exam-request,referral}` | Generate PDF |

All routes except `/health` and `/auth/login` require `Authorization: Bearer <token>`.

Fonts: DejaVu Sans (`backend/assets/fonts/`, license in `DejaVu-LICENSE.txt`).
