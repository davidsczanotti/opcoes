from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .scraper.storage import _ensure_parent

DB_PATH = Path("data/opcoes_snapshots.db")


def _connect() -> sqlite3.Connection:
    _ensure_parent(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            underlying TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            fees REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            exit_date TEXT,
            exit_price REAL,
            notes TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions (ticker)")
    conn.commit()


def _normalize_ticker(value: str) -> str:
    return (value or "").strip().upper()


def add_position(
    *,
    ticker: str,
    underlying: str,
    trade_date: str,
    qty: int,
    entry_price: float,
    fees: float = 0.0,
    notes: Optional[str] = None,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO positions (ticker, underlying, trade_date, qty, entry_price, fees, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _normalize_ticker(ticker),
            _normalize_ticker(underlying),
            trade_date,
            int(qty),
            float(entry_price),
            float(fees or 0.0),
            notes,
        ),
    )
    conn.commit()
    pos_id = cursor.lastrowid
    conn.close()
    return int(pos_id)


def close_position(*, position_id: int, exit_date: str, exit_price: float) -> None:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE positions
        SET status = 'closed',
            exit_date = ?,
            exit_price = ?
        WHERE id = ? AND status = 'open'
        """,
        (exit_date, float(exit_price), int(position_id)),
    )
    if cur.rowcount == 0:
        conn.close()
        raise ValueError(f"Posição {position_id} não encontrada ou já fechada.")
    conn.commit()
    conn.close()


def list_positions(
    *,
    include_closed: bool = False,
    ticker: Optional[str] = None,
    only_closed: bool = False,
) -> List[dict]:
    conn = _connect()
    where: List[str] = []
    params: List[object] = []
    if only_closed:
        where.append("p.status = 'closed'")
    elif not include_closed:
        where.append("p.status = 'open'")
    if ticker:
        where.append("p.ticker = ?")
        params.append(_normalize_ticker(ticker))
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    query = f"""
        SELECT
            p.*,
            snap.snapshot_date AS last_snapshot_date,
            snap."ultimo" AS last_price_raw,
            snap."score_total" AS last_score_total,
            snap."trend_flag" AS last_trend_flag
        FROM positions p
        LEFT JOIN (
            SELECT os1.*
            FROM option_snapshots os1
            INNER JOIN (
                SELECT ticker, MAX(snapshot_date) AS snapshot_date
                FROM option_snapshots
                GROUP BY ticker
            ) latest
            ON os1.ticker = latest.ticker AND os1.snapshot_date = latest.snapshot_date
        ) AS snap ON snap.ticker = p.ticker
        {where_clause}
        ORDER BY p.trade_date DESC, p.id DESC
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    def parse_decimal(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        value = value.replace("%", "").replace("+", "").replace("\u2212", "-").replace("−", "-")
        value = value.replace(".", "").replace(",", ".")
        try:
            return float(value)
        except ValueError:
            return None

    entry_price = float(row["entry_price"])
    qty = int(row["qty"])
    fees = float(row["fees"] or 0.0)
    last_price = parse_decimal(row["last_price_raw"])
    pl = None
    pl_pct = None
    if last_price is not None and entry_price:
        pl = (last_price - entry_price) * qty - fees
        pl_pct = ((last_price / entry_price) - 1.0) * 100.0
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "underlying": row["underlying"],
        "trade_date": row["trade_date"],
        "qty": qty,
        "entry_price": entry_price,
        "fees": fees,
        "status": row["status"],
        "notes": row["notes"] or "",
        "last_snapshot_date": row["last_snapshot_date"],
        "last_price": last_price,
        "pl": pl,
        "pl_pct": pl_pct,
        "score_total": row["last_score_total"],
        "trend_flag": row["last_trend_flag"],
    }


__all__ = ["add_position", "list_positions"]
