from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .storage import CSV_FIELDS, _ensure_parent
from .prices import PriceIndicators


class SnapshotDB:
    """Stores daily snapshots of options and underlying indicators."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        _ensure_parent(self.path)
        self.conn = sqlite3.connect(self.path)
        self._ensure_schema()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    def _ensure_schema(self) -> None:
        columns_sql = ",\n                ".join(f'"{col}" TEXT' for col in CSV_FIELDS)
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS option_snapshots (
                snapshot_date TEXT NOT NULL,
                {columns_sql},
                PRIMARY KEY (snapshot_date, ticker)
            )
            """
        )
        self._ensure_columns("option_snapshots", CSV_FIELDS)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS underlying_snapshots (
                snapshot_date TEXT NOT NULL,
                underlying TEXT NOT NULL,
                price REAL,
                price_date TEXT,
                mm200 REAL,
                return_3m REAL,
                trend_flag INTEGER,
                trend_reason TEXT,
                PRIMARY KEY (snapshot_date, underlying)
            )
            """
        )
        self.conn.commit()

    def _ensure_columns(self, table: str, columns: Iterable[str]) -> None:
        existing = {
            row[1]
            for row in self.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if row and len(row) > 1
        }
        for col in columns:
            if col not in existing:
                self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT')
        self.conn.commit()

    def record_underlyings(
        self,
        snapshot_date: str,
        price_map: Mapping[str, PriceIndicators],
        symbols: Sequence[str],
    ) -> None:
        records: List[tuple] = []
        for sym in symbols:
            info = price_map.get(sym)
            if not info:
                continue
            records.append(
                (
                    snapshot_date,
                    sym,
                    info.price,
                    info.price_date,
                    info.mm200,
                    info.return_3m,
                    info.trend_flag,
                    info.trend_reason,
                )
            )
        if not records:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO underlying_snapshots
            (snapshot_date, underlying, price, price_date, mm200, return_3m, trend_flag, trend_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        self.conn.commit()

    def record_options(self, snapshot_date: str, rows: Iterable[Dict[str, str]]) -> None:
        rows = list(rows)
        if not rows:
            return
        columns = ["snapshot_date"] + list(CSV_FIELDS)
        column_clause = ",".join(f'"{col}"' for col in columns)
        placeholders = ",".join(["?"] * len(columns))
        payload: List[List[str]] = []
        for row in rows:
            values: List[Optional[str]] = [snapshot_date]
            for col in CSV_FIELDS:
                values.append(row.get(col, ""))
            payload.append(values)
        self.conn.executemany(
            f"""
            INSERT OR REPLACE INTO option_snapshots ({column_clause})
            VALUES ({placeholders})
            """,
            payload,
        )
        self.conn.commit()


__all__ = ["SnapshotDB"]
