from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..fundamentus import fetch_approved_ranking, fetch_signals, fetch_snapshot, latest_snapshot_date


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
        "ranking_total": ranking_total["rows"],
        "ranking_window": ranking_window["rows"],
        "ranking_window_days": window_days,
        "ranking_window_start": ranking_window["start_date"],
        "ranking_window_end": ranking_window["end_date"],
    }


__all__ = ["get_fundamentus_context"]
