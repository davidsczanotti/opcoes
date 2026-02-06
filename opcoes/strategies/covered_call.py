from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from .. import finance
from ..portfolio import list_positions
from ..snapshot_repository import fetch_latest_underlying_options, fetch_latest_underlying_quote
from ..settings import get_covered_call_settings, update_covered_call_settings
from ..utils import infer_option_type, parse_ptbr_number


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


def _is_short_strategy_position(pos: Dict, strategy_tag: str) -> bool:
    side = (pos.get("side") or "").strip().lower()
    if side == "long":
        return False
    if side == "short":
        return True
    return (pos.get("strategy_tag") or "").strip().lower() == strategy_tag


def _bova_coverage(positions: List[Dict], underlying: str) -> Tuple[Dict[str, Any], List[Dict], List[Dict]]:
    """Replica a lógica original de _bova_coverage de web.py.

    - Lotes do ativo-objeto: ticker == underlying
    - Calls do ativo-objeto: underlying == underlying e ticker != underlying
    """

    underlying = (underlying or "").strip().upper()
    if not underlying:
        return {}, [], []

    # Lotes do ativo-objeto (ticker == underlying)
    bova_lots: List[Dict] = [
        p
        for p in positions
        if (p.get("ticker") or "").strip().upper() == underlying
    ]
    # Calls do ativo-objeto (underlying == underlying, ticker != underlying, tipo CALL)
    call_positions: List[Dict] = [
        p
        for p in positions
        if (p.get("underlying") or "").strip().upper() == underlying
        and (p.get("ticker") or "").strip().upper() != underlying
        and infer_option_type(p.get("ticker")) == "CALL"
        and _is_short_strategy_position(p, "covered_call")
    ]

    # Ordena lotes e calls por data (FIFO)
    def _key_date(pos: Dict) -> str:
        return str(pos.get("trade_date") or "")

    bova_lots = sorted(bova_lots, key=_key_date)
    call_positions = sorted(call_positions, key=_key_date)

    # Inicializa cobertura por lote
    lot_infos: List[Dict] = []
    for p in bova_lots:
        open_qty = int(p.get("open_qty") or p.get("qty") or 0)
        lot_infos.append(
            {
                "id": p["id"],
                "trade_date": p.get("trade_date"),
                "qty_total": int(p.get("qty") or 0),
                "open_qty": open_qty,
                "covered": 0,
                "free": open_qty,
                "entry_price": float(p.get("entry_price") or 0.0),
            }
        )

    # Mapa auxiliar por id para lookup rápido durante a alocação.
    lot_by_id = {int(l["id"]): l for l in lot_infos if l.get("id") is not None}

    # Alocação FIFO: assumimos 1:1 entre opções e ações, respeitando parent_position_id quando existir.
    for call in call_positions:
        open_contracts = int(call.get("open_qty") or call.get("qty") or 0)
        need = open_contracts
        if need <= 0:
            continue
        parent_id = call.get("parent_position_id")
        lot = None
        if parent_id is not None:
            try:
                lot = lot_by_id.get(int(parent_id))
            except (TypeError, ValueError):
                lot = None
        if lot is None:
            # sem vínculo explícito: não aloca coberturas neste helper
            continue
        available = max(lot["open_qty"] - lot["covered"], 0)
        if available <= 0:
            continue
        alloc = min(available, need)
        lot["covered"] += alloc
        lot["free"] = max(lot["open_qty"] - lot["covered"], 0)

    # Resumo agregado
    shares_total = sum(l["open_qty"] for l in lot_infos)
    shares_covered = sum(l["covered"] for l in lot_infos)
    shares_free = sum(l["free"] for l in lot_infos)

    free_min = None
    free_max = None
    free_sum = 0.0
    if shares_free > 0:
        for l in lot_infos:
            f = l["free"]
            if f <= 0:
                continue
            price = l["entry_price"]
            free_sum += price * f
            if free_min is None or price < free_min:
                free_min = price
            if free_max is None or price > free_max:
                free_max = price
    free_avg = (free_sum / shares_free) if shares_free > 0 else None

    stock_summary: Dict[str, Any] = {
        "shares_total": int(shares_total),
        "shares_covered": int(shares_covered),
        "shares_free": int(shares_free),
        "free_min_price": free_min,
        "free_max_price": free_max,
        "free_avg_price": free_avg,
    }

    return stock_summary, lot_infos, call_positions


