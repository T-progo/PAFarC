"""Create a user account, or change the role of an existing one.

Usage (from the project root):
    python -m scripts.create_pharmacist [--full-name NAME] [--crf CRF] [--uf UF] [--login LOGIN]
                                        [--role pharmacist|administrator]
    python -m scripts.create_pharmacist --set-role LOGIN --role administrator|pharmacist

Missing values are prompted for. The password is always read interactively
(never as an argument, so it does not end up in shell history). The default
role is pharmacist; use --role administrator for the first administrator.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from backend.admin import set_role
from backend.auth import create_pharmacist, normalize_login
from backend.database import get_sessionmaker, init_db
from backend.models import ROLE_PHARMACIST, ROLES, Pharmacist


def _ask(value: str | None, prompt: str) -> str:
    return value if value else input(prompt)


def _ask_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("Error: passwords do not match.")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a PharmaTech user account or change its role.")
    parser.add_argument("--full-name")
    parser.add_argument("--crf", help="CRF number (or a legacy combined value such as 'CRF/RJ 16906')")
    parser.add_argument("--uf", help="UF of the CRF, e.g. RJ")
    parser.add_argument("--login")
    parser.add_argument("--role", choices=ROLES, default=None)
    parser.add_argument("--set-role", metavar="LOGIN", help="change the role of an existing account")
    args = parser.parse_args()

    init_db()
    if args.set_role:
        if not args.role:
            sys.exit("Error: --set-role requires --role.")
        with get_sessionmaker()() as db:
            user = db.scalar(select(Pharmacist).where(Pharmacist.login == normalize_login(args.set_role)))
            if user is None:
                sys.exit(f"Error: login '{args.set_role}' not found.")
            try:
                set_role(db, user, args.role)
            except ValueError as exc:
                sys.exit(f"Error: {exc}")
            print(f"Role updated: id={user.id} login={user.login} role={user.role}")
        return

    full_name = _ask(args.full_name, "Full name: ")
    crf = _ask(args.crf, "CRF: ")
    login = _ask(args.login, "Login: ")
    password = _ask_password()

    with get_sessionmaker()() as db:
        try:
            pharmacist = create_pharmacist(
                db, full_name=full_name, crf=crf, crf_uf=args.uf, login=login, password=password,
                role=args.role or ROLE_PHARMACIST,
            )
        except ValueError as exc:
            sys.exit(f"Error: {exc}")

    print(f"Pharmacist created: id={pharmacist.id} login={pharmacist.login} role={pharmacist.role}")


if __name__ == "__main__":
    main()
