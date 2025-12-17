from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import get_db_path
from .scraper.storage import _ensure_parent, _parse_ptbr_number
from .utils import infer_option_type


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or get_db_path())
    _ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ranking_entries (
            snapshot_date TEXT NOT NULL,
            category TEXT NOT NULL,
            ticker TEXT NOT NULL,
            underlying TEXT,
            option_type TEXT,
            vencimento TEXT,
            dias_uteis INTEGER,
            score_total REAL,
            best_bid REAL,
            best_ask REAL,
            preco_teorico REAL,
            ultimo REAL,
            vol_impl_perc REAL,
            iv_rank_180d REAL,
            underlying_price REAL,
            extras TEXT,
            PRIMARY KEY (snapshot_date, category, ticker)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ranking_runs (
            snapshot_date TEXT PRIMARY KEY,
            params TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            underlying TEXT,
            option_type TEXT,
            vencimento TEXT,
            dias_uteis INTEGER,
            strike REAL,
            best_bid REAL,
            best_ask REAL,
            ultimo REAL,
            preco_teorico REAL,
            score_total REAL,
            vol_impl_perc REAL,
            iv_rank_180d REAL,
            underlying_price REAL,
            underlying_price_date TEXT,
            raw_row TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _parse_float(value: Any) -> Optional[float]:
    parsed = _parse_ptbr_number(value)
    if parsed is None:
        return None
    try:
        return float(parsed)
    except Exception:  # noqa: BLE001
        return None


def record_ranking_entries(
    snapshot_date: str,
    categories: Mapping[str, Sequence[Mapping[str, Any]]],
    params: Optional[Mapping[str, Any]] = None,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """Grava listas de ranking (top/racional/loteria/teórica) por data."""

    conn = _connect(db_path)
    if params is not None:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ranking_runs (snapshot_date, params) VALUES (?, ?)",
                (snapshot_date, json.dumps(params)),
            )
        except Exception:
            pass

    payload: List[Tuple] = []
    extra_keys = [
        "Status_Moneyness",
        "Status_Liquidez",
        "Status_2x",
        "Status_Remoto",
        "breakeven_price",
        "breakeven_dist_pct",
        "prob_itm_pct",
        "prob_itm_delta_pct",
        "prob_be_pct",
        "custo_pct",
        "extrinsic_value",
        "extrinsic_pct_spot",
        "spread_pct",
        "iv_hv_spread",
    ]

    for category, rows in categories.items():
        for r in rows:
            ticker = str(r.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            underlying = (r.get("underlying") or "").strip().upper() or None
            venc = (r.get("vencimento") or "").strip() or None
            dias_uteis = None
            try:
                dias_uteis = int(r.get("dias_uteis")) if r.get("dias_uteis") is not None else None
            except Exception:
                dias_uteis = None
            opt_type = (r.get("option_type") or infer_option_type(ticker) or "").upper() or None

            extras: Dict[str, Any] = {}
            for key in extra_keys:
                val = r.get(key)
                parsed = _parse_float(val)
                extras[key] = parsed if parsed is not None else (val if val not in ("", None) else None)
            extras_clean = {k: v for k, v in extras.items() if v not in (None, "")}
            extras_json = json.dumps(extras_clean, ensure_ascii=False) if extras_clean else None

            payload.append(
                (
                    snapshot_date,
                    category,
                    ticker,
                    underlying,
                    opt_type,
                    venc,
                    dias_uteis,
                    _parse_float(r.get("score_total")),
                    _parse_float(r.get("best_bid")),
                    _parse_float(r.get("best_ask")),
                    _parse_float(r.get("preco_teorico")),
                    _parse_float(r.get("ultimo")),
                    _parse_float(r.get("vol_impl_perc")),
                    _parse_float(r.get("iv_rank_180d")),
                    _parse_float(r.get("underlying_price")),
                    extras_json,
                )
            )

    if payload:
        conn.executemany(
            """
            INSERT OR REPLACE INTO ranking_entries (
                snapshot_date, category, ticker, underlying, option_type,
                vencimento, dias_uteis, score_total, best_bid, best_ask,
                preco_teorico, ultimo, vol_impl_perc, iv_rank_180d,
                underlying_price, extras
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
    conn.close()


def record_decision(
    ticker: str,
    *,
    snapshot_date: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Grava a linha completa de um ticker do snapshot mais recente (ou de uma data)."""

    conn = _connect(db_path)
    try:
        if snapshot_date:
            row = conn.execute(
                "SELECT * FROM option_snapshots WHERE ticker = ? AND snapshot_date = ? LIMIT 1",
                (ticker.strip().upper(), snapshot_date),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM option_snapshots
                WHERE ticker = ?
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (ticker.strip().upper(),),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        snap_date = data.get("snapshot_date")
        venc = data.get("vencimento")
        dias_uteis = None
        try:
            dias_uteis = int(data.get("dias_uteis")) if data.get("dias_uteis") is not None else None
        except Exception:
            dias_uteis = None

        payload = (
            snap_date,
            ticker.strip().upper(),
            (data.get("underlying") or "").strip().upper() or None,
            (data.get("option_type") or infer_option_type(ticker) or "").upper() or None,
            venc,
            dias_uteis,
            _parse_float(data.get("strike")),
            _parse_float(data.get("best_bid")),
            _parse_float(data.get("best_ask")),
            _parse_float(data.get("ultimo")),
            _parse_float(data.get("preco_teorico")),
            _parse_float(data.get("score_total")),
            _parse_float(data.get("vol_impl_perc")),
            _parse_float(data.get("iv_rank_180d")),
            _parse_float(data.get("underlying_price")),
            data.get("underlying_price_date"),
            json.dumps(data, ensure_ascii=False),
            notes,
        )
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO decisions (
                snapshot_date, ticker, underlying, option_type, vencimento,
                dias_uteis, strike, best_bid, best_ask, ultimo, preco_teorico,
                score_total, vol_impl_perc, iv_rank_180d, underlying_price,
                underlying_price_date, raw_row, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_decisions(*, limit: Optional[int] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    query = "SELECT * FROM decisions ORDER BY created_at DESC, id DESC"
    if limit is not None and limit > 0:
        query += " LIMIT ?"
        params: Tuple[Any, ...] = (limit,)
    else:
        params = ()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_history(
    *,
    retention_days: int = 180,
    purge_snapshots: bool = False,
    today: Optional[dt.date] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Remove rankings (e opcionalmente snapshots) antigos ou vencidos."""

    today = today or dt.date.today()
    cutoff = today - dt.timedelta(days=max(retention_days, 0))
    cutoff_iso = cutoff.isoformat()
    removed: Dict[str, int] = {"ranking_entries": 0, "ranking_runs": 0, "option_snapshots": 0, "underlying_snapshots": 0}

    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute("DELETE FROM ranking_runs WHERE snapshot_date < ?", (cutoff_iso,))
    removed["ranking_runs"] = cur.rowcount if cur.rowcount is not None else 0

    cur.execute("DELETE FROM ranking_entries WHERE snapshot_date < ?", (cutoff_iso,))
    removed["ranking_entries"] = cur.rowcount if cur.rowcount is not None else 0

    # Remove ranking_entries vencidos (vencimento < hoje)
    cur.execute("SELECT rowid, vencimento FROM ranking_entries WHERE vencimento IS NOT NULL")
    rows = cur.fetchall()
    to_delete = []
    for r in rows:
        venc = (r[1] or "").strip()
        try:
            venc_date = dt.datetime.strptime(venc, "%d/%m/%Y").date()
        except Exception:
            continue
        if venc_date < today:
            to_delete.append(r[0])
    if to_delete:
        cur.executemany("DELETE FROM ranking_entries WHERE rowid = ?", [(rid,) for rid in to_delete])
        removed["ranking_entries"] += len(to_delete)

    if purge_snapshots:
        cur.execute("DELETE FROM option_snapshots WHERE snapshot_date < ?", (cutoff_iso,))
        removed["option_snapshots"] = cur.rowcount if cur.rowcount is not None else 0
        cur.execute("DELETE FROM underlying_snapshots WHERE snapshot_date < ?", (cutoff_iso,))
        removed["underlying_snapshots"] = cur.rowcount if cur.rowcount is not None else 0

        # Remove opções vencidas
        cur.execute("SELECT rowid, vencimento FROM option_snapshots WHERE vencimento IS NOT NULL")
        rows = cur.fetchall()
        to_delete = []
        for r in rows:
            venc = (r[1] or "").strip()
            try:
                venc_date = dt.datetime.strptime(venc, "%d/%m/%Y").date()
            except Exception:
                continue
            if venc_date < today:
                to_delete.append(r[0])
        if to_delete:
            cur.executemany("DELETE FROM option_snapshots WHERE rowid = ?", [(rid,) for rid in to_delete])
            removed["option_snapshots"] += len(to_delete)

    conn.commit()
    conn.close()
    return removed


__all__ = [
    "record_ranking_entries",
    "record_decision",
    "list_decisions",
    "cleanup_history",
]
