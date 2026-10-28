"""חיבור ל-SQLite (D-110). יוצר את הסכמה אם הקובץ חדש."""
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_file="data/or-olam.db"):
    os.makedirs(os.path.dirname(db_file) or ".", exist_ok=True)
    fresh = not os.path.exists(db_file)
    db = sqlite3.connect(db_file)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    if fresh:
        with open(SCHEMA, encoding="utf-8") as f:
            db.executescript(f.read())
    return db
