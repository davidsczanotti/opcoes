from __future__ import annotations

from collections import Counter
import datetime as dt
import re
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yfinance as yf

from ..fundamentus import fetch_approved_ranking, fetch_signals, fetch_snapshot, latest_snapshot_date
from ..config import get_db_path
from ..settings import get_fundamentus_settings
from ..utils import parse_ptbr_number


_SECTOR_LABELS = {
    "insurance": "Seguradoras",
    "insurance - property & casualty": "Seguradoras",
    "insurance - diversified": "Seguradoras",
    "banks": "Bancos",
    "diversified banks": "Bancos",
    "regional banks": "Bancos",
    "financial services": "Servicos financeiros",
    "other industrial metals & mining": "Mineracao",
    "steel": "Siderurgia",
    "oil & gas integrated": "Petroleo e gas",
    "oil & gas e&p": "Petroleo e gas",
    "oil & gas equipment & services": "Servicos de petroleo e gas",
    "utilities": "Utilities",
    "electric utilities": "Energia eletrica",
    "multiline utilities": "Energia eletrica",
    "real estate": "Imobiliario",
    "consumer defensive": "Consumo nao ciclico",
    "consumer cyclical": "Consumo ciclico",
    "communication services": "Comunicacoes",
    "telecom services": "Telecom",
    "basic materials": "Materiais basicos",
    "healthcare": "Saude",
    "technology": "Tecnologia",
    "industrials": "Industriais",
    "energy": "Energia",
}
_SECTOR_PREFIXES = {
    "insurance": "Seguradoras",
    "bank": "Bancos",
    "oil & gas": "Petroleo e gas",
    "utilities": "Utilities",
    "real estate": "Imobiliario",
    "telecom": "Telecom",
    "steel": "Siderurgia",
    "mining": "Mineracao",
}
_SECTOR_PALETTE = [
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc949",
    "#af7aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ab",
]
_TICKER_META_CACHE: Dict[str, Dict[str, Optional[str]]] = {}
_TARGET_YIELD_PCT = 8.0

def _parse_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return dt.datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def _next_month_third_friday(base_date: dt.date) -> dt.date:
    year = base_date.year + (1 if base_date.month == 12 else 0)
    month = 1 if base_date.month == 12 else base_date.month + 1
    first = dt.date(year, month, 1)
    first_friday = first + dt.timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + dt.timedelta(days=14)


def _latest_option_snapshot_date() -> Optional[str]:
    conn = sqlite3.connect(get_db_path())
    try:
        row = conn.execute("SELECT MAX(snapshot_date) AS d FROM option_snapshots").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _fetch_underlying_prices(snapshot_date: str, underlyings: Sequence[str]) -> Dict[str, float]:
    if not snapshot_date or not underlyings:
        return {}
    conn = sqlite3.connect(get_db_path())
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join(["?"] * len(underlyings))
        params = [snapshot_date] + [u.upper() for u in underlyings]
        query = """
            SELECT underlying, price
            FROM underlying_snapshots
            WHERE snapshot_date = ?
              AND UPPER(underlying) IN ({placeholders})
        """.format(placeholders=placeholders)
        rows = conn.execute(query, params).fetchall()
        prices: Dict[str, float] = {}
        for row in rows:
            price = row["price"]
            if price is None:
                continue
            prices[str(row["underlying"]).upper()] = float(price)
        return prices
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _fetch_put_rows(snapshot_date: str, underlyings: Sequence[str]) -> List[Dict[str, Any]]:
    if not snapshot_date or not underlyings:
        return []
    conn = sqlite3.connect(get_db_path())
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join(["?"] * len(underlyings))
        params = [snapshot_date] + [u.upper() for u in underlyings]
        query = """
            SELECT underlying, ticker, vencimento, strike, ultimo, best_bid, mod
            FROM option_snapshots
            WHERE snapshot_date = ?
              AND UPPER(underlying) IN ({placeholders})
              AND UPPER(option_type) LIKE 'PUT%'
        """.format(placeholders=placeholders)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _build_put_opportunities(
    *,
    fundamentals: Sequence[Mapping[str, Any]],
    option_rows: Sequence[Mapping[str, Any]],
    target_vencimento: Optional[dt.date],
    distance_limit_pct: float,
    price_map: Mapping[str, float],
) -> List[Dict[str, Any]]:
    if not target_vencimento:
        return []
    spot_by_ticker: Dict[str, float] = {}
    for row in fundamentals:
        papel = str(row.get("papel") or "").strip().upper()
        if not papel:
            continue
        cotacao = parse_ptbr_number(row.get("cotacao"))
        if cotacao is None:
            continue
        spot_by_ticker[papel] = float(cotacao)

    opportunities: List[Dict[str, Any]] = []
    for opt in option_rows:
        underlying = str(opt.get("underlying") or "").strip().upper()
        if not underlying:
            continue
        venc = _parse_date(str(opt.get("vencimento") or "").strip())
        if venc != target_vencimento:
            continue
        spot = spot_by_ticker.get(underlying)
        if spot is None:
            spot = price_map.get(underlying)
        if spot is None or spot <= 0:
            continue
        strike = parse_ptbr_number(opt.get("strike"))
        if strike is None or strike <= 0:
            continue
        if strike >= spot:
            continue
        distance_pct = (spot - strike) / spot * 100.0
        if distance_pct <= 0 or distance_pct > distance_limit_pct:
            continue
        premium = parse_ptbr_number(opt.get("ultimo"))
        if premium is None or premium <= 0:
            premium = parse_ptbr_number(opt.get("best_bid"))
        if premium is None or premium <= 0:
            continue
        premium_pct = (premium / strike) * 100.0
        opportunities.append(
            {
                "papel": underlying,
                "cotacao": spot,
                "contrato": opt.get("ticker"),
                "strike": strike,
                "ultimo": premium,
                "premio_pct": premium_pct,
                "distancia_strike_pct": distance_pct,
            }
        )

    opportunities.sort(
        key=lambda row: (
            row.get("papel") or "",
            -(row.get("premio_pct") or 0.0),
            row.get("strike") or 0.0,
        )
    )
    return opportunities


