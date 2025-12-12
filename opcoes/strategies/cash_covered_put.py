from __future__ import annotations

import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..snapshot_repository import fetch_latest_underlying_options, fetch_latest_underlying_quote
from ..scraper.storage import _parse_ptbr_number
from ..utils import infer_option_type
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


def _calculate_portfolio_metrics(
    *,
    spot: Optional[float],
    contract_size: int,
    cash_mode: str,
    puts_real: List[Dict[str, Any]],
    puts_simulated: List[Dict[str, Any]],
) -> Dict[str, float]:
    # Caixa por modo
    mode = (cash_mode or "real").lower()
    if mode not in ("real", "simulated", "all"):
        mode = "real"
    total_balance = finance.get_balance(mode="all" if mode == "all" else mode)

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
    defaults = get_cash_put_settings()

    underlying = (args.get("underlying") or defaults.underlying).strip().upper()
    min_yield_pct = _get_float_arg(args, "min_yield_pct", defaults.min_yield_pct)
    min_buffer_pct = _get_float_arg(args, "min_buffer_pct", defaults.min_buffer_pct)
    min_days = _get_int_arg(args, "min_days", defaults.min_days)
    max_days = _get_int_arg(args, "max_days", defaults.max_days)
    contract_size = max(_get_int_arg(args, "contract_size", defaults.contract_size), 1)
    limit = _get_int_arg(args, "limit", defaults.limit)
    cash_mode = (args.get("cash_mode") or defaults.cash_mode).strip().lower()

    # Buscar posições abertas de Puts (Real vs Simulado)
    all_positions = list_positions(include_closed=False)
    puts_real = []
    puts_simulated = []
    
    # Agregador de prêmios simulados
    sim_premiums_agg: Dict[str, float] = {}

    for pos in all_positions:
        ticker = (pos.get("ticker") or "").upper()
        # Se trade_type for "stock", ignora. Se não tiver trade_type explícito, tenta inferir.
        # Mas `list_positions` retorna o que está no banco.
        # Vamos checar se é PUT.
        if infer_option_type(ticker) == "PUT":
            # Filtra pelo underlying se estiver definido na view
            pos_underlying = (pos.get("underlying") or "").upper()
            if underlying and pos_underlying != underlying:
                continue
            
            # Enrich position with Cash-Put specific metrics
            strike = pos.get("strike")
            entry = pos.get("entry_price") or 0.0
            qty = pos.get("qty") or 0
            fees = pos.get("fees") or 0.0
            spot = pos.get("underlying_price")

            stock_be = None
            dist_be = None
            projected_outcome = None
            collateral_yield_pct = None
            
            if strike and entry:
                stock_be = strike - entry
                try:
                    if strike > 0:
                        # Aproximação do retorno sobre o capital imobilizado:
                        # (prêmio / strike) * 100, equivalente a
                        # (prêmio_total / capital_imobilizado) * 100.
                        collateral_yield_pct = (entry / strike) * 100.0
                except Exception:
                    collateral_yield_pct = None

                if spot and spot > 0:
                    dist_be = (spot - stock_be) / spot * 100.0
                    # Resultado financeiro se exercido no preço atual (ou expirado)
                    # Se Spot < Strike: Exercido. Resultado = (Spot - Strike) + Premium
                    # Se Spot >= Strike: Expira pó. Resultado = Premium
                    if spot < strike:
                        outcome_per_share = (spot - strike) + entry
                    else:
                        outcome_per_share = entry
                    
                    projected_outcome = (outcome_per_share * qty) - fees

            pos["stock_breakeven"] = stock_be
            pos["dist_be_pct"] = dist_be
            pos["projected_outcome"] = projected_outcome
            pos["collateral_yield_pct"] = collateral_yield_pct

            if pos.get("is_simulated"):
                puts_simulated.append(pos)
                # Soma prêmios simulados por mês
                t_date = pos.get("trade_date")
                if t_date:
                    try:
                        # Parse YYYY-MM-DD
                        dt = datetime.date.fromisoformat(t_date)
                        m_key = dt.strftime("%Y-%m")
                        val = (entry * qty) - fees
                        sim_premiums_agg[m_key] = sim_premiums_agg.get(m_key, 0.0) + val
                    except ValueError:
                        pass
            else:
                puts_real.append(pos)
    
    # Formata lista de prêmios simulados (ordenada desc)
    simulated_monthly_premiums = []
    for m in sorted(sim_premiums_agg.keys(), reverse=True):
        simulated_monthly_premiums.append({"month": m, "total": sim_premiums_agg[m]})

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
    )
    monthly_premiums = finance.get_monthly_premiums()
    transactions = finance.get_transactions(limit=10)

    return {
        "underlying": underlying,
        "underlying_quote": quote,
        "puts_real": puts_real,
        "puts_simulated": puts_simulated,
        "simulated_monthly_premiums": simulated_monthly_premiums,
        "cash_mode": cash_mode,
        "filters": {
            "min_yield_pct": min_yield_pct,
            "min_buffer_pct": min_buffer_pct,
            "min_days": min_days,
            "max_days": max_days,
            "contract_size": contract_size,
            "limit": limit,
        },
        "suggestions": suggestions,
        "finance": finance_metrics,
        "monthly_premiums": monthly_premiums,
        "recent_transactions": transactions,
    }


__all__ = ["get_cash_covered_put_context"]