def _call_cashflow_summaries(
    call_positions: List[Dict],
    lots: List[Dict],
) -> List[Dict]:
    total_shares = sum(l.get("open_qty") or 0 for l in lots)
    avg_cost_global = None
    if total_shares > 0:
        cost_sum = sum((l.get("open_qty") or 0) * (l.get("entry_price") or 0.0) for l in lots)
        if cost_sum:
            avg_cost_global = cost_sum / total_shares

    lot_by_id = {int(l["id"]): l for l in lots if l.get("id") is not None}

    summaries: List[Dict] = []
    for pos in call_positions:
        qty = int(pos.get("open_qty") or pos.get("qty") or 0)
        if qty <= 0:
            continue
        trade_type = (pos.get("trade_type") or "swing").strip().lower()
        aliquota_acao = 0.15
        price_call = float(pos.get("entry_price") or 0.0)
        fees = float(pos.get("fees") or 0.0)
        strike = pos.get("strike")

        parent_id = pos.get("parent_position_id")
        local_avg_cost = avg_cost_global
        if parent_id is not None:
            try:
                lot = lot_by_id.get(int(parent_id))
            except (TypeError, ValueError):
                lot = None
            if lot is not None:
                local_avg_cost = float(lot.get("entry_price") or 0.0)

        premium_bruto = price_call * qty
        premium_amount = finance.calculate_option_premium(
            entry_price=price_call,
            qty=qty,
            fees=fees,
        )
        darf_premio = finance.calculate_darf_provision(
            premium_amount=premium_amount,
            trade_type=trade_type,
        )
        ir_premio = -darf_premio
        premio_liq = premium_amount + darf_premio

        pl_expira = premio_liq
        pl_expira_pct = None

        pl_exercicio = None
        avg_cost = local_avg_cost
        pl_exercicio_pct = None
        if local_avg_cost is not None and strike is not None:
            try:
                strike_val = float(strike)
            except (TypeError, ValueError):
                strike_val = None
            if strike_val is not None:
                ganho_papel = (strike_val - local_avg_cost) * qty
                ir_papel = max(0.0, ganho_papel) * aliquota_acao
                ganho_bruto_total = premium_bruto + ganho_papel
                pl_exercicio = ganho_bruto_total - fees - ir_premio - ir_papel

        capital = None
        if local_avg_cost is not None and qty > 0:
            capital = local_avg_cost * qty
        if capital and pl_expira is not None:
            pl_expira_pct = (pl_expira / capital) * 100.0
        if capital and pl_exercicio is not None:
            pl_exercicio_pct = (pl_exercicio / capital) * 100.0

        summaries.append(
            {
                "position_id": pos.get("id"),
                "lot_id": pos.get("parent_position_id"),
                "ticker": pos.get("ticker"),
                "qty": qty,
                "open_qty": qty,
                "strike": strike,
                "avg_cost": avg_cost,
                "premium_bruto": premium_bruto,
                "premio_liq": premio_liq,
                "pl_expira": pl_expira,
                "pl_exercicio": pl_exercicio,
                "pl_expira_pct": pl_expira_pct,
                "pl_exercicio_pct": pl_exercicio_pct,
            }
        )
    return summaries


def _parse_float(value) -> float | None:
    try:
        return float(parse_ptbr_number(value))
    except Exception:
        return None


def _apply_buyback_metrics(
    call_positions: List[Dict],
    *,
    buyback_target_pct: float,
) -> List[Dict]:
    output: List[Dict] = []
    for pos in call_positions:
        pos_data = dict(pos)
        entry = pos_data.get("entry_price") or 0.0
        last_price = pos_data.get("last_price")
        open_qty = pos_data.get("open_qty") or pos_data.get("qty") or 0

        buyback_profit_per_share = None
        buyback_profit_total = None
        buyback_profit_pct = None
        buyback_target_hit = False

        if entry and last_price is not None and open_qty:
            try:
                buyback_profit_per_share = float(entry) - float(last_price)
                buyback_profit_total = buyback_profit_per_share * int(open_qty)
                if entry > 0:
                    buyback_profit_pct = (buyback_profit_per_share / float(entry)) * 100.0
            except Exception:
                buyback_profit_per_share = None
                buyback_profit_total = None
                buyback_profit_pct = None

        if buyback_profit_pct is not None and buyback_target_pct is not None:
            buyback_target_hit = buyback_profit_pct >= float(buyback_target_pct)

        pos_data["buyback_profit_per_share"] = buyback_profit_per_share
        pos_data["buyback_profit_total"] = buyback_profit_total
        pos_data["buyback_profit_pct"] = buyback_profit_pct
        pos_data["buyback_target_hit"] = buyback_target_hit
        output.append(pos_data)
    return output


