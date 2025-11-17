from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .storage import _ensure_parent


class FlowStore:
    """Historiza volume financeiro e número de negócios por ticker."""

    def __init__(self, path: Path, window: int = 5) -> None:
        self.path = Path(path)
        _ensure_parent(self.path)
        self.conn = sqlite3.connect(self.path)
        self.window = window
        self._ensure_schema()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    def _ensure_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS flow_history (
                ticker TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                vol_fin REAL,
                num_neg REAL,
                PRIMARY KEY (ticker, snapshot_date)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_flow_history ON flow_history (ticker, snapshot_date)")
        self.conn.commit()

    def averages(self, ticker: str, snapshot_date: str) -> Tuple[Optional[float], Optional[float]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT vol_fin, num_neg FROM flow_history
            WHERE ticker = ? AND snapshot_date < ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (ticker, snapshot_date, self.window),
        )
        rows = cur.fetchall()
        if not rows:
            return None, None
        vols = [r[0] for r in rows if r and r[0] is not None and r[0] > 0]
        nums = [r[1] for r in rows if r and r[1] is not None and r[1] > 0]
        avg_vol = sum(vols) / len(vols) if vols else None
        avg_num = sum(nums) / len(nums) if nums else None
        return avg_vol, avg_num

    def record_many(self, rows: Iterable[Tuple[str, str, Optional[float], Optional[float]]]) -> None:
        payload = [(ticker, date, vol if vol is not None else None, num if num is not None else None) for ticker, date, vol, num in rows]
        if not payload:
            return
        cur = self.conn.cursor()
        cur.executemany(
            """
            INSERT OR REPLACE INTO flow_history (ticker, snapshot_date, vol_fin, num_neg)
            VALUES (?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()


__all__ = ["FlowStore"]
