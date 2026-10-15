"""Administration (administrators only): user management and establishments.

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
from backend.establishments import (
    DuplicateCNPJError,
    create_establishment,
    update_establishment,
)
from backend.models import ROLE_ADMINISTRATOR, ROLE_PHARMACIST, Establishment, Pharmacist
from backend.schemas import (
    EstablishmentIn,
    EstablishmentOut,
    EstablishmentUpdateIn,
    PasswordResetIn,
    PharmacistOut,
    UserCreateIn,
    UserUpdateIn,
)
from backend.security import hash_password

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


# --- helpers -------------------------------------------------------------------------------

def _get_user(db: Session, user_id: int) -> Pharmacist:
    user = db.get(Pharmacist, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    return user


def _bad_request(exc: ValueError) -> HTTPException:
    conflict = isinstance(exc, (AdminRuleError, DuplicateCNPJError)) or "already in use" in str(exc)
    return HTTPException(status_code=status.HTTP_409_CONFLICT if conflict else 422, detail=str(exc))


# --- users -----------------------------------------------------------------------------------

@router.get("/users", response_model=list[PharmacistOut])
def list_users(db: DB) -> list[Pharmacist]:
    return list(db.scalars(select(Pharmacist).order_by(Pharmacist.full_name)).all())


@router.post("/users", response_model=PharmacistOut, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreateIn, db: DB) -> Pharmacist:
    if data.role == ROLE_PHARMACIST and data.establishment_id is None:
        raise HTTPException(status_code=422, detail="Selecione o estabelecimento do farmacêutico.")
    try:
        return create_pharmacist(
            db, full_name=data.full_name, crf=data.crf, crf_uf=data.crf_uf, login=data.login,
            password=data.password, role=data.role, establishment_id=data.establishment_id,
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
        _apply_establishment(db, user, data)
    except ValueError as exc:
        db.rollback()
        raise _bad_request(exc) from None
    db.commit()
    db.refresh(user)
    return user


def _apply_establishment(db: Session, user: Pharmacist, data: UserUpdateIn) -> None:
    """Pharmacists must stay linked to an existing establishment; administrators are unlinked."""
    given = "establishment_id" in data.model_fields_set
    if user.role == ROLE_ADMINISTRATOR:
        if given and data.establishment_id is not None:
            raise ValueError("Administradores não são vinculados a estabelecimentos.")
        user.establishment_id = None
        return
    if given:
        if data.establishment_id is None or db.get(Establishment, data.establishment_id) is None:
            raise ValueError("Selecione um estabelecimento válido para o farmacêutico.")
        user.establishment_id = data.establishment_id
    elif user.establishment_id is None:
        raise ValueError("Selecione o estabelecimento do farmacêutico.")


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


# --- establishments (no deletion in this milestone) ---------------------------------------------

@router.get("/establishments", response_model=list[EstablishmentOut])
def list_establishments(db: DB) -> list[Establishment]:
    return list(db.scalars(select(Establishment).order_by(Establishment.name, Establishment.id)).all())


@router.post("/establishments", response_model=EstablishmentOut, status_code=status.HTTP_201_CREATED)
def create_establishment_route(data: EstablishmentIn, db: DB) -> Establishment:
    try:
        return create_establishment(db, name=data.name, cnpj=data.cnpj, address=data.address, phone=data.phone)
    except ValueError as exc:
        raise _bad_request(exc) from None


@router.patch("/establishments/{establishment_id}", response_model=EstablishmentOut)
def update_establishment_route(establishment_id: int, data: EstablishmentUpdateIn, db: DB) -> Establishment:
    establishment = db.get(Establishment, establishment_id)
    if establishment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estabelecimento não encontrado.")
    try:
        return update_establishment(db, establishment, data.model_dump(exclude_unset=True))
    except ValueError as exc:
        db.rollback()
        raise _bad_request(exc) from None
