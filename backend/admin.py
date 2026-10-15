"""Administration (administrators only): user management and the establishment record.

Every route requires an active administrator (403 for pharmacists). Passwords are only
ever stored as bcrypt hashes and never returned.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import (
    clear_failed_logins,
    create_pharmacist,
    login_in_use,
    require_admin,
    validate_crf,
    validate_login,
    validate_password,
    validate_role,
)
from backend.database import get_db
from backend.models import ROLE_ADMINISTRATOR, EstablishmentSettings, Pharmacist
from backend.pdf import Establishment, clean_text
from backend.schemas import (
    EstablishmentIn,
    EstablishmentOut,
    PasswordResetIn,
    PharmacistOut,
    UserCreateIn,
    UserUpdateIn,
)
from backend.security import hash_password

ESTABLISHMENT_ID = 1

Admin = Annotated[Pharmacist, Depends(require_admin)]
DB = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# --- services (also used by the command-line script) ------------------------------------

class AdminRuleError(ValueError):
    """A change that would lock administrators out."""


def _other_active_admins(db: Session, user: Pharmacist) -> int:
    return db.scalar(
        select(func.count(Pharmacist.id)).where(
            Pharmacist.role == ROLE_ADMINISTRATOR, Pharmacist.active.is_(True), Pharmacist.id != user.id
        )
    )


def check_admin_rules(db: Session, user: Pharmacist, *, new_role: str, new_active: bool,
                      actor: Pharmacist | None) -> None:
    """Refuse changes that remove the actor's own admin access or the last active administrator."""
    losing_admin = user.is_admin and user.active and (new_role != ROLE_ADMINISTRATOR or not new_active)
    if not losing_admin:
        return
    if actor is not None and actor.id == user.id:
        raise AdminRuleError("Você não pode bloquear nem remover o seu próprio acesso de administrador.")
    if _other_active_admins(db, user) == 0:
        raise AdminRuleError("É necessário manter ao menos um administrador ativo.")


def set_role(db: Session, user: Pharmacist, role: str, actor: Pharmacist | None = None) -> Pharmacist:
    validate_role(role)
    check_admin_rules(db, user, new_role=role, new_active=user.active, actor=actor)
    user.role = role
    db.commit()
    db.refresh(user)
    return user


def effective_establishment(db: Session) -> Establishment:
    """Establishment printed on new documents: the saved record, else the .env values."""
    configured = Establishment.from_settings()
    row = db.get(EstablishmentSettings, ESTABLISHMENT_ID)
    if row is None:
        return configured
    return Establishment(name=row.name, cnpj=row.cnpj, address=row.address, phone=row.phone,
                         city=configured.city)


# --- helpers -------------------------------------------------------------------------------

def _get_user(db: Session, user_id: int) -> Pharmacist:
    user = db.get(Pharmacist, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    return user


def _bad_request(exc: ValueError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if isinstance(exc, AdminRuleError) or "already in use" in str(exc) else 422
    return HTTPException(status_code=code, detail=str(exc))


# --- users -----------------------------------------------------------------------------------

@router.get("/users", response_model=list[PharmacistOut])
def list_users(db: DB) -> list[Pharmacist]:
    return list(db.scalars(select(Pharmacist).order_by(Pharmacist.full_name)).all())


@router.post("/users", response_model=PharmacistOut, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreateIn, db: DB) -> Pharmacist:
    try:
        return create_pharmacist(
            db, full_name=data.full_name, crf=data.crf, crf_uf=data.crf_uf, login=data.login,
            password=data.password, role=data.role,
        )
    except ValueError as exc:
        raise _bad_request(exc) from None


@router.patch("/users/{user_id}", response_model=PharmacistOut)
def update_user(user_id: int, data: UserUpdateIn, db: DB, admin: Admin) -> Pharmacist:
    user = _get_user(db, user_id)
    try:
        if data.full_name is not None:
            if not data.full_name.strip():
                raise ValueError("Full name is required.")
            user.full_name = data.full_name.strip()
        final_role = data.role if data.role is not None else user.role
        if data.crf is not None:
            user.crf, user.crf_uf = validate_crf(data.crf, data.crf_uf, validate_role(final_role))
        elif final_role != user.role and not user.crf.strip():
            validate_crf("", None, final_role)  # e.g. administrator without CRF becoming pharmacist
        if data.login is not None:
            login = validate_login(data.login)
            if login_in_use(db, login, exclude_id=user.id):
                raise ValueError(f"Login '{login}' is already in use.")
            user.login = login
        if data.role is not None:
            validate_role(data.role)
            check_admin_rules(db, user, new_role=data.role, new_active=user.active, actor=admin)
            user.role = data.role
    except ValueError as exc:
        db.rollback()
        raise _bad_request(exc) from None
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/deactivate", response_model=PharmacistOut)
def deactivate_user(user_id: int, db: DB, admin: Admin) -> Pharmacist:
    """Block a user: login and existing sessions are refused immediately."""
    user = _get_user(db, user_id)
    try:
        check_admin_rules(db, user, new_role=user.role, new_active=False, actor=admin)
    except ValueError as exc:
        raise _bad_request(exc) from None
    user.active = False
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/activate", response_model=PharmacistOut)
def activate_user(user_id: int, db: DB) -> Pharmacist:
    """Activate or reactivate a user."""
    user = _get_user(db, user_id)
    user.active = True
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: int, data: PasswordResetIn, db: DB) -> Response:
    """Administrator assigns a new password (stored as a bcrypt hash only)."""
    user = _get_user(db, user_id)
    try:
        validate_password(data.password)
    except ValueError as exc:
        raise _bad_request(exc) from None
    user.password_hash = hash_password(data.password)
    db.commit()
    clear_failed_logins(user.login)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- establishment -------------------------------------------------------------------------------

def _establishment_out(db: Session) -> EstablishmentOut:
    row = db.get(EstablishmentSettings, ESTABLISHMENT_ID)
    current = effective_establishment(db)
    return EstablishmentOut(
        name=current.name, cnpj=current.cnpj, address=current.address, phone=current.phone,
        source="database" if row is not None else "configuration",
        updated_at=row.updated_at if row is not None else None,
    )


@router.get("/establishment", response_model=EstablishmentOut)
def get_establishment(db: DB) -> EstablishmentOut:
    return _establishment_out(db)


@router.put("/establishment", response_model=EstablishmentOut)
def save_establishment(data: EstablishmentIn, db: DB, admin: Admin) -> EstablishmentOut:
    name = clean_text(data.name)
    if not name:
        raise HTTPException(status_code=422, detail="Informe o nome do estabelecimento.")
    values = {"name": name, "cnpj": clean_text(data.cnpj), "address": clean_text(data.address),
              "phone": clean_text(data.phone), "updated_by_id": admin.id}
    row = db.get(EstablishmentSettings, ESTABLISHMENT_ID)
    if row is None:
        db.add(EstablishmentSettings(id=ESTABLISHMENT_ID, **values))
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return _establishment_out(db)
