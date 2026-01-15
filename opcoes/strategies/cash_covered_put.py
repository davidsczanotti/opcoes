from __future__ import annotations

import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..snapshot_repository import fetch_latest_underlying_options, fetch_latest_underlying_quote
from ..utils import infer_option_type, parse_ptbr_number
from .. import finance
from ..portfolio import list_positions
from ..settings import get_cash_put_settings, update_cash_put_settings


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
        parsed = parse_ptbr_number(value)
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


def _is_put_option_position(pos: Mapping[str, Any]) -> bool:
    ticker = (pos.get("ticker") or "").strip().upper()
    if not ticker:
        return False
    if infer_option_type(ticker) != "PUT":
        return False
    underlying = (pos.get("underlying") or "").strip().upper()
    if underlying and ticker != underlying:
        return True
    return (pos.get("strategy_tag") or "").strip().lower() == "cash_put"


def _calculate_portfolio_metrics(
    *,
    spot: Optional[float],
    contract_size: int,
    cash_mode: str,
    puts_real: List[Dict[str, Any]],
    puts_simulated: List[Dict[str, Any]],
    total_balance: float,
) -> Dict[str, float]:
    # Caixa por modo
    mode = (cash_mode or "real").lower()
    if mode not in ("real", "simulated", "all"):
        mode = "real"
    total_balance = float(total_balance or 0.0)

    # Colateral travado somente no modo selecionado
    collateral_locked = 0.0
    source_positions: List[Dict[str, Any]]
    if mode == "simulated":
        source_positions = puts_simulated
    elif mode == "real":
        source_positions = puts_real
    else:
        source_positions = puts_real + puts_simulated

    for pos in source_positions:
        strike = pos.get("strike") or 0.0
        open_qty = pos.get("open_qty") or 0
        try:
            if strike and open_qty:
                collateral_locked += float(strike) * int(open_qty)
        except Exception:
            continue

    available_cash = total_balance - collateral_locked

    max_shares: Optional[int] = None
    max_lots: Optional[int] = None
    try:
        if spot is not None and spot > 0 and contract_size > 0 and available_cash > 0:
            max_shares = int(available_cash // spot)
            max_lots = int(available_cash // (spot * contract_size))
    except Exception:
        max_shares = None
        max_lots = None

    return {
        "total_cash": float(total_balance),
        "available_cash": float(available_cash),
        "collateral_locked": float(collateral_locked),
        "max_shares": max_shares,
        "max_lots": max_lots,
    }


def _aggregate_put_premiums_by_month(
    positions: List[Dict[str, Any]],
    *,
    is_simulated: bool,
) -> List[Dict[str, float]]:
    premiums_agg: Dict[str, float] = {}

    for pos in positions:
        if bool(pos.get("is_simulated")) != is_simulated:
            continue
        if not _is_put_option_position(pos):
            continue

        entry = pos.get("entry_price") or 0.0
        qty = pos.get("qty") or 0
        fees = pos.get("fees") or 0.0
        t_date = pos.get("trade_date")
        if not t_date or not entry or not qty:
            continue

        try:
            dt = datetime.date.fromisoformat(t_date)
        except Exception:
            continue

        trade_type = (pos.get("trade_type") or "swing").strip().lower()
        aliquota_opts = 0.20 if "day" in trade_type else 0.15
        premium_bruto = (entry * qty)
        base_premio = max(0.0, float(premium_bruto - fees))
        ir_premio = base_premio * aliquota_opts
        val = float(premium_bruto - fees - ir_premio)

        m_key = dt.strftime("%Y-%m")
        premiums_agg[m_key] = premiums_agg.get(m_key, 0.0) + val

    results = [{"month": m, "total": premiums_agg[m]} for m in sorted(premiums_agg.keys(), reverse=True)]
    return results[::-1]


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


def calculate_cash_covered_put_strategy(
    *,
    underlying: str,
    positions_open: List[Dict[str, Any]],
    options_rows: List[Dict[str, Any]],
    quote: Optional[Dict[str, Any]],
    min_yield_pct: float,
    min_buffer_pct: float,
    min_days: int,
    max_days: int,
    contract_size: int,
    limit: int,
    cash_mode: str,
    total_balance: float,
    buyback_target_pct: float,
) -> Dict[str, Any]:
    """
    Pure strategy logic for Cash Covered Put.
    Filters positions, derives metrics, and builds suggestions from provided data.
    """
    puts_real: List[Dict[str, Any]] = []
    puts_simulated: List[Dict[str, Any]] = []
    simulated_monthly_premiums_fallback = _aggregate_put_premiums_by_month(
        positions_open,
        is_simulated=True,
    )

    for pos in positions_open:
        ticker = (pos.get("ticker") or "").upper()
        if infer_option_type(ticker) != "PUT":
            continue
        pos_underlying = (pos.get("underlying") or "").upper()
        if underlying and pos_underlying != underlying:
            continue

        pos_data = dict(pos)
        strike = pos_data.get("strike")
        entry = pos_data.get("entry_price") or 0.0
        qty = pos_data.get("qty") or 0
        fees = pos_data.get("fees") or 0.0
        open_qty = pos_data.get("open_qty") or qty
        spot = pos_data.get("underlying_price")
        last_price = pos_data.get("last_price")

        stock_be = None
        dist_be = None
        projected_outcome = None
        collateral_yield_pct = None
        buyback_profit_per_share = None
        buyback_profit_total = None
        buyback_profit_pct = None
        buyback_target_hit = False

        if strike and entry:
            stock_be = strike - entry
            try:
                if strike > 0:
                    collateral_yield_pct = (entry / strike) * 100.0
            except Exception:
                collateral_yield_pct = None

            if spot and spot > 0:
                dist_be = (spot - stock_be) / spot * 100.0
                if spot < strike:
                    outcome_per_share = (spot - strike) + entry
                else:
                    outcome_per_share = entry
                projected_outcome = (outcome_per_share * qty) - fees

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

        pos_data["stock_breakeven"] = stock_be
        pos_data["dist_be_pct"] = dist_be
        pos_data["projected_outcome"] = projected_outcome
        pos_data["collateral_yield_pct"] = collateral_yield_pct
        pos_data["buyback_profit_per_share"] = buyback_profit_per_share
        pos_data["buyback_profit_total"] = buyback_profit_total
        pos_data["buyback_profit_pct"] = buyback_profit_pct
        pos_data["buyback_target_hit"] = buyback_target_hit

        if pos_data.get("is_simulated"):
            puts_simulated.append(pos_data)
        else:
            puts_real.append(pos_data)

    suggestions = _build_put_suggestions(
        options_rows,
        min_yield_pct=min_yield_pct,
        min_buffer_pct=min_buffer_pct,
        min_days=min_days,
        max_days=max_days,
        contract_size=contract_size,
        limit=limit,
    )

    spot_price: Optional[float] = None
    if quote and quote.get("price") is not None:
        try:
            spot_price = float(quote["price"])
        except (TypeError, ValueError):
            spot_price = None

    finance_metrics = _calculate_portfolio_metrics(
        spot=spot_price,
        contract_size=contract_size,
        cash_mode=cash_mode,
        puts_real=puts_real,
        puts_simulated=puts_simulated,
        total_balance=total_balance,
    )

    return {
        "underlying": underlying,
        "underlying_quote": quote,
        "puts_real": puts_real,
        "puts_simulated": puts_simulated,
        "cash_mode": cash_mode,
        "suggestions": suggestions,
        "finance": finance_metrics,
        "simulated_monthly_premiums_fallback": simulated_monthly_premiums_fallback,
        "buyback_target_pct": buyback_target_pct,
        "buyback_candidates_real": [p for p in puts_real if p.get("buyback_target_hit")],
        "buyback_candidates_simulated": [p for p in puts_simulated if p.get("buyback_target_hit")],
    }


def get_cash_covered_put_context(args: Mapping[str, Any]) -> Dict[str, Any]:
    defaults = get_cash_put_settings()

    underlying = (args.get("underlying") or defaults.underlying).strip().upper()
    min_yield_pct = _get_float_arg(args, "min_yield_pct", defaults.min_yield_pct)
    min_buffer_pct = _get_float_arg(args, "min_buffer_pct", defaults.min_buffer_pct)
    min_days = _get_int_arg(args, "min_days", defaults.min_days)
    max_days = _get_int_arg(args, "max_days", defaults.max_days)
    contract_size = max(_get_int_arg(args, "contract_size", defaults.contract_size), 1)
    limit = _get_int_arg(args, "limit", defaults.limit)
    cash_mode = (args.get("cash_mode") or defaults.cash_mode).strip().lower()
    buyback_target_pct = _get_float_arg(
        args,
        "buyback_target_pct",
        defaults.buyback_target_pct,
    )

    positions_open = list_positions(include_closed=False)
    rows = fetch_latest_underlying_options(underlying=underlying)
    quote = fetch_latest_underlying_quote(underlying)

    if args:
        update_cash_put_settings(
            underlying=underlying,
            min_yield_pct=min_yield_pct,
            min_buffer_pct=min_buffer_pct,
            min_days=min_days,
            max_days=max_days,
            contract_size=contract_size,
            limit=limit,
            cash_mode=cash_mode,
            buyback_target_pct=buyback_target_pct,
        )

    mode = (cash_mode or "real").lower()
    if mode not in ("real", "simulated", "all"):
        mode = "real"
    total_balance = finance.get_balance(mode="all" if mode == "all" else mode)

    ctx = calculate_cash_covered_put_strategy(
        underlying=underlying,
        positions_open=positions_open,
        options_rows=rows,
        quote=quote,
        min_yield_pct=min_yield_pct,
        min_buffer_pct=min_buffer_pct,
        min_days=min_days,
        max_days=max_days,
        contract_size=contract_size,
        limit=limit,
        cash_mode=mode,
        total_balance=total_balance,
        buyback_target_pct=buyback_target_pct,
    )
    puts_all = [pos for pos in positions_open if _is_put_option_position(pos)]
    puts_real_all = [pos for pos in puts_all if not bool(pos.get("is_simulated"))]
    puts_simulated_all = [pos for pos in puts_all if bool(pos.get("is_simulated"))]

    spot_price: Optional[float] = None
    if quote and quote.get("price") is not None:
        try:
            spot_price = float(quote["price"])
        except (TypeError, ValueError):
            spot_price = None

    finance_metrics = _calculate_portfolio_metrics(
        spot=spot_price,
        contract_size=contract_size,
        cash_mode=mode,
        puts_real=puts_real_all,
        puts_simulated=puts_simulated_all,
        total_balance=total_balance,
    )
    balance_real = finance.get_balance(mode="real")
    balance_simulated = finance.get_balance(mode="simulated")
    finance_breakdown = {
        "real": _calculate_portfolio_metrics(
            spot=spot_price,
            contract_size=contract_size,
            cash_mode="real",
            puts_real=puts_real_all,
            puts_simulated=puts_simulated_all,
            total_balance=balance_real,
        ),
        "simulated": _calculate_portfolio_metrics(
            spot=spot_price,
            contract_size=contract_size,
            cash_mode="simulated",
            puts_real=puts_real_all,
            puts_simulated=puts_simulated_all,
            total_balance=balance_simulated,
        ),
    }
    # Prêmios líquidos por mês (PREMIUM - DARF) via caixa (ledger).
    monthly_premiums = finance.get_monthly_premiums(include_darf=True, is_simulated=False)
    simulated_monthly_premiums = finance.get_monthly_premiums(include_darf=True, is_simulated=True) or ctx.get("simulated_monthly_premiums_fallback", [])
    transactions = finance.get_transactions(limit=10)

    return {
        **ctx,
        "finance": finance_metrics,
        "finance_breakdown": finance_breakdown,
        "simulated_monthly_premiums": simulated_monthly_premiums,
        "filters": {
            "min_yield_pct": min_yield_pct,
            "min_buffer_pct": min_buffer_pct,
            "min_days": min_days,
            "max_days": max_days,
            "contract_size": contract_size,
            "limit": limit,
            "buyback_target_pct": buyback_target_pct,
        },
        "monthly_premiums": monthly_premiums,
        "recent_transactions": transactions,
    }


__all__ = ["get_cash_covered_put_context"]
