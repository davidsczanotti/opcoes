from __future__ import annotations

import sqlite3
from typing import Iterable, List, Optional

from .config import get_db_path
from .scraper.storage import _ensure_parent


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
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
            trade_type TEXT DEFAULT 'swing',
            irrf REAL,
            status TEXT NOT NULL DEFAULT 'open',
            exit_date TEXT,
            exit_price REAL,
            notes TEXT,
            partial_date TEXT,
            partial_price REAL,
            partial_qty INTEGER,
            exit_reason TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions (ticker)")
    _ensure_position_columns(conn)
    conn.commit()


def _ensure_position_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute('PRAGMA table_info("positions")').fetchall()
        if row and len(row) > 1
    }
    columns = {
        "partial_date": "TEXT",
        "partial_price": "REAL",
        "partial_qty": "INTEGER",
        "exit_reason": "TEXT",
        "trade_type": "TEXT",
        "irrf": "REAL",
        "is_simulated": "INTEGER DEFAULT 0",
        "parent_position_id": "INTEGER",
    }
    for col, col_type in columns.items():
        if col not in existing:
            conn.execute(f'ALTER TABLE positions ADD COLUMN "{col}" {col_type}')
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
    trade_type: str = "swing",
    irrf: Optional[float] = None,
    notes: Optional[str] = None,
    partial_date: Optional[str] = None,
    partial_price: Optional[float] = None,
    partial_qty: Optional[int] = None,
    exit_reason: Optional[str] = None,
    is_simulated: bool = False,
    parent_position_id: Optional[int] = None,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO positions (ticker, underlying, trade_date, qty, entry_price, fees, trade_type, irrf, notes, partial_date, partial_price, partial_qty, exit_reason, is_simulated, parent_position_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _normalize_ticker(ticker),
            _normalize_ticker(underlying),
            trade_date,
            int(qty),
            float(entry_price),
            float(fees or 0.0),
            trade_type,
            float(irrf) if irrf is not None else None,
            notes,
            partial_date,
            float(partial_price) if partial_price is not None else None,
            int(partial_qty) if partial_qty is not None else None,
            exit_reason,
            1 if is_simulated else 0,
            int(parent_position_id) if parent_position_id is not None else None,
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


def update_position(
    *,
    position_id: int,
    trade_date: Optional[str] = None,
    qty: Optional[int] = None,
    entry_price: Optional[float] = None,
    fees: Optional[float] = None,
    status: Optional[str] = None,
    exit_date: Optional[str] = None,
    exit_price: Optional[float] = None,
    notes: Optional[str] = None,
    partial_date: Optional[str] = None,
    partial_price: Optional[float] = None,
    partial_qty: Optional[int] = None,
    exit_reason: Optional[str] = None,
    trade_type: Optional[str] = None,
    irrf: Optional[float] = None,
    is_simulated: Optional[bool] = None,
    parent_position_id: Optional[int] = None,
) -> None:
    fields = []
    params = []
    if trade_date is not None:
        fields.append("trade_date = ?")
        params.append(trade_date)
    if qty is not None:
        fields.append("qty = ?")
        params.append(int(qty))
    if entry_price is not None:
        fields.append("entry_price = ?")
        params.append(float(entry_price))
    if fees is not None:
        fields.append("fees = ?")
        params.append(float(fees))
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if exit_date is not None:
        fields.append("exit_date = ?")
        params.append(exit_date)
    if exit_price is not None:
        fields.append("exit_price = ?")
        params.append(float(exit_price))
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if partial_date is not None:
        fields.append("partial_date = ?")
        params.append(partial_date)
    if partial_price is not None:
        fields.append("partial_price = ?")
        params.append(float(partial_price))
    if partial_qty is not None:
        fields.append("partial_qty = ?")
        params.append(int(partial_qty))
    if exit_reason is not None:
        fields.append("exit_reason = ?")
        params.append(exit_reason)
    if trade_type is not None:
        fields.append("trade_type = ?")
        params.append(trade_type)
    if irrf is not None:
        fields.append("irrf = ?")
        params.append(float(irrf))
    if is_simulated is not None:
        fields.append("is_simulated = ?")
        params.append(1 if is_simulated else 0)
    if parent_position_id is not None:
        fields.append("parent_position_id = ?")
        params.append(int(parent_position_id))
    if not fields:
        return

    params.append(int(position_id))
    conn = _connect()
    cur = conn.cursor()
    cur.execute(f"UPDATE positions SET {', '.join(fields)} WHERE id = ?", params)
    if cur.rowcount == 0:
        conn.close()
        raise ValueError(f"Posição {position_id} não encontrada.")
    conn.commit()
    conn.close()


def delete_position(*, position_id: int) -> None:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM positions WHERE id = ?", (int(position_id),))
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
            snap."trend_flag" AS last_trend_flag,
            snap."vencimento" AS last_vencimento,
            snap."dias_uteis" AS last_dias_uteis,
            snap."underlying_price" AS last_underlying_price,
            snap."extrinsic_pct_spot" AS last_extrinsic_pct_spot,
            snap."%_Alta_p_2x" AS last_pct_2x,
            snap."strike" AS last_strike
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

    def parse_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except ValueError:
            return None

    entry_price = float(row["entry_price"])
    qty = int(row["qty"])
    fees = float(row["fees"] or 0.0)
    partial_qty = int(row["partial_qty"] or 0) if "partial_qty" in row.keys() else 0
    partial_price = parse_decimal(row["partial_price"]) if "partial_price" in row.keys() else None
    partial_date = row["partial_date"] if "partial_date" in row.keys() else None
    exit_reason = row["exit_reason"] if "exit_reason" in row.keys() else None
    last_price = parse_decimal(row["last_price_raw"])
    open_qty = max(qty - partial_qty, 0)

    is_sim_raw = 0
    if "is_simulated" in row.keys():
        try:
            is_sim_raw = int(row["is_simulated"] or 0)
        except (TypeError, ValueError):
            is_sim_raw = 0

    realized_pl = None
    if partial_qty and partial_price is not None:
        realized_pl = (partial_price - entry_price) * partial_qty

    pl_open = None
    if last_price is not None and entry_price and open_qty > 0:
        pl_open = (last_price - entry_price) * open_qty

    pl = None
    if realized_pl is not None or pl_open is not None:
        pl = (realized_pl or 0.0) + (pl_open or 0.0) - fees

    pl_pct = None
    if pl is not None and entry_price and qty > 0:
        invested = entry_price * qty
        pl_pct = (pl / invested) * 100.0

    breakeven = None
    if open_qty > 0:
        # preço tal que PL total (realizado + aberto - fees) = 0
        be_price = entry_price
        numerator = (realized_pl or 0.0) - fees
        be_price = entry_price - (numerator / open_qty)
        breakeven = be_price

    underlying_price = None
    extrinsic_pct_spot = None
    pct_2x = None
    last_strike = None
    if "last_underlying_price" in row.keys():
        underlying_price = parse_decimal(row["last_underlying_price"])
    if "last_extrinsic_pct_spot" in row.keys():
        extrinsic_pct_spot = parse_decimal(row["last_extrinsic_pct_spot"])
    if "last_pct_2x" in row.keys():
        pct_2x = parse_decimal(row["last_pct_2x"])
    if "last_strike" in row.keys():
        last_strike = parse_decimal(row["last_strike"])

    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "underlying": row["underlying"],
        "trade_date": row["trade_date"],
        "qty": qty,
        "open_qty": open_qty,
        "entry_price": entry_price,
        "fees": fees,
        "status": row["status"],
        "notes": row["notes"] or "",
        "last_snapshot_date": row["last_snapshot_date"],
        "last_price": last_price,
        "pl": pl,
        "pl_pct": pl_pct,
        "score_total": parse_decimal(row["last_score_total"]),
        "trend_flag": row["last_trend_flag"],
        "vencimento": row["last_vencimento"],
        "dias_uteis": parse_int(row["last_dias_uteis"]),
        "partial_qty": partial_qty,
        "partial_price": partial_price,
        "partial_date": partial_date,
        "exit_reason": exit_reason,
        "realized_pl": realized_pl,
        "breakeven_price": breakeven,
        "trade_type": row["trade_type"],
        "irrf": row["irrf"],
        "is_simulated": bool(is_sim_raw),
        "parent_position_id": row["parent_position_id"] if "parent_position_id" in row.keys() else None,
        "underlying_price": underlying_price,
        "extrinsic_pct_spot": extrinsic_pct_spot,
        "pct_2x": pct_2x,
        "strike": last_strike,
    }


__all__ = ["add_position", "list_positions"]
