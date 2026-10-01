"""Create a consistent backup copy of the PharmaTech SQLite database.

Usage (from the project root, with the production .env in place):
    python -m scripts.backup_database /path/to/backup-folder

Uses SQLite's online backup API, so it is safe to run while the application is
running. The copy is encrypted the same way as the live database: it can only be
read with the same PHARMATECH_DATA_ENCRYPTION_KEY and PHARMATECH_BLIND_INDEX_KEY,
which must be backed up separately and securely (never next to the database copy).
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from backend.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up the PharmaTech SQLite database.")
    parser.add_argument("destination", type=Path, help="Folder where the backup file is written")
    args = parser.parse_args()

    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        sys.exit("Error: PHARMATECH_DATABASE_URL is not a SQLite file database.")
    source = Path(url.database)
    if not source.is_file():
        sys.exit(f"Error: database file not found: {source}")

    args.destination.mkdir(parents=True, exist_ok=True)
    target = args.destination / f"pharmatech-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            sys.exit(f"Error: integrity check failed for {target}")
    src.close()
    dst.close()
    print(f"Backup written: {target}")


if __name__ == "__main__":
    main()
