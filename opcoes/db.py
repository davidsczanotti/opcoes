from __future__ import annotations

from contextlib import contextmanager
import sqlite3

from .config import get_db_path


def open_db() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_transaction() -> sqlite3.Connection:
    conn = open_db()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = ["open_db", "db_transaction"]
