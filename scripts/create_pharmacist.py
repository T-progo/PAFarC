"""Create a pharmacist account.

Usage (from the project root):
    python -m scripts.create_pharmacist [--full-name NAME] [--crf CRF] [--login LOGIN]

Missing values are prompted for. The password is always read interactively
(never as an argument, so it does not end up in shell history).
"""

import argparse
import getpass
import sys

from backend.auth import create_pharmacist
from backend.database import get_sessionmaker, init_db


def _ask(value: str | None, prompt: str) -> str:
    return value if value else input(prompt)


def _ask_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("Error: passwords do not match.")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a PharmaTech pharmacist account.")
    parser.add_argument("--full-name")
    parser.add_argument("--crf")
    parser.add_argument("--login")
    args = parser.parse_args()

    full_name = _ask(args.full_name, "Full name: ")
    crf = _ask(args.crf, "CRF: ")
    login = _ask(args.login, "Login: ")
    password = _ask_password()

    init_db()
    with get_sessionmaker()() as db:
        try:
            pharmacist = create_pharmacist(
                db, full_name=full_name, crf=crf, login=login, password=password
            )
        except ValueError as exc:
            sys.exit(f"Error: {exc}")

    print(f"Pharmacist created: id={pharmacist.id} login={pharmacist.login}")


if __name__ == "__main__":
    main()
