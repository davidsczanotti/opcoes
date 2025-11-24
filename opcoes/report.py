from __future__ import annotations

import datetime as dt
import math
import sqlite3
import statistics
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
    theoretical_opportunities: List[Dict[str, object]]
    positions: List[Dict[str, object]]
    alerts: List[Dict[str, object]]
    recurring_opportunities: List[Dict[str, object]]
    recurring_window_start: Optional[str]
    recurring_window_days: int
    recurring_snapshot_days: int
    hv_window_days: int


def generate_report(
    *,
    min_score: int = 8,
    limit: int = 20,
    recurring_days: int = 30,
    recurring_limit: int = 15,
    hv_days: int = 21,
) -> ReportData:
    conn = _connect()
    snapshot_date = _latest_snapshot_date(conn)
    if not snapshot_date:
        conn.close()
        raise RuntimeError("Nenhum snapshot encontrado. Rode o scraper primeiro.")
    opportunities = _fetch_opportunities(conn, snapshot_date, min_score, limit)
    hv_map = _compute_hv_map(conn, snapshot_date, [o.get("underlying", "") for o in opportunities], hv_days)
    for opp in opportunities:
        underlying = (opp.get("underlying") or "").strip().upper()
        hv = hv_map.get(underlying)
        opp["hv_21d"] = hv
        iv = opp.get("vol_impl_perc")
        if hv is not None and iv is not None:
            opp["iv_hv_spread"] = iv - hv
        else:
            opp["iv_hv_spread"] = None
        ask = opp.get("best_ask")
        theo = opp.get("preco_teorico")
        if ask is not None and theo is not None and theo > 0:
            opp["desconto_teorico_pct"] = (theo - ask) / theo * 100.0
        else:
            opp["desconto_teorico_pct"] = None
    recurring_opps, window_start, snapshot_days = _fetch_recurring_opportunities(
        conn, snapshot_date, min_score, recurring_days, recurring_limit
    )
    conn.close()

    tradeable_opps: List[Dict[str, object]] = []
    theoretical_opps: List[Dict[str, object]] = []
    for opp in opportunities:
        if opp.get("best_ask") is not None:
            tradeable_opps.append(opp)
        elif opp.get("preco_teorico") is not None:
            theoretical_opps.append(opp)

    positions = list_positions(include_closed=False)
    alerts = _build_alerts(positions, min_score=min_score)
    return ReportData(
        snapshot_date=snapshot_date,
        opportunities=tradeable_opps,
        theoretical_opportunities=theoretical_opps,
        positions=positions,
        alerts=alerts,
        recurring_opportunities=recurring_opps,
        recurring_window_start=window_start,
        recurring_window_days=max(recurring_days, 0),
        recurring_snapshot_days=snapshot_days,
        hv_window_days=max(hv_days, 0),
    )


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
            "underlying_price_date",
            "dias_uteis",
            "Status_Moneyness",
            "Status_Liquidez",
            "Status_2x",
            "iv_score",
            "em2x_score",
            "delta",
            "ultimo",
            "underlying_price",
            "%_Alta_p_2x",
            "custo_pct",
            "intrinsic_value",
            "extrinsic_value",
            "extrinsic_pct_spot",
            "breakeven_price",
            "breakeven_dist_pct",
            "vol_fluxo_5d",
            "num_fluxo_5d",
            "iv_rank_180d",
            "vol_impl_perc",
            "best_bid",
            "best_ask",
            "spread_pct",
            "preco_teorico",
            "distorcao_preco_pct",
            "distorcao_flag",
            "illiquidez_flag"
        FROM option_snapshots
        WHERE snapshot_date = ?
          AND CAST("score_total" AS INTEGER) >= ?
          AND ("trend_flag" = '1' OR "trend_flag" = '')
          AND (
                "distorcao_flag" IS NULL
             OR "distorcao_flag" = ''
             OR ABS(CAST("distorcao_preco_pct" AS REAL)) <= 50.0
          )
        ORDER BY CAST("score_total" AS INTEGER) DESC,
                 CAST("%_Alta_p_2x" AS REAL) ASC NULLS LAST
        LIMIT ?
    """
    rows = conn.execute(query, (snapshot_date, min_score, limit)).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, object]:
    return {
        "ticker": row["ticker"],
        "underlying": row["underlying"],
        "score_total": _parse_int(row["score_total"]),
        "trend_flag": row["trend_flag"],
        "underlying_price_date": row["underlying_price_date"],
        "dias_uteis": _parse_int(row["dias_uteis"]),
        "Status_Moneyness": row["Status_Moneyness"],
        "Status_Liquidez": row["Status_Liquidez"],
        "Status_2x": row["Status_2x"],
        "iv_score": _parse_int(row["iv_score"]),
        "em2x_score": _parse_int(row["em2x_score"]),
        "delta": _parse_decimal(row["delta"]),
        "ultimo": _parse_decimal(row["ultimo"]),
        "underlying_price": _parse_decimal(row["underlying_price"]),
        "%_Alta_p_2x": _parse_decimal(row["%_Alta_p_2x"]),
        "custo_pct": _parse_decimal(row["custo_pct"]),
        "intrinsic_value": _parse_decimal(row["intrinsic_value"]),
        "extrinsic_value": _parse_decimal(row["extrinsic_value"]),
        "extrinsic_pct_spot": _parse_decimal(row["extrinsic_pct_spot"]),
        "breakeven_price": _parse_decimal(row["breakeven_price"]),
        "breakeven_dist_pct": _parse_decimal(row["breakeven_dist_pct"]),
        "vol_fluxo_5d": _parse_decimal(row["vol_fluxo_5d"]),
        "num_fluxo_5d": _parse_decimal(row["num_fluxo_5d"]),
        "iv_rank_180d": _parse_decimal(row["iv_rank_180d"]),
        "vol_impl_perc": _parse_decimal(row["vol_impl_perc"]),
        "best_bid": _parse_decimal(row["best_bid"]),
        "best_ask": _parse_decimal(row["best_ask"]),
        "spread_pct": _parse_decimal(row["spread_pct"]),
        "preco_teorico": _parse_decimal(row["preco_teorico"]),
        "distorcao_preco_pct": _parse_decimal(row["distorcao_preco_pct"]),
        "distorcao_flag": (row["distorcao_flag"] or "").strip(),
        "illiquidez_flag": (row["illiquidez_flag"] or "").strip(),
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


def _fetch_recurring_opportunities(
    conn: sqlite3.Connection,
    latest_snapshot_date: str,
    min_score: int,
    history_days: int,
    limit: int,
) -> tuple[List[Dict[str, object]], Optional[str], int]:
    if history_days <= 0:
        window_start = latest_snapshot_date
    else:
        try:
            latest = dt.date.fromisoformat(latest_snapshot_date)
            window_start = (latest - dt.timedelta(days=history_days - 1)).isoformat()
        except ValueError:
            window_start = latest_snapshot_date

    total_snapshots_row = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_date) FROM option_snapshots WHERE snapshot_date >= ?",
        (window_start,),
    ).fetchone()
    snapshot_days = int(total_snapshots_row[0] or 0) if total_snapshots_row else 0

    filtered_query = """
        WITH filtered AS (
            SELECT *
            FROM option_snapshots
            WHERE snapshot_date >= ?
              AND CAST("score_total" AS INTEGER) >= ?
              AND ("trend_flag" = '1' OR "trend_flag" = '')
        ),
        agg AS (
            SELECT
                ticker,
                MAX(underlying) AS underlying,
                COUNT(*) AS hits,
                MIN(snapshot_date) AS first_seen,
                MAX(snapshot_date) AS last_seen
            FROM filtered
            GROUP BY ticker
        )
        SELECT
            agg.ticker,
            agg.underlying,
            agg.hits,
            agg.first_seen,
            agg.last_seen,
            f.score_total AS last_score,
            f."%_Alta_p_2x" AS pct_2x,
            f.ultimo AS last_price,
            f.underlying_price AS last_underlying_price,
            f.dias_uteis AS dias_uteis
        FROM agg
        LEFT JOIN filtered f ON f.ticker = agg.ticker AND f.snapshot_date = agg.last_seen
        ORDER BY agg.hits DESC, agg.last_seen DESC
        LIMIT ?
    """
    rows = conn.execute(filtered_query, (window_start, min_score, limit)).fetchall()
    results: List[Dict[str, object]] = []
    for row in rows:
        hits = _parse_int(row["hits"]) or 0
        results.append(
            {
                "ticker": row["ticker"],
                "underlying": row["underlying"],
                "hits": hits,
                "presence_pct": (hits / snapshot_days * 100.0) if snapshot_days else None,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "score_total": _parse_int(row["last_score"]),
                "%_Alta_p_2x": _parse_decimal(row["pct_2x"]),
                "ultimo": _parse_decimal(row["last_price"]),
                "underlying_price": _parse_decimal(row["last_underlying_price"]),
                "dias_uteis": _parse_int(row["dias_uteis"]),
            }
        )
    return results, window_start, snapshot_days


def _compute_hv_map(
    conn: sqlite3.Connection,
    snapshot_date: str,
    underlyings: List[str],
    window_days: int,
) -> Dict[str, Optional[float]]:
    unique = sorted({(u or "").strip().upper() for u in underlyings if (u or "").strip()})
    if not unique or window_days <= 0:
        return {}
    try:
        latest = dt.date.fromisoformat(snapshot_date)
        start_date = (latest - dt.timedelta(days=window_days * 2)).isoformat()
    except ValueError:
        start_date = snapshot_date

    placeholders = ",".join(["?"] * len(unique))
    query = f"""
        SELECT underlying, snapshot_date, price
        FROM underlying_snapshots
        WHERE underlying IN ({placeholders})
          AND snapshot_date BETWEEN ? AND ?
        ORDER BY underlying, snapshot_date
    """
    rows = conn.execute(query, (*unique, start_date, snapshot_date)).fetchall()

    grouped: Dict[str, Dict[str, float]] = {}
    for row in rows:
        sym = (row["underlying"] or "").strip().upper()
        try:
            price_val = float(row["price"])
        except (TypeError, ValueError):
            continue
        date_val = (row["snapshot_date"] or "").strip()
        if not sym or not date_val:
            continue
        grouped.setdefault(sym, {})[date_val] = price_val

    hv_map: Dict[str, Optional[float]] = {}
    for sym, price_by_date in grouped.items():
        # Evita HV irreal quando há poucos pontos: exige mínimo de observações
        min_obs = max(5, window_days // 2)  # precisa de ao menos ~metade da janela, mínimo 5
        if len(price_by_date) < min_obs:
            hv_map[sym] = None
            continue
        sorted_prices = [price_by_date[d] for d in sorted(price_by_date)]
        log_returns: List[float] = []
        for prev, curr in zip(sorted_prices, sorted_prices[1:]):
            if prev is None or curr is None or prev <= 0 or curr <= 0:
                continue
            try:
                log_returns.append(math.log(curr / prev))
            except ValueError:
                continue
        if len(log_returns) < 2:
            hv_map[sym] = None
            continue
        try:
            std_dev = statistics.stdev(log_returns)
            hv = std_dev * math.sqrt(252) * 100.0
            hv_map[sym] = hv
        except statistics.StatisticsError:
            hv_map[sym] = None
    return hv_map


def _parse_decimal(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = str(value).strip()
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


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


__all__ = ["generate_report", "ReportData"]
