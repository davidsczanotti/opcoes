from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .config import get_db_path
from .scraper.storage import _ensure_parent


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    _ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def latest_snapshot_date(db_path: Optional[Path] = None) -> Optional[str]:
    """Recupera a data mais recente disponível em option_snapshots."""

    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(snapshot_date) AS d FROM option_snapshots").fetchone()
        return row["d"] if row else None
    finally:
        conn.close()


def fetch_latest_underlying_options(
    underlying: str,
    *,
    db_path: Optional[Path] = None,
) -> List[Dict[str, object]]:
    """Busca linhas de option_snapshots do último snapshot para um underlying."""

    underlying = (underlying or "").strip().upper()
    if not underlying:
        return []

    conn = _connect(db_path)
    try:
        snapshot_date = latest_snapshot_date(db_path=db_path)
        if not snapshot_date:
            return []
        rows = conn.execute(
            """
            SELECT
                ticker,
                underlying,
                vencimento,
                dias_uteis,
                strike,
                dist_perc_strike,
                underlying_price,
                extrinsic_pct_spot,
                "%_Alta_p_2x" AS pct_2x,
                score_total
            FROM option_snapshots
            WHERE snapshot_date = ?
              AND UPPER(underlying) = ?
              AND dias_uteis IS NOT NULL
            """,
            (snapshot_date, underlying),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_latest_underlying_quote(
    underlying: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, object]]:
    """Busca a cotação mais recente do underlying em underlying_snapshots."""

    underlying = (underlying or "").strip().upper()
    if not underlying:
        return None

    conn = _connect(db_path)
    try:
        snapshot_date = latest_snapshot_date(db_path=db_path)
        if not snapshot_date:
            return None
        row = conn.execute(
            """
            SELECT snapshot_date, underlying, price, price_date, mm200, return_3m, trend_flag, trend_reason
            FROM underlying_snapshots
            WHERE snapshot_date = ?
              AND UPPER(underlying) = ?
            LIMIT 1
            """,
            (snapshot_date, underlying),
        ).fetchone()
        if not row:
            return None
        return {
            "snapshot_date": row["snapshot_date"],
            "underlying": row["underlying"],
            "price": row["price"],
            "price_date": row["price_date"],
            "mm200": row["mm200"],
            "return_3m": row["return_3m"],
            "trend_flag": row["trend_flag"],
            "trend_reason": row["trend_reason"],
        }
    finally:
        conn.close()


__all__ = ["latest_snapshot_date", "fetch_latest_underlying_options", "fetch_latest_underlying_quote"]
