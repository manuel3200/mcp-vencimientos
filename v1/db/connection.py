import os
import sqlite3
from contextlib import contextmanager
from typing import Generator
from core.config import settings

DB_DIR = settings.DATA_DIR
DB_PATH = settings.DB_PATH

def get_connection() -> sqlite3.Connection:
    """Crea y configura una conexión SQLite optimizada con WAL, busy_timeout y claves foráneas."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    return conn

@contextmanager
def get_db_cursor() -> Generator[sqlite3.Cursor, None, None]:
    """Context manager para ejecutar sentencias con commit automático y cierre seguro."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