def calculate_covered_call_strategy(
    underlying: str,
    positions_open: List[Dict],
    options_rows: List[Dict],
    quote: Dict | None,
    min_extrinsic: float,
    min_days: int,
    max_days: int,
    min_dist_strike: float,
    buyback_target_pct: float,
) -> Dict[str, Any]:
    """
    Pure strategy logic for Covered Call.
    Calculates coverage, cashflows, and finds best suggestions based on provided data.
    """
    positions_real = [p for p in positions_open if not p.get("is_simulated")]
    positions_simulated = [p for p in positions_open if p.get("is_simulated")]

    stock_real, lots_real, covered_real = _bova_coverage(positions_real, underlying)
    stock_sim, lots_sim, covered_sim = _bova_coverage(positions_simulated, underlying)

    call_summary_real = _call_cashflow_summaries(covered_real, lots_real)
    call_summary_sim = _call_cashflow_summaries(covered_sim, lots_sim)
    covered_real = _apply_buyback_metrics(covered_real, buyback_target_pct=buyback_target_pct)
    covered_sim = _apply_buyback_metrics(covered_sim, buyback_target_pct=buyback_target_pct)

    # Reuses the existing helper, but we need to adapt _fetch_bova_suggestions logic
    # to accept rows instead of fetching them.
    # Since _fetch_bova_suggestions was tightly coupled with fetching, we'll inline/adapt its filtering logic here
    # or extract a pure filter function.
    # To keep it clean, let's look at _fetch_bova_suggestions again.
    # It fetches AND filters. We should split it.
    
    # Let's do the filtering here directly on options_rows
    suggestions: List[Dict] = []
    for r in options_rows:
        opt_type = (r.get("option_type") or infer_option_type(r.get("ticker")) or "").upper()
        if opt_type and opt_type != "CALL":
            continue
        dias_uteis = _parse_float(r["dias_uteis"])
        if dias_uteis is None:
            continue
        if dias_uteis < min_days or dias_uteis > max_days:
            continue
        extrinsic = _parse_float(r["extrinsic_pct_spot"])
        if extrinsic is None or extrinsic < min_extrinsic:
            continue
        dist = _parse_float(r["dist_perc_strike"])
        if dist is None or dist < min_dist_strike:
            continue
        
        suggestion = {
            "ticker": r["ticker"],
            "underlying": r["underlying"],
            "vencimento": r["vencimento"],
            "dias_uteis": int(dias_uteis),
            "strike": _parse_float(r["strike"]),
            "dist_perc_strike": _parse_float(r["dist_perc_strike"]),
            "underlying_price": _parse_float(r["underlying_price"]),
            "extrinsic_pct_spot": extrinsic,
            "pct_2x": _parse_float(r["pct_2x"]),
            "score_total": _parse_float(r["score_total"]),
        }
        suggestions.append(suggestion)

    suggestions.sort(
        key=lambda s: (
            s.get("dias_uteis") or 0,
            -(s.get("extrinsic_pct_spot") or 0.0),
        )
    )

    best_idx = None
    best_score = None
    for idx, s in enumerate(suggestions):
        extr = s.get("extrinsic_pct_spot")
        dias = s.get("dias_uteis")
        if extr is None or dias is None or dias <= 0:
            continue
        score = extr / dias
        if best_score is None or score > best_score:
            best_score = score
            best_idx = idx
    if best_idx is not None:
        suggestions[best_idx]["best_flag"] = True
        suggestions[best_idx]["best_yield_per_day"] = best_score

    return {
        "underlying": underlying,
        "underlying_quote": quote,
        "stock_real": stock_real,
        "stock_sim": stock_sim,
        "covered_real": covered_real,
        "covered_sim": covered_sim,
        "lots_real": lots_real,
        "lots_sim": lots_sim,
        "call_summary_real": call_summary_real,
        "call_summary_sim": call_summary_sim,
        "suggestions": suggestions,
        "buyback_target_pct": buyback_target_pct,
        "buyback_candidates_real": [p for p in covered_real if p.get("buyback_target_hit")],
        "buyback_candidates_simulated": [p for p in covered_sim if p.get("buyback_target_hit")],
    }


def get_covered_call_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    defaults = get_covered_call_settings()

    underlying = (args.get("underlying") or defaults.underlying).strip().upper()
    min_extrinsic = _get_float_arg(args, "min_extrinsic", defaults.min_extrinsic)
    min_days = _get_int_arg(args, "min_days", defaults.min_days)
    max_days = _get_int_arg(args, "max_days", defaults.max_days)
    min_dist_strike = _get_float_arg(args, "min_dist_strike", defaults.min_dist_strike)

    # IO / Data Fetching
    positions_open = list_positions(include_closed=False)
    # We fetch rows here instead of inside the helper
    options_rows = fetch_latest_underlying_options(underlying=underlying)
    quote = fetch_latest_underlying_quote(underlying)

    if args:
        update_covered_call_settings(
            underlying=underlying,
            min_extrinsic=min_extrinsic,
            min_days=min_days,
            max_days=max_days,
            min_dist_strike=min_dist_strike,
            buyback_target_pct=defaults.buyback_target_pct,
        )

    ctx = calculate_covered_call_strategy(
        underlying=underlying,
        positions_open=positions_open,
        options_rows=options_rows,
        quote=quote,
        min_extrinsic=min_extrinsic,
        min_days=min_days,
        max_days=max_days,
        min_dist_strike=min_dist_strike,
        buyback_target_pct=defaults.buyback_target_pct,
    )

    ctx["filters"] = {
        "min_extrinsic": min_extrinsic,
        "min_days": min_days,
        "max_days": max_days,
        "min_dist_strike": min_dist_strike,
    }
    return ctx
