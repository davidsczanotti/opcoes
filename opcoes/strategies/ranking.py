from __future__ import annotations

from typing import Any, Dict, List, Mapping

from ..report import ReportData, generate_report
from ..utils import infer_option_type
from ..settings import get_strategy_settings


def _get_int_arg(args: Mapping[str, Any], name: str, default: int) -> int:
    try:
        raw = args.get(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(raw)
    except (TypeError, ValueError):
        return default


def _compute_totals(positions: List[Dict]) -> Dict[str, Any]:
    total_purchase = 0.0
    total_current = 0.0
    total_pl = 0.0
    for pos in positions:
        qty = pos.get("qty") or 0
        open_qty = pos.get("open_qty") or 0
        entry = pos.get("entry_price") or 0.0
        last_price = pos.get("last_price")
        realized = pos.get("realized_pl") or 0.0
        pl = pos.get("pl")

        total_purchase += entry * qty
        if last_price is not None:
            total_current += last_price * open_qty
        total_current += realized
        if pl is not None:
            total_pl += pl

    total_pl_pct = (total_pl / total_purchase * 100.0) if total_purchase else None
    return {
        "total_purchase": total_purchase,
        "total_current": total_current,
        "total_pl": total_pl,
        "total_pl_pct": total_pl_pct,
    }


def _segment_opportunities(opps: List[Dict]) -> Dict[str, List[Dict]]:
    segments: Dict[str, List[Dict]] = {
        "carteira": [],
        "alavancagem": [],
        "aposta": [],
    }
    for o in opps:
        status = (o.get("Status_Moneyness") or "").lower()
        delta = o.get("delta")
        try:
            delta_val = float(delta) if delta is not None else None
        except (TypeError, ValueError):
            delta_val = None

        # Usa delta como critério principal quando disponível.
        if delta_val is not None:
            if delta_val >= 0.7:
                segments["carteira"].append(o)
                continue
            if 0.4 <= delta_val < 0.7:
                segments["alavancagem"].append(o)
                continue
            segments["aposta"].append(o)
            continue

        # Fallback para quando delta não estiver disponível.
        if "itm" in status:
            segments["carteira"].append(o)
            continue
        if "0-5% otm" in status or "colada" in status or "atm" in status:
            segments["alavancagem"].append(o)
            continue
        segments["aposta"].append(o)
    return segments


def _normalize_type(value: str | None, ticker: str | None = None) -> str:
    t = (value or "").strip().upper()
    if t in {"CALL", "PUT"}:
        return t
    inferred = infer_option_type(ticker or "")
    return inferred.upper() if inferred else ""


def _filter_by_type(items: List[Dict], opt_type: str | None) -> List[Dict]:
    if not opt_type:
        return items
    target = opt_type.strip().upper()
    filtered: List[Dict] = []
    for item in items:
        item_type = _normalize_type(item.get("option_type"), item.get("ticker"))
        if target == item_type:
            filtered.append(item)
    return filtered


def calculate_ranking_strategy(
    data: ReportData,
    min_score: int,
    limit: int,
    recurring_days: int,
    recurring_limit: int,
    underlying_filter: str,
    option_type_filter: str,
) -> Dict[str, Any]:
    """
    Pure strategy logic for Ranking.
    Filters opportunities, processes alerts, and calculates totals.
    """
    # Filtering Logic
    if option_type_filter:
        data.opportunities = _filter_by_type(data.opportunities, option_type_filter)
        data.theoretical_opportunities = _filter_by_type(data.theoretical_opportunities, option_type_filter)
        data.rational_opportunities = _filter_by_type(data.rational_opportunities, option_type_filter)
        data.lottery_opportunities = _filter_by_type(data.lottery_opportunities, option_type_filter)
        data.recurring_opportunities = _filter_by_type(data.recurring_opportunities, option_type_filter)

    if underlying_filter:
        data.opportunities = [
            o
            for o in data.opportunities
            if underlying_filter in (o.get("underlying") or "").upper()
            or underlying_filter in (o.get("ticker") or "").upper()
        ]
        data.recurring_opportunities = [
            o
            for o in data.recurring_opportunities
            if underlying_filter in (o.get("underlying") or "").upper()
            or underlying_filter in (o.get("ticker") or "").upper()
        ]

    # Alert Processing
    alerts_map: Dict[int, List[str]] = {}
    for alert in data.alerts:
        pos = alert.get("position")
        if not pos:
            continue
        alerts_map[pos.get("id")] = alert.get("reasons", [])

    # Positions & Totals
    positions_real = [p for p in data.positions if not p.get("is_simulated")]
    positions_simulated = [p for p in data.positions if p.get("is_simulated")]
    totals_real = _compute_totals(positions_real)
    totals_simulated = _compute_totals(positions_simulated)
    
    # Segmentation
    all_opps = list(data.opportunities) + list(data.theoretical_opportunities)
    segments = _segment_opportunities(all_opps)

    return {
        "data": data,
        "min_score": min_score,
        "limit": limit,
        "recurring_days": recurring_days,
        "recurring_limit": recurring_limit,
        "underlying_filter": underlying_filter,
        "option_type_filter": option_type_filter,
        "alerts_map": alerts_map,
        "totals_real": totals_real,
        "totals_simulated": totals_simulated,
        "positions_real": positions_real,
        "positions_simulated": positions_simulated,
        "segments": segments,
    }


def get_ranking_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    strat_settings = get_strategy_settings()
    
    min_score = _get_int_arg(args, "min_score", strat_settings.min_score)
    limit = _get_int_arg(args, "limit", strat_settings.limit_opportunities)
    recurring_days = _get_int_arg(args, "recurring_days", strat_settings.recurring_days)
    recurring_limit = _get_int_arg(args, "recurring_limit", 15)
    
    underlying_filter = (args.get("underlying") or "").strip().upper()
    option_type_filter = (args.get("option_type") or "").strip().upper()
    if option_type_filter in {"CALLS", "CALL"}:
        option_type_filter = "CALL"
    elif option_type_filter in {"PUTS", "PUT"}:
        option_type_filter = "PUT"
    else:
        option_type_filter = ""

    # IO / Data Fetching
    data: ReportData = generate_report(
        min_score=min_score,
        limit=limit,
        recurring_days=recurring_days,
        recurring_limit=recurring_limit,
    )

    # Pure Logic Delegation
    return calculate_ranking_strategy(
        data=data,
        min_score=min_score,
        limit=limit,
        recurring_days=recurring_days,
        recurring_limit=recurring_limit,
        underlying_filter=underlying_filter,
        option_type_filter=option_type_filter,
    )
