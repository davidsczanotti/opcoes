from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import get_db_path


@dataclass(frozen=True)
class DarfMonth:
    id: int
    period: str  # YYYY-MM
    due_date: str  # YYYY-MM-DD
    amount: float  # valor a pagar (positivo)
    paid_date: Optional[str] = None  # YYYY-MM-DD
    paid_amount: Optional[float] = None  # positivo
    notes: Optional[str] = None
    is_simulated: bool = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS darf_months (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            due_date TEXT NOT NULL,
            amount REAL NOT NULL,
            paid_date TEXT,
            paid_amount REAL,
            notes TEXT,
            is_simulated INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(period, is_simulated)
        )
        """
    )
    conn.commit()


def _parse_period(period: str) -> str:
    text = (period or "").strip()
    if len(text) != 7 or text[4] != "-":
        raise ValueError("Período inválido (use YYYY-MM).")
    year = int(text[:4])
    month = int(text[5:7])
    if month < 1 or month > 12:
        raise ValueError("Período inválido (mês).")
    return f"{year:04d}-{month:02d}"


def last_business_day_next_month(period: str) -> str:
    """Último dia útil do mês seguinte (considera apenas fim de semana; ignora feriados)."""
    p = _parse_period(period)
    year = int(p[:4])
    month = int(p[5:7])

    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1

    # último dia do mês seguinte
    if next_month == 12:
        first_after = dt.date(next_year + 1, 1, 1)
    else:
        first_after = dt.date(next_year, next_month + 1, 1)
    d = first_after - dt.timedelta(days=1)

    # ajusta se cair em sábado/domingo
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def upsert_month(
    *,
    period: str,
    due_date: str,
    amount: float,
    is_simulated: bool = False,
    notes: Optional[str] = None,
) -> int:
    p = _parse_period(period)
    now = dt.datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM darf_months WHERE period = ? AND COALESCE(is_simulated, 0) = ?",
            (p, 1 if is_simulated else 0),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE darf_months
                SET due_date = ?,
                    amount = ?,
                    notes = COALESCE(?, notes),
                    updated_at = ?
                WHERE id = ?
                """,
                (due_date, float(amount), notes, now, int(existing["id"])),
            )
            conn.commit()
            return int(existing["id"])

        cur = conn.execute(
            """
            INSERT INTO darf_months
            (period, due_date, amount, notes, is_simulated, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (p, due_date, float(amount), notes, 1 if is_simulated else 0, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_month(*, period: str, is_simulated: bool = False) -> Optional[DarfMonth]:
    p = _parse_period(period)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM darf_months WHERE period = ? AND COALESCE(is_simulated, 0) = ?",
            (p, 1 if is_simulated else 0),
        ).fetchone()
        if not row:
            return None
        return DarfMonth(
            id=int(row["id"]),
            period=str(row["period"]),
            due_date=str(row["due_date"]),
            amount=float(row["amount"]),
            paid_date=row["paid_date"],
            paid_amount=float(row["paid_amount"]) if row["paid_amount"] is not None else None,
            notes=row["notes"],
            is_simulated=bool(row["is_simulated"] or 0),
        )
    finally:
        conn.close()


def list_months(*, is_simulated: bool, limit: int = 36) -> List[DarfMonth]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM darf_months
            WHERE COALESCE(is_simulated, 0) = ?
            ORDER BY period DESC
            LIMIT ?
            """,
            (1 if is_simulated else 0, int(limit)),
        ).fetchall()
        out: List[DarfMonth] = []
        for r in rows:
            out.append(
                DarfMonth(
                    id=int(r["id"]),
                    period=str(r["period"]),
                    due_date=str(r["due_date"]),
                    amount=float(r["amount"]),
                    paid_date=r["paid_date"],
                    paid_amount=float(r["paid_amount"]) if r["paid_amount"] is not None else None,
                    notes=r["notes"],
                    is_simulated=bool(r["is_simulated"] or 0),
                )
            )
        return out
    finally:
        conn.close()


def mark_paid(
    *,
    period: str,
    paid_date: str,
    paid_amount: Optional[float] = None,
    is_simulated: bool = False,
) -> None:
    p = _parse_period(period)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, amount FROM darf_months WHERE period = ? AND COALESCE(is_simulated, 0) = ?",
            (p, 1 if is_simulated else 0),
        ).fetchone()
        if not row:
            raise ValueError("DARF do mês não gerado.")
        amount = float(paid_amount) if paid_amount is not None else float(row["amount"])
        now = dt.datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE darf_months
            SET paid_date = ?,
                paid_amount = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (paid_date, float(amount), now, int(row["id"])),
        )
        conn.commit()
    finally:
        conn.close()


def get_monthly_darf_provisions(*, is_simulated: bool, limit: int = 36) -> Dict[str, float]:
    """Soma provisões de DARF (saldo limpo) por competência (YYYY-MM)."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT strftime('%Y-%m', date) AS period, SUM(amount) AS total
            FROM ledger
            WHERE type = 'DARF'
              AND position_id IS NOT NULL
              AND COALESCE(is_simulated, 0) = ?
            GROUP BY period
            ORDER BY period DESC
            LIMIT ?
            """,
            (1 if is_simulated else 0, int(limit)),
        ).fetchall()
        # amount no ledger é negativo; aqui devolvemos positivo
        out: Dict[str, float] = {}
        for r in rows:
            period = r["period"]
            if not period:
                continue
            total = float(r["total"] or 0.0)
            out[str(period)] = max(0.0, -total)
        return out
    finally:
        conn.close()


def list_provision_entries(*, period: str, is_simulated: bool) -> List[dict]:
    """Lista lançamentos de provisão (DARF com position_id) para auditoria."""
    p = _parse_period(period)
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT
              l.id,
              l.date,
              l.amount,
              l.description,
              l.position_id,
              p.ticker AS position_ticker,
              p.underlying AS position_underlying
            FROM ledger l
            LEFT JOIN positions p ON p.id = l.position_id
            WHERE l.type = 'DARF'
              AND l.position_id IS NOT NULL
              AND strftime('%Y-%m', l.date) = ?
              AND COALESCE(l.is_simulated, 0) = ?
            ORDER BY l.date DESC, l.id DESC
            """,
            (p, 1 if is_simulated else 0),
        ).fetchall()
        out: List[dict] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "date": r["date"],
                    "amount": float(r["amount"] or 0.0),
                    "description": r["description"],
                    "position_id": r["position_id"],
                    "position_ticker": r["position_ticker"],
                    "position_underlying": r["position_underlying"],
                }
            )
        return out
    finally:
        conn.close()


__all__ = [
    "DarfMonth",
    "get_month",
    "list_months",
    "get_monthly_darf_provisions",
    "list_provision_entries",
    "last_business_day_next_month",
    "upsert_month",
    "mark_paid",
]

