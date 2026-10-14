import re
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ROLE_PHARMACIST, ROLES, UFS, Pharmacist, parse_legacy_crf
from backend.schemas import PharmacistOut, Token
from backend.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

# Simple usernames or e-mail addresses (e.g. "nome.sobrenome" or "nome@exemplo.com").
LOGIN_PATTERN = re.compile(r"^[a-z0-9._+@-]{3,50}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt limit
CRF_NUMBER_PATTERN = re.compile(r"^\d[\d.\-]{0,19}$")

# Brute-force protection: after MAX_FAILED_LOGINS wrong passwords for one login
# within LOCKOUT_SECONDS, further attempts for that login are refused until the
# window passes. In-memory, so it assumes a single API process (see README).
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
_failed_logins: dict[str, list[float]] = {}
_failed_logins_lock = threading.Lock()


def _recent_failures(login: str, now: float) -> list[float]:
    attempts = [t for t in _failed_logins.get(login, []) if now - t < LOCKOUT_SECONDS]
    if attempts:
        _failed_logins[login] = attempts
    else:
        _failed_logins.pop(login, None)
    return attempts


def is_login_locked(login: str) -> bool:
    with _failed_logins_lock:
        return len(_recent_failures(login, time.monotonic())) >= MAX_FAILED_LOGINS


def record_failed_login(login: str) -> None:
    with _failed_logins_lock:
        now = time.monotonic()
        for key in list(_failed_logins):  # drop stale entries so the dict stays small
            _recent_failures(key, now)
        _failed_logins.setdefault(login, []).append(now)


def clear_failed_logins(login: str) -> None:
    with _failed_logins_lock:
        _failed_logins.pop(login, None)

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def normalize_login(login: str) -> str:
    return login.strip().lower()


def validate_login(login: str) -> str:
    login = normalize_login(login)
    if not LOGIN_PATTERN.fullmatch(login):
        raise ValueError("Login must be 3-50 characters: a-z, 0-9, '.', '_', '-', '+' or '@'.")
    return login


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    return password


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
    return role


def validate_crf(crf: str, crf_uf: str | None) -> tuple[str, str | None]:
    """(crf, uf) to store. With a UF, `crf` must be the number only. Without one, a
    legacy combined text (e.g. "CRF/RJ 16906") is accepted as before and its UF extracted."""
    crf = crf.strip()
    if not crf:
        raise ValueError("CRF is required.")
    if crf_uf is not None and crf_uf.strip():
        uf = crf_uf.strip().upper()
        if uf not in UFS:
            raise ValueError("UF must be a valid Brazilian state abbreviation.")
        if not CRF_NUMBER_PATTERN.fullmatch(crf):
            raise ValueError("CRF number must contain only digits (optionally '.' or '-').")
        return crf, uf
    legacy = parse_legacy_crf(crf)
    return crf, (legacy[1] if legacy else None)


def login_in_use(db: Session, login: str, exclude_id: int | None = None) -> bool:
    query = select(Pharmacist.id).where(Pharmacist.login == login)
    if exclude_id is not None:
        query = query.where(Pharmacist.id != exclude_id)
    return db.scalar(query) is not None


def create_pharmacist(
    db: Session, *, full_name: str, crf: str, login: str, password: str,
    crf_uf: str | None = None, role: str = ROLE_PHARMACIST,
) -> Pharmacist:
    full_name = full_name.strip()
    if not full_name:
        raise ValueError("Full name is required.")
    crf, uf = validate_crf(crf, crf_uf)
    login = validate_login(login)
    validate_password(password)
    validate_role(role)
    if login_in_use(db, login):
        raise ValueError(f"Login '{login}' is already in use.")

    pharmacist = Pharmacist(
        full_name=full_name, crf=crf, crf_uf=uf, login=login, role=role,
        password_hash=hash_password(password),
    )
    db.add(pharmacist)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError(f"Login '{login}' is already in use.") from None
    db.refresh(pharmacist)
    return pharmacist


def authenticate_pharmacist(db: Session, login: str, password: str) -> Pharmacist | None:
    pharmacist = db.scalar(select(Pharmacist).where(Pharmacist.login == normalize_login(login)))
    if pharmacist is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, pharmacist.password_hash):
        return None
    return pharmacist


def get_current_pharmacist(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Pharmacist:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise unauthorized
    pharmacist_id = decode_access_token(token)
    if pharmacist_id is None:
        raise unauthorized
    pharmacist = db.get(Pharmacist, pharmacist_id)
    if pharmacist is None or not pharmacist.active:
        raise unauthorized
    return pharmacist


def require_admin(
    pharmacist: Annotated[Pharmacist, Depends(get_current_pharmacist)],
) -> Pharmacist:
    if not pharmacist.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito ao administrador.")
    return pharmacist


def require_clinical(
    pharmacist: Annotated[Pharmacist, Depends(get_current_pharmacist)],
) -> Pharmacist:
    if not pharmacist.clinical_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a farmacêuticos.")
    return pharmacist


def backfill_crf_uf(db: Session) -> int:
    """Fill the separate UF for accounts created before it existed, parsed from the
    original CRF text (which itself is left unchanged). Idempotent."""
    changed = 0
    for pharmacist in db.scalars(select(Pharmacist).where(Pharmacist.crf_uf.is_(None))).all():
        legacy = parse_legacy_crf(pharmacist.crf)
        if legacy:
            pharmacist.crf_uf = legacy[1]
            changed += 1
    db.commit()
    return changed


@router.post("/login", response_model=Token)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> Token:
    login_key = normalize_login(form.username)
    if is_login_locked(login_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )
    pharmacist = authenticate_pharmacist(db, form.username, form.password)
    if pharmacist is None:
        record_failed_login(login_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    clear_failed_logins(login_key)
    if not pharmacist.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")
    return Token(access_token=create_access_token(pharmacist.id))


@router.get("/me", response_model=PharmacistOut)
def read_current_pharmacist(
    pharmacist: Annotated[Pharmacist, Depends(get_current_pharmacist)],
) -> Pharmacist:
    return pharmacist
