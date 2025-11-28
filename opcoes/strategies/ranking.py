from __future__ import annotations

from typing import Any, Dict, List, Mapping

from ..report import ReportData, generate_report


def _get_int_arg(args: Mapping[str, Any], name: str, default: int) -> int:
    try:
        raw = args.get(name, default)
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

        if "itm" in status or (delta_val is not None and delta_val >= 0.7):
            segments["carteira"].append(o)
            continue
        if "0-5% otm" in status or "colada" in status or "atm" in status:
            segments["alavancagem"].append(o)
            continue
        segments["aposta"].append(o)
    return segments


def get_ranking_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    min_score = _get_int_arg(args, "min_score", 8)
    limit = _get_int_arg(args, "limit", 30)
    recurring_days = _get_int_arg(args, "recurring_days", 30)
    recurring_limit = _get_int_arg(args, "recurring_limit", 15)
    underlying_filter = (args.get("underlying") or "").strip().upper()

    data: ReportData = generate_report(
        min_score=min_score,
        limit=limit,
        recurring_days=recurring_days,
        recurring_limit=recurring_limit,
    )

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

    alerts_map: Dict[int, List[str]] = {}
    for alert in data.alerts:
        pos = alert.get("position")
        if not pos:
            continue
        alerts_map[pos.get("id")] = alert.get("reasons", [])

    positions_real = [p for p in data.positions if not p.get("is_simulated")]
    positions_simulated = [p for p in data.positions if p.get("is_simulated")]
    totals_real = _compute_totals(positions_real)
    totals_simulated = _compute_totals(positions_simulated)
    all_opps = list(data.opportunities) + list(data.theoretical_opportunities)
    segments = _segment_opportunities(all_opps)

    return {
        "data": data,
        "min_score": min_score,
        "limit": limit,
        "recurring_days": recurring_days,
        "recurring_limit": recurring_limit,
        "underlying_filter": underlying_filter,
        "alerts_map": alerts_map,
        "totals_real": totals_real,
        "totals_simulated": totals_simulated,
        "positions_real": positions_real,
        "positions_simulated": positions_simulated,
        "segments": segments,
    }

