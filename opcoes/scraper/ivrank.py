from __future__ import annotations

import contextlib
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .storage import _ensure_parent


class IVRankStore:
    """Armazena histórico diário de IV por underlying/vencimento e calcula o rank."""

    def __init__(self, path: Path, window_days: int = 180) -> None:
        self.path = Path(path)
        _ensure_parent(self.path)
        self.conn = sqlite3.connect(self.path)
        self.window_days = window_days
        self._ensure_schema()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    def _ensure_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS iv_history (
                underlying TEXT NOT NULL,
                vencimento TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                iv_value REAL NOT NULL,
                PRIMARY KEY (underlying, vencimento, snapshot_date)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_iv_history_lookup ON iv_history (underlying, vencimento, snapshot_date)"
        )
        self.conn.commit()

    def record_many(self, entries: Iterable[Tuple[str, str, str, float]]) -> None:
        payload = list(entries)
        if not payload:
            return
        cur = self.conn.cursor()
        cur.executemany(
            """
            INSERT OR REPLACE INTO iv_history (underlying, vencimento, snapshot_date, iv_value)
            VALUES (?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()

    def rank_for(
        self,
        underlying: str,
        vencimento: str,
        snapshot_date: str,
        current_value: float,
    ) -> Optional[float]:
        if current_value is None:
            return None
        start_date = _subtract_days(snapshot_date, self.window_days).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT iv_value FROM iv_history
            WHERE underlying = ? AND vencimento = ?
              AND snapshot_date BETWEEN ? AND ?
            """,
            (underlying, vencimento, start_date, snapshot_date),
        )
        values = [row[0] for row in cur.fetchall() if row and row[0] is not None]
        if not values:
            return None
        min_val = min(values)
        max_val = max(values)
        if max_val - min_val < 1e-6:
            return 50.0
        rank = ((current_value - min_val) / (max_val - min_val)) * 100.0
        return max(0.0, min(100.0, rank))


def _subtract_days(date_iso: str, days: int) -> dt.date:
    base = dt.date.fromisoformat(date_iso)
    return base - dt.timedelta(days=days)


__all__ = ["IVRankStore"]
