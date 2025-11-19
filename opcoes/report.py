from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .scraper.storage import _ensure_parent
from .portfolio import list_positions

DB_PATH = Path("data/opcoes_snapshots.db")


@dataclass
class ReportData:
    snapshot_date: str
    opportunities: List[Dict[str, object]]
    positions: List[Dict[str, object]]
    alerts: List[Dict[str, object]]


def generate_report(*, min_score: int = 8, limit: int = 20) -> ReportData:
    conn = _connect()
    snapshot_date = _latest_snapshot_date(conn)
    if not snapshot_date:
        conn.close()
        raise RuntimeError("Nenhum snapshot encontrado. Rode o scraper primeiro.")
    opportunities = _fetch_opportunities(conn, snapshot_date, min_score, limit)
    conn.close()

    positions = list_positions(include_closed=False)
    alerts = _build_alerts(positions, min_score=min_score)
    return ReportData(snapshot_date=snapshot_date, opportunities=opportunities, positions=positions, alerts=alerts)


def _connect() -> sqlite3.Connection:
    _ensure_parent(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_snapshot_date(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT MAX(snapshot_date) FROM option_snapshots").fetchone()
    value = row[0] if row else None
    return str(value) if value else None


def _fetch_opportunities(
    conn: sqlite3.Connection,
    snapshot_date: str,
    min_score: int,
    limit: int,
) -> List[Dict[str, object]]:
    query = """
        SELECT
            ticker,
            underlying,
            "score_total",
            "trend_flag",
            "Status_Moneyness",
            "Status_Liquidez",
            "Status_2x",
            "iv_score",
            "em2x_score",
            "ultimo",
            "underlying_price",
            "%_Alta_p_2x",
            "custo_pct",
            "intrinsic_value",
            "extrinsic_value",
            "vol_fluxo_5d",
            "num_fluxo_5d"
        FROM option_snapshots
        WHERE snapshot_date = ?
          AND CAST("score_total" AS INTEGER) >= ?
          AND ("trend_flag" = '1' OR "trend_flag" = '')
        ORDER BY CAST("score_total" AS INTEGER) DESC,
                 CAST("%_Alta_p_2x" AS REAL) ASC NULLS LAST
        LIMIT ?
    """
    rows = conn.execute(query, (snapshot_date, min_score, limit)).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, object]:
    def parse_decimal(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        value = (
            value.replace("%", "")
            .replace("+", "")
            .replace("\u2212", "-")
            .replace("−", "-")
            .replace(".", "")
            .replace(",", ".")
        )
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

    return {
        "ticker": row["ticker"],
        "underlying": row["underlying"],
        "score_total": parse_int(row["score_total"]),
        "trend_flag": row["trend_flag"],
        "Status_Moneyness": row["Status_Moneyness"],
        "Status_Liquidez": row["Status_Liquidez"],
        "Status_2x": row["Status_2x"],
        "iv_score": parse_int(row["iv_score"]),
        "em2x_score": parse_int(row["em2x_score"]),
        "ultimo": parse_decimal(row["ultimo"]),
        "underlying_price": parse_decimal(row["underlying_price"]),
        "%_Alta_p_2x": parse_decimal(row["%_Alta_p_2x"]),
        "custo_pct": parse_decimal(row["custo_pct"]),
        "intrinsic_value": parse_decimal(row["intrinsic_value"]),
        "extrinsic_value": parse_decimal(row["extrinsic_value"]),
        "vol_fluxo_5d": parse_decimal(row["vol_fluxo_5d"]),
        "num_fluxo_5d": parse_decimal(row["num_fluxo_5d"]),
    }


def _build_alerts(positions: List[Dict[str, object]], *, min_score: int) -> List[Dict[str, object]]:
    alerts: List[Dict[str, object]] = []
    for pos in positions:
        reasons = []
        score = pos.get("score_total")
        trend = (pos.get("trend_flag") or "").strip()
        pl = pos.get("pl")
        pl_pct = pos.get("pl_pct")

        if trend and trend != "1":
            reasons.append("Trend flag não está 1")

        if score not in (None, ""):
            try:
                score_int = int(score)
                if score_int < min_score:
                    reasons.append(f"Score baixo ({score_int})")
            except ValueError:
                pass

        if pl_pct is not None:
            if pl_pct <= -50.0:
                reasons.append(f"Stop -50% atingido (P/L%={pl_pct:.2f}%)")
            elif pl_pct >= 100.0:
                reasons.append(f"Alvo 100% (dobro) atingido (P/L%={pl_pct:.2f}%)")
            elif pl_pct >= 50.0:
                reasons.append(f"Alvo +50% para parcial (P/L%={pl_pct:.2f}%)")
        elif pl is not None and pl < 0:
            reasons.append(f"P/L negativo ({pl:.2f})")

        if reasons:
            alerts.append({"position": pos, "reasons": reasons})
    return alerts


__all__ = ["generate_report", "ReportData"]
