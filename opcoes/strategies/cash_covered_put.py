from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..snapshot_repository import fetch_latest_underlying_options, fetch_latest_underlying_quote
from ..scraper.storage import _parse_ptbr_number
from ..utils import infer_option_type


def _get_int_arg(args: Mapping[str, Any], name: str, default: int) -> int:
    try:
        raw = args.get(name, default)
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_float_arg(args: Mapping[str, Any], name: str, default: float) -> float:
    try:
        raw = args.get(name, default)
        return float(raw)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any) -> Optional[float]:
    try:
        parsed = _parse_ptbr_number(value)
    except Exception:
        return None
    if parsed is None:
        return None
    try:
        return float(parsed)
    except Exception:
        return None


def _premium_from_row(row: Mapping[str, Any]) -> Tuple[Optional[float], str]:
    for key in ("best_bid", "ultimo", "preco_teorico"):
        val = _parse_float(row.get(key))
        if val is not None and val > 0:
            return val, key
    return None, ""


def _build_put_suggestions(
    rows: List[Dict[str, Any]],
    *,
    min_yield_pct: float,
    min_buffer_pct: float,
    min_days: int,
    max_days: int,
    contract_size: int,
    limit: int,
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []

    for r in rows:
        opt_type = (r.get("option_type") or infer_option_type(r.get("ticker")) or "").upper()
        if opt_type and opt_type != "PUT":
            continue

        strike = _parse_float(r.get("strike"))
        days = _parse_float(r.get("dias_uteis"))
        spot = _parse_float(r.get("underlying_price"))
        if strike is None or strike <= 0 or spot is None or spot <= 0:
            continue
        dias_uteis = int(days) if days is not None else None
        if dias_uteis is None or dias_uteis < min_days or dias_uteis > max_days:
            continue

        premium, source = _premium_from_row(r)
        if premium is None or premium <= 0:
            continue

        yield_pct = premium / strike * 100.0
        if yield_pct < min_yield_pct:
            continue

        buffer_pct = (spot - strike) / spot * 100.0
        if buffer_pct < min_buffer_pct:
            continue

        annualized_yield = yield_pct * (252.0 / dias_uteis) if dias_uteis > 0 else None
        breakeven = strike - premium
        breakeven_buffer_pct = (spot - breakeven) / spot * 100.0 if spot > 0 else None

        suggestions.append(
            {
                "ticker": r.get("ticker"),
                "underlying": r.get("underlying"),
                "vencimento": r.get("vencimento"),
                "dias_uteis": dias_uteis,
                "strike": strike,
                "premium": premium,
                "premium_source": source,
                "premium_total": premium * contract_size if contract_size else None,
                "yield_pct": yield_pct,
                "annualized_yield_pct": annualized_yield,
                "buffer_pct": buffer_pct,
                "breakeven_price": breakeven,
                "breakeven_buffer_pct": breakeven_buffer_pct,
                "capital_required": strike * contract_size if contract_size else None,
                "best_bid": _parse_float(r.get("best_bid")),
                "best_ask": _parse_float(r.get("best_ask")),
                "ultimo": _parse_float(r.get("ultimo")),
                "preco_teorico": _parse_float(r.get("preco_teorico")),
                "vol_impl_perc": _parse_float(r.get("vol_impl_perc")),
                "iv_rank_180d": _parse_float(r.get("iv_rank_180d")),
                "score_total": _parse_float(r.get("score_total")),
                "underlying_price": spot,
                "underlying_price_date": r.get("underlying_price_date"),
            }
        )

    suggestions.sort(
        key=lambda s: (
            -(s.get("annualized_yield_pct") or -1.0),
            -(s.get("yield_pct") or -1.0),
            -(s.get("buffer_pct") or -1.0),
        )
    )
    if limit and limit > 0:
        suggestions = suggestions[:limit]
    return suggestions


def get_cash_covered_put_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    underlying = (args.get("underlying", "PETR4") or "PETR4").strip().upper()
    min_yield_pct = _get_float_arg(args, "min_yield_pct", 1.0)
    min_buffer_pct = _get_float_arg(args, "min_buffer_pct", 5.0)
    min_days = _get_int_arg(args, "min_days", 7)
    max_days = _get_int_arg(args, "max_days", 120)
    contract_size = max(_get_int_arg(args, "contract_size", 100), 1)
    limit = _get_int_arg(args, "limit", 50)

    rows = fetch_latest_underlying_options(underlying=underlying)
    suggestions = _build_put_suggestions(
        rows,
        min_yield_pct=min_yield_pct,
        min_buffer_pct=min_buffer_pct,
        min_days=min_days,
        max_days=max_days,
        contract_size=contract_size,
        limit=limit,
    )
    quote = fetch_latest_underlying_quote(underlying)

    return {
        "underlying": underlying,
        "underlying_quote": quote,
        "filters": {
            "min_yield_pct": min_yield_pct,
            "min_buffer_pct": min_buffer_pct,
            "min_days": min_days,
            "max_days": max_days,
            "contract_size": contract_size,
            "limit": limit,
        },
        "suggestions": suggestions,
    }


__all__ = ["get_cash_covered_put_context"]