def _base_ticker(papel: str) -> str:
    return re.sub(r"\d+$", "", (papel or "")).strip().upper()


def _liquidez_key(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("liquidez_2m") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_option_underlyings() -> set[str]:
    conn = sqlite3.connect(get_db_path())
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT MAX(snapshot_date) AS d FROM option_snapshots").fetchone()
        snapshot_date = row["d"] if row else None
        if not snapshot_date:
            return set()
        rows = conn.execute(
            "SELECT DISTINCT underlying FROM option_snapshots WHERE snapshot_date = ?",
            (snapshot_date,),
        ).fetchall()
        return {str(r[0]).strip().upper() for r in rows if r and r[0]}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def _dedupe_by_option_listing(
    rows: List[Dict[str, Any]],
    option_underlyings: set[str],
) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        papel = (row.get("papel") or "").strip().upper()
        if not papel:
            continue
        base = _base_ticker(papel)
        grouped.setdefault(base, []).append(row)
    output: List[Dict[str, Any]] = []
    for _, group in grouped.items():
        if len(group) == 1:
            output.append(group[0])
            continue
        preferred = [
            row
            for row in group
            if (row.get("papel") or "").strip().upper() in option_underlyings
        ]
        candidates = preferred or group
        best = max(candidates, key=_liquidez_key)
        output.append(best)
    return output


def _to_yahoo_symbol(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    s = symbol.strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    return f"{s}.SA"


def _ensure_ticker_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ticker_metadata (
            ticker TEXT PRIMARY KEY,
            sector TEXT,
            industry TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _load_cached_metadata(tickers: Sequence[str]) -> tuple[Dict[str, Dict[str, Optional[str]]], List[str]]:
    cached: Dict[str, Dict[str, Optional[str]]] = {}
    missing: List[str] = []
    for ticker in tickers:
        meta = _TICKER_META_CACHE.get(ticker)
        if meta is not None:
            cached[ticker] = meta
        else:
            missing.append(ticker)
    if not missing:
        return cached, []

    conn = sqlite3.connect(get_db_path())
    try:
        conn.row_factory = sqlite3.Row
        _ensure_ticker_metadata_table(conn)
        placeholders = ",".join(["?"] * len(missing))
        query = f"SELECT ticker, sector, industry FROM ticker_metadata WHERE ticker IN ({placeholders})"
        rows = conn.execute(query, missing).fetchall()
        for row in rows:
            meta = {"sector": row["sector"], "industry": row["industry"]}
            cached[row["ticker"]] = meta
            _TICKER_META_CACHE[row["ticker"]] = meta
    except sqlite3.Error:
        return cached, missing
    finally:
        conn.close()

    remaining = [t for t in missing if t not in cached]
    return cached, remaining


def _save_metadata(entries: Dict[str, Dict[str, Optional[str]]]) -> None:
    if not entries:
        return
    conn = sqlite3.connect(get_db_path())
    try:
        _ensure_ticker_metadata_table(conn)
        payload = [
            (ticker, data.get("sector"), data.get("industry"))
            for ticker, data in entries.items()
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO ticker_metadata (ticker, sector, industry, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            payload,
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def _fetch_metadata_yf(tickers: Sequence[str]) -> Dict[str, Dict[str, Optional[str]]]:
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for ticker in tickers:
        yahoo = _to_yahoo_symbol(ticker)
        if not yahoo:
            out[ticker] = {"sector": None, "industry": None}
            continue
        try:
            info = yf.Ticker(yahoo).get_info()
        except Exception:  # noqa: BLE001
            out[ticker] = {"sector": None, "industry": None}
            continue
        out[ticker] = {"sector": info.get("sector"), "industry": info.get("industry")}
    return out


def _normalize_sector_label(raw: Optional[str]) -> str:
    if not raw:
        return "Sem setor"
    text = str(raw).strip()
    if not text:
        return "Sem setor"
    key = text.lower()
    if key in _SECTOR_LABELS:
        return _SECTOR_LABELS[key]
    for prefix, label in _SECTOR_PREFIXES.items():
        if key.startswith(prefix):
            return label
    return text


def _attach_sector_info(rows: List[Dict[str, Any]]) -> None:
    tickers = [str(row.get("papel") or "").strip().upper() for row in rows]
    tickers = list(dict.fromkeys([t for t in tickers if t]))
    if not tickers:
        return
    cached, missing = _load_cached_metadata(tickers)
    fetched: Dict[str, Dict[str, Optional[str]]] = {}
    if missing:
        fetched = _fetch_metadata_yf(missing)
        if fetched:
            _save_metadata(fetched)
            for ticker, data in fetched.items():
                _TICKER_META_CACHE[ticker] = data
            cached.update(fetched)

    for row in rows:
        papel = str(row.get("papel") or "").strip().upper()
        meta = cached.get(papel, {"sector": None, "industry": None})
        label = _normalize_sector_label(meta.get("industry") or meta.get("sector"))
        row["sector"] = label


def _build_sector_breakdown(rows: List[Dict[str, Any]]) -> List[Dict[str, object]]:
    counter: Counter[str] = Counter()
    for row in rows:
        label = str(row.get("sector") or "Sem setor").strip() or "Sem setor"
        counter[label] += 1
    total = sum(counter.values())
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    breakdown: List[Dict[str, object]] = []
    for idx, (label, count) in enumerate(items):
        color = _SECTOR_PALETTE[idx % len(_SECTOR_PALETTE)]
        pct = (count / total * 100.0) if total else 0.0
        breakdown.append({"label": label, "count": count, "pct": pct, "color": color})
    return breakdown


def _attach_price_ceiling(
    rows: List[Dict[str, Any]],
    *,
    target_yield_pct: float = _TARGET_YIELD_PCT,
) -> None:
    if not rows or target_yield_pct <= 0:
        return
    for row in rows:
        dy = row.get("div_yield")
        price = row.get("cotacao")
        try:
            dy_val = float(dy) if dy is not None else None
            price_val = float(price) if price is not None else None
        except (TypeError, ValueError):
            row["preco_teto"] = None
            continue
        if not dy_val or not price_val:
            row["preco_teto"] = None
            continue
        row["preco_teto"] = price_val * (dy_val / target_yield_pct)


def _get_optional_int_arg(args: Mapping[str, Any], name: str) -> Optional[int]:
    try:
        raw = args.get(name)
        if raw is None or str(raw).strip() == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def _get_int_arg(args: Mapping[str, Any], name: str, default: int) -> int:
    try:
        raw = args.get(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_status_filter(args: Mapping[str, Any], *, has_signals: bool) -> str:
    raw = (args.get("status") or "").strip().lower()
    if raw in ("approved", "rejected", "all"):
        return raw
    return "approved" if has_signals else "all"


def get_fundamentus_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    limit = _get_optional_int_arg(args, "limit")
    snap = (args.get("date") or "").strip()
    if not snap:
        snap = latest_snapshot_date() or ""
    rows = fetch_snapshot(snapshot_date=snap or None, limit=limit)
    signals = fetch_signals(snapshot_date=snap or None)
    signals_map = {s["papel"]: s for s in signals if s.get("papel")}
    for row in rows:
        signal = signals_map.get(row.get("papel"))
        if signal:
            row["signal"] = signal

    if not rows:
        message = "Em construcao: aguardando definicao de filtros e coleta no Fundamentus."
    elif signals:
        message = "Snapshot e filtros carregados a partir do banco."
    else:
        message = "Snapshot carregado, filtros ainda nao aplicados."

    approved_count = sum(1 for s in signals if s.get("status") == "approved")
    rejected_count = sum(1 for s in signals if s.get("status") == "rejected")
    status_filter = _get_status_filter(args, has_signals=bool(signals))
    if status_filter == "approved":
        filtered_rows = [row for row in rows if row.get("signal", {}).get("status") == "approved"]
    elif status_filter == "rejected":
        filtered_rows = [row for row in rows if row.get("signal", {}).get("status") == "rejected"]
    else:
        filtered_rows = rows
    status_label = {"approved": "Aprovadas", "rejected": "Reprovadas", "all": "Todas"}.get(
        status_filter, "Todas"
    )

    fund_cfg = get_fundamentus_settings()
    target_yield_pct = fund_cfg.target_yield_pct or _TARGET_YIELD_PCT

    option_underlyings = _fetch_option_underlyings()
    filtered_rows = _dedupe_by_option_listing(filtered_rows, option_underlyings)
    if filtered_rows:
        _attach_sector_info(filtered_rows)
        _attach_price_ceiling(filtered_rows, target_yield_pct=target_yield_pct)
    sector_breakdown = _build_sector_breakdown(filtered_rows) if filtered_rows else []

    option_snapshot_date = _latest_option_snapshot_date()
    target_vencimento = None
    put_distance_limit_pct = 15.0
    put_opportunities: List[Dict[str, Any]] = []
    if option_snapshot_date:
        base_date = _parse_date(option_snapshot_date) or _parse_date(snap) or dt.date.today()
        target_vencimento = _next_month_third_friday(base_date)
    if filtered_rows and option_snapshot_date and target_vencimento:
        underlyings = [
            str(row.get("papel") or "").strip().upper()
            for row in filtered_rows
            if row.get("papel")
        ]
        option_rows = _fetch_put_rows(option_snapshot_date, underlyings)
        price_map = _fetch_underlying_prices(option_snapshot_date, underlyings)
        put_opportunities = _build_put_opportunities(
            fundamentals=filtered_rows,
            option_rows=option_rows,
            target_vencimento=target_vencimento,
            distance_limit_pct=put_distance_limit_pct,
            price_map=price_map,
        )
    put_target_vencimento = (
        target_vencimento.strftime("%d/%m/%Y") if target_vencimento else None
    )

    window_days = max(1, _get_int_arg(args, "window_days", 30))
    ranking_total = fetch_approved_ranking(snapshot_date=snap or None, limit=20)
    ranking_window = fetch_approved_ranking(
        snapshot_date=snap or None,
        window_days=window_days,
        limit=20,
    )
    return {
        "status": "em_construcao" if not rows else "ok",
        "message": message,
        "snapshot_date": snap or None,
        "rows": filtered_rows,
        "total_rows": len(rows),
        "limit": limit,
        "filtered_rows_count": len(filtered_rows),
        "signals_available": bool(signals),
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "status_filter": status_filter,
        "status_label": status_label,
        "target_yield_pct": target_yield_pct,
        "sector_breakdown": sector_breakdown,
        "put_opportunities": put_opportunities,
        "put_target_vencimento": put_target_vencimento,
        "put_snapshot_date": option_snapshot_date,
        "put_distance_limit_pct": put_distance_limit_pct,
        "ranking_total": ranking_total["rows"],
        "ranking_window": ranking_window["rows"],
        "ranking_window_days": window_days,
        "ranking_window_start": ranking_window["start_date"],
        "ranking_window_end": ranking_window["end_date"],
    }


__all__ = ["get_fundamentus_context"]
