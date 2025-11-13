from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from database.core import DB_PATH

BACKUP_DIR = Path("backups")


def create_database_backup() -> Path:
    """Copy the database file to the backups directory and return the new path."""
    if not DB_PATH.exists():
        raise FileNotFoundError("Bazaga hali ma'lumot yozilmagan.")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"bot_backup_{timestamp}.db"
    shutil.copy2(DB_PATH, target)
    return target
