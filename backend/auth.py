import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Pharmacist
from backend.schemas import PharmacistOut, Token
from backend.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

LOGIN_PATTERN = re.compile(r"^[a-z0-9._-]{3,50}$")
MIN_PASSWORD_LENGTH = 8

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def normalize_login(login: str) -> str:
    return login.strip().lower()


def create_pharmacist(
    db: Session, *, full_name: str, crf: str, login: str, password: str
) -> Pharmacist:
    full_name = full_name.strip()
    crf = crf.strip()
    login = normalize_login(login)
    if not full_name:
        raise ValueError("Full name is required.")
    if not crf:
        raise ValueError("CRF is required.")
    if not LOGIN_PATTERN.fullmatch(login):
        raise ValueError("Login must be 3-50 characters: a-z, 0-9, '.', '_' or '-'.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if db.scalar(select(Pharmacist.id).where(Pharmacist.login == login)) is not None:
        raise ValueError(f"Login '{login}' is already in use.")

    pharmacist = Pharmacist(
        full_name=full_name, crf=crf, login=login, password_hash=hash_password(password)
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


@router.post("/login", response_model=Token)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> Token:
    pharmacist = authenticate_pharmacist(db, form.username, form.password)
    if pharmacist is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not pharmacist.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")
    return Token(access_token=create_access_token(pharmacist.id))


@router.get("/me", response_model=PharmacistOut)
def read_current_pharmacist(
    pharmacist: Annotated[Pharmacist, Depends(get_current_pharmacist)],
) -> Pharmacist:
    return pharmacist
