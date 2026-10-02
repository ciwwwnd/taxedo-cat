from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from taxedo.paths import DEFAULT_DATA_DIR


def check(data_dir: Path, max_age: int = 120) -> bool:
    heartbeat = data_dir / "heartbeat"
    if not heartbeat.is_file() or time.time() - heartbeat.stat().st_mtime > max_age:
        return False
    try:
        with sqlite3.connect(
            f"file:{data_dir / 'receipts.db'}?mode=ro", uri=True
        ) as db:
            db.execute("SELECT COUNT(*) FROM receipts").fetchone()
        return True
    except sqlite3.Error:
        return False


if __name__ == "__main__":
    data_dir = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
    sys.exit(0 if check(data_dir) else 1)
