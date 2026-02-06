from __future__ import annotations

from collections import Counter
import datetime as dt
import re
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yfinance as yf

from ..fundamentus import (
    fetch_approved_ranking,
    fetch_filter_run,
    fetch_signal_dates,
    fetch_signals,
    fetch_snapshot,
    latest_snapshot_date,
)
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
_PUT_DISTANCE_LIMIT_PCT = 15.0
_PUT_MIN_PREMIUM_PCT = 0.5
_PUT_TARGET_MONTHLY_YIELD_PCT = 1.0
_PUT_MIN_SCORE = 4.0
_PUT_SCORE_WEIGHTS = {
    "safety": 0.35,
    "income": 0.30,
    "quality": 0.25,
    "execution": 0.10,
}
_REASON_LABELS = {
    "approved": "Aprovada em todos os filtros.",
    "missing_liquidez_2m": "Sem dado de liquidez de 2 meses.",
    "liquidez_2m_below_min": "Liquidez de 2 meses abaixo do minimo.",
    "missing_div_bruta_patrim": "Sem dado de divida bruta/patrimonio.",
    "div_bruta_patrim_above_max": "Divida bruta/patrimonio acima do limite.",
    "missing_cresc_rec_5a": "Sem dado de crescimento de receita em 5 anos.",
    "cresc_rec_5a_below_min": "Crescimento de receita em 5 anos abaixo do minimo.",
    "missing_div_yield": "Sem dado de dividend yield.",
    "div_yield_below_min": "Dividend yield abaixo do minimo.",
    "missing_roe": "Sem dado de ROE.",
    "roe_below_min": "ROE abaixo do minimo.",
    "missing_margem_liquida": "Sem dado de margem liquida.",
    "margem_liquida_out_of_rule": "Margem liquida fora da regra minima.",
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _translate_reason(reason: Optional[str]) -> str:
    key = (reason or "").strip().lower()
    if not key:
        return "Sem motivo registrado."
    if key in _REASON_LABELS:
        return _REASON_LABELS[key]
    return key.replace("_", " ")

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


def _build_fundamentals_index(
    fundamentals: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Optional[float]]]:
    idx: Dict[str, Dict[str, Optional[float]]] = {}
    for row in fundamentals:
        papel = str(row.get("papel") or "").strip().upper()
        if not papel:
            continue
        idx[papel] = {
            "div_yield": _safe_float(row.get("div_yield")),
            "roe": _safe_float(row.get("roe")),
            "margem_liquida": _safe_float(row.get("margem_liquida")),
            "div_bruta_patrim": _safe_float(row.get("div_bruta_patrim")),
        }
    return idx


def _compute_fund_quality_score(fund: Mapping[str, Optional[float]]) -> float:
    dy = fund.get("div_yield")
    roe = fund.get("roe")
    margem = fund.get("margem_liquida")
    debt = fund.get("div_bruta_patrim")

    components: List[float] = []
    if dy is not None and dy > 0:
        components.append(_clamp(dy / 8.0))
    if roe is not None and roe > 0:
        components.append(_clamp(roe / 20.0))
    if margem is not None and margem > 0:
        components.append(_clamp(margem / 15.0))
    if debt is not None:
        components.append(1.0 - _clamp(debt / 2.0))

    if not components:
        return 5.0
    return (sum(components) / len(components)) * 10.0


def _classify_put_profile(distance_pct: float, monthly_yield_pct: Optional[float], has_bid: bool) -> str:
    monthly = monthly_yield_pct or 0.0
    if distance_pct >= 8.0 and monthly <= 2.5 and has_bid:
        return "Conservadora"
    if distance_pct >= 4.0:
        return "Equilibrada"
    return "Agressiva"


def _execution_note(*, has_bid: bool, distance_pct: float) -> str:
    if not has_bid:
        return "Sem bid no book; tratar como watchlist."
    if distance_pct < 2.0:
        return "Strike muito proximo do spot."
    return "Book com bid; execucao mais confiavel."


def _build_put_opportunities(
    *,
    fundamentals: Sequence[Mapping[str, Any]],
    option_rows: Sequence[Mapping[str, Any]],
    target_vencimento: Optional[dt.date],
    distance_limit_pct: float,
    min_premium_pct: float,
    target_monthly_yield_pct: float,
    min_score: float,
    asof_date: Optional[dt.date],
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
    fundamentals_idx = _build_fundamentals_index(fundamentals)

    distance_target_pct = max(3.0, min(distance_limit_pct * 0.6, 10.0))
    monthly_target_pct = max(0.1, target_monthly_yield_pct)

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

        bid = parse_ptbr_number(opt.get("best_bid"))
        ultimo = parse_ptbr_number(opt.get("ultimo"))
        premium_source = ""
        premium: Optional[float] = None
        if bid is not None and bid > 0:
            premium = bid
            premium_source = "best_bid"
        elif ultimo is not None and ultimo > 0:
            premium = ultimo
            premium_source = "ultimo"
        if premium is None or premium <= 0:
            continue

        premium_pct = (premium / strike) * 100.0
        if premium_pct < min_premium_pct:
            continue

        days_to_exp = None
        if asof_date is not None:
            days_to_exp = (venc - asof_date).days
            if days_to_exp <= 0:
                continue

        monthly_yield_pct = None
        if days_to_exp and days_to_exp > 0:
            monthly_yield_pct = premium_pct * (30.0 / float(days_to_exp))

        safety_score = _clamp(distance_pct / distance_target_pct) * 10.0
        income_base = monthly_yield_pct if monthly_yield_pct is not None else premium_pct
        income_score = _clamp(income_base / monthly_target_pct) * 10.0
        quality_score = _compute_fund_quality_score(fundamentals_idx.get(underlying, {}))
        execution_score = 10.0 if premium_source == "best_bid" else 3.5

        put_score = (
            (_PUT_SCORE_WEIGHTS["safety"] * safety_score)
            + (_PUT_SCORE_WEIGHTS["income"] * income_score)
            + (_PUT_SCORE_WEIGHTS["quality"] * quality_score)
            + (_PUT_SCORE_WEIGHTS["execution"] * execution_score)
        )
        if put_score < min_score:
            continue

        profile = _classify_put_profile(
            distance_pct=distance_pct,
            monthly_yield_pct=monthly_yield_pct,
            has_bid=(premium_source == "best_bid"),
        )

        opportunities.append(
            {
                "papel": underlying,
                "cotacao": spot,
                "contrato": opt.get("ticker"),
                "strike": strike,
                "ultimo": ultimo,
                "best_bid": bid,
                "preco_ref": premium,
                "premium_source": premium_source,
                "premio_pct": premium_pct,
                "premio_mensal_pct": monthly_yield_pct,
                "distancia_strike_pct": distance_pct,
                "dias_ate_vencimento": days_to_exp,
                "put_score": put_score,
                "score_safety": safety_score,
                "score_income": income_score,
                "score_quality": quality_score,
                "score_execution": execution_score,
                "put_profile": profile,
                "execution_note": _execution_note(
                    has_bid=(premium_source == "best_bid"),
                    distance_pct=distance_pct,
                ),
            }
        )

    opportunities.sort(
        key=lambda row: (
            -(row.get("put_score") or 0.0),
            row.get("premium_source") != "best_bid",
            -(row.get("premio_pct") or 0.0),
            -(row.get("distancia_strike_pct") or 0.0),
            row.get("papel") or "",
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


def _attach_peg_ratio(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    for row in rows:
        pl = row.get("pl")
        growth = row.get("cresc_rec_5a")
        try:
            pl_val = float(pl) if pl is not None else None
            growth_val = float(growth) if growth is not None else None
        except (TypeError, ValueError):
            row["peg_ratio"] = None
            continue
        if pl_val is None or growth_val is None or pl_val <= 0 or growth_val <= 0:
            row["peg_ratio"] = None
            continue
        row["peg_ratio"] = pl_val / growth_val


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


def _signal_with_labels(signal: Mapping[str, Any]) -> Dict[str, Any]:
    data = dict(signal)
    reason = (data.get("reason") or "").strip().lower()
    data["reason_label"] = _translate_reason(reason)
    return data


def _build_put_profile_breakdown(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    order = ["Conservadora", "Equilibrada", "Agressiva"]
    counts = Counter(str(row.get("put_profile") or "").strip() for row in rows)
    out: List[Dict[str, Any]] = []
    for label in order:
        count = int(counts.get(label, 0))
        if count <= 0:
            continue
        out.append({"label": label, "count": count})
    return out


def get_fundamentus_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    limit = _get_optional_int_arg(args, "limit")
    snap = (args.get("date") or "").strip()
    if not snap:
        snap = latest_snapshot_date() or ""
    rows = fetch_snapshot(snapshot_date=snap or None, limit=limit)
    signals = fetch_signals(snapshot_date=snap or None)
    filter_run = fetch_filter_run(snapshot_date=snap or None)
    signals_map = {
        s["papel"]: _signal_with_labels(s)
        for s in signals
        if s.get("papel")
    }
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
    put_distance_limit_pct = max(1.0, fund_cfg.put_distance_limit_pct or _PUT_DISTANCE_LIMIT_PCT)
    put_min_premium_pct = max(0.0, fund_cfg.put_min_premium_pct or _PUT_MIN_PREMIUM_PCT)
    put_target_monthly_yield_pct = max(
        0.1,
        fund_cfg.put_target_monthly_yield_pct or _PUT_TARGET_MONTHLY_YIELD_PCT,
    )
    put_min_score = max(0.0, min(10.0, fund_cfg.put_min_score or _PUT_MIN_SCORE))

    option_underlyings = _fetch_option_underlyings()

    changes_reference_date = None
    entered_opportunities: List[str] = []
    exited_opportunities: List[str] = []
    current_approved_count = 0
    previous_approved_count = 0

    if signals and snap:
        signal_dates = fetch_signal_dates(end_date=snap, limit=2)
        if signal_dates and signal_dates[0] == snap and len(signal_dates) > 1:
            previous_snapshot = signal_dates[1]
            prev_rows = fetch_snapshot(snapshot_date=previous_snapshot, limit=limit)
            prev_signals = fetch_signals(snapshot_date=previous_snapshot)
            prev_signals_map = {s["papel"]: s for s in prev_signals if s.get("papel")}
            for row in prev_rows:
                prev_signal = prev_signals_map.get(row.get("papel"))
                if prev_signal:
                    row["signal"] = prev_signal

            current_approved_rows = [
                row for row in rows if row.get("signal", {}).get("status") == "approved"
            ]
            prev_approved_rows = [
                row for row in prev_rows if row.get("signal", {}).get("status") == "approved"
            ]
            current_approved_rows = _dedupe_by_option_listing(
                current_approved_rows,
                option_underlyings,
            )
            prev_approved_rows = _dedupe_by_option_listing(
                prev_approved_rows,
                option_underlyings,
            )

            current_set = {
                (row.get("papel") or "").strip().upper()
                for row in current_approved_rows
                if row.get("papel")
            }
            prev_set = {
                (row.get("papel") or "").strip().upper()
                for row in prev_approved_rows
                if row.get("papel")
            }
            entered_opportunities = sorted(current_set - prev_set)
            exited_opportunities = sorted(prev_set - current_set)
            current_approved_count = len(current_set)
            previous_approved_count = len(prev_set)
            changes_reference_date = previous_snapshot

    filtered_rows = _dedupe_by_option_listing(filtered_rows, option_underlyings)
    if filtered_rows:
        _attach_sector_info(filtered_rows)
        _attach_price_ceiling(filtered_rows, target_yield_pct=target_yield_pct)
        _attach_peg_ratio(filtered_rows)
    sector_breakdown = _build_sector_breakdown(filtered_rows) if filtered_rows else []

    option_snapshot_date = _latest_option_snapshot_date()
    target_vencimento = None
    put_watchlist_count = 0
    put_profile_breakdown: List[Dict[str, Any]] = []
    put_base_date = None
    fund_snapshot_dt = _parse_date(snap)
    option_snapshot_dt = _parse_date(option_snapshot_date)
    snapshot_lag_days = None
    if fund_snapshot_dt and option_snapshot_dt:
        snapshot_lag_days = (fund_snapshot_dt - option_snapshot_dt).days
    put_opportunities: List[Dict[str, Any]] = []
    if option_snapshot_date:
        put_base_date = _parse_date(option_snapshot_date) or _parse_date(snap) or dt.date.today()
        target_vencimento = _next_month_third_friday(put_base_date)
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
            min_premium_pct=put_min_premium_pct,
            target_monthly_yield_pct=put_target_monthly_yield_pct,
            min_score=put_min_score,
            asof_date=put_base_date,
            price_map=price_map,
        )
        put_watchlist_count = sum(1 for row in put_opportunities if row.get("premium_source") != "best_bid")
        put_profile_breakdown = _build_put_profile_breakdown(put_opportunities)
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
        "filter_run": filter_run,
        "target_yield_pct": target_yield_pct,
        "sector_breakdown": sector_breakdown,
        "changes_reference_date": changes_reference_date,
        "entered_opportunities": entered_opportunities,
        "exited_opportunities": exited_opportunities,
        "current_approved_count": current_approved_count,
        "previous_approved_count": previous_approved_count,
        "put_opportunities": put_opportunities,
        "put_target_vencimento": put_target_vencimento,
        "put_snapshot_date": option_snapshot_date,
        "put_watchlist_count": put_watchlist_count,
        "put_profile_breakdown": put_profile_breakdown,
        "put_distance_limit_pct": put_distance_limit_pct,
        "put_min_premium_pct": put_min_premium_pct,
        "put_target_monthly_yield_pct": put_target_monthly_yield_pct,
        "put_min_score": put_min_score,
        "put_score_formula": "35% seguranca + 30% renda + 25% qualidade + 10% execucao",
        "snapshot_lag_days": snapshot_lag_days,
        "ranking_total": ranking_total["rows"],
        "ranking_window": ranking_window["rows"],
        "ranking_window_days": window_days,
        "ranking_window_start": ranking_window["start_date"],
        "ranking_window_end": ranking_window["end_date"],
    }


__all__ = ["get_fundamentus_context"]
