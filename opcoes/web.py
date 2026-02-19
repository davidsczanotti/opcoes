from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Optional

from flask import Flask, redirect, render_template, request, url_for

from .config import get_db_path
from .portfolio import add_position, delete_position, list_positions, update_position, close_position, get_position
from .utils import infer_option_type, parse_ptbr_number
from .settings import (
    CashCoveredPutSettings,
    CoveredCallSettings,
    FeeSettings,
    StrategySettings,
    FundamentusSettings,
    get_cash_put_settings,
    get_covered_call_settings,
    get_fee_settings,
    get_strategy_settings,
    get_fundamentus_settings,
    update_cash_put_settings,
    update_covered_call_settings,
    update_fee_settings,
    update_strategy_settings,
    update_fundamentus_settings,
)
from .strategies import get_cash_covered_put_context, get_covered_call_context, get_fundamentus_context, get_ranking_context
from .flows import FlowError, assign_put, callaway
from . import finance, darf


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    @app.route("/")
    def index() -> str:
        ctx = get_ranking_context(request.args)
        return render_template("index.html", **ctx)

    @app.route("/covered-call")
    def covered_call() -> str:
        ctx = get_covered_call_context(request.args)
        return render_template("covered_call.html", **ctx)

    @app.route("/cash-covered-put")
    def cash_covered_put() -> str:
        ctx = get_cash_covered_put_context(request.args)
        return render_template("cash_covered_put.html", **ctx)

    @app.route("/fundamentus")
    def fundamentus() -> str:
        ctx = get_fundamentus_context(request.args)
        return render_template("fundamentus.html", **ctx)

    @app.route("/darf")
    def darf_view() -> str:
        mode = (request.args.get("mode") or "real").strip().lower()
        is_simulated = mode == "simulated"
        selected_period = (request.args.get("period") or "").strip()

        provisions = darf.get_monthly_darf_provisions(is_simulated=is_simulated, limit=36)
        records = darf.list_months(is_simulated=is_simulated, limit=36)
        record_by_period = {r.period: r for r in records}

        periods = sorted(set(provisions.keys()) | set(record_by_period.keys()), reverse=True)
        if not selected_period:
            if periods:
                selected_period = periods[0]
            else:
                selected_period = datetime.date.today().strftime("%Y-%m")

        summaries = []
        for p in periods:
            prov = float(provisions.get(p, 0.0) or 0.0)
            rec = record_by_period.get(p)
            try:
                due_date = rec.due_date if rec else darf.last_business_day_next_month(p)
            except Exception:
                due_date = "-"

            generated = rec.amount if rec else None
            paid_date = rec.paid_date if rec else None
            paid_amount = rec.paid_amount if rec else None

            status = "Sem movimento"
            if prov > 0 and not rec:
                status = "Pendente"
            if rec and not rec.paid_date:
                status = "Gerado"
            if rec and rec.paid_date:
                status = "Pago"

            diff = None
            if prov > 0 and rec:
                diff = prov - float(rec.amount or 0.0)

            summaries.append(
                {
                    "period": p,
                    "provisioned": prov,
                    "generated": generated,
                    "due_date": due_date,
                    "paid_date": paid_date,
                    "paid_amount": paid_amount,
                    "status": status,
                    "diff": diff,
                }
            )

        selected_record = None
        try:
            selected_record = darf.get_month(period=selected_period, is_simulated=is_simulated)
        except Exception:
            selected_record = None

        provision_entries = []
        try:
            provision_entries = darf.list_provision_entries(period=selected_period, is_simulated=is_simulated)
        except Exception:
            provision_entries = []

        return render_template(
            "darf.html",
            mode=mode,
            is_simulated=is_simulated,
            selected_period=selected_period,
            periods=summaries,
            provision_entries=provision_entries,
            selected_record=selected_record,
        )

    @app.post("/darf/generate")
    def darf_generate():
        form = request.form
        period = (form.get("period") or "").strip()
        is_simulated = form.get("is_simulated") == "1"
        mode = "simulated" if is_simulated else "real"

        try:
            entries = darf.list_provision_entries(period=period, is_simulated=is_simulated)
            provisioned = max(0.0, -sum(float(e.get("amount") or 0.0) for e in entries))
            due_date = darf.last_business_day_next_month(period)
        except Exception:
            return redirect(url_for("darf_view", mode=mode))

        if provisioned > 0:
            darf.upsert_month(
                period=period,
                due_date=due_date,
                amount=provisioned,
                is_simulated=is_simulated,
            )

        return redirect(url_for("darf_view", mode=mode, period=period))

    @app.post("/darf/pay")
    def darf_pay():
        form = request.form
        period = (form.get("period") or "").strip()
        is_simulated = form.get("is_simulated") == "1"
        mode = "simulated" if is_simulated else "real"
        paid_date = _parse_form_date(form.get("paid_date")) or datetime.date.today().isoformat()
        paid_amount = _parse_form_float(form.get("paid_amount")) if form.get("paid_amount") else None

        try:
            rec = darf.get_month(period=period, is_simulated=is_simulated)
            if not rec:
                entries = darf.list_provision_entries(period=period, is_simulated=is_simulated)
                provisioned = max(0.0, -sum(float(e.get("amount") or 0.0) for e in entries))
                if provisioned <= 0:
                    return redirect(url_for("darf_view", mode=mode, period=period))
                due_date = darf.last_business_day_next_month(period)
                darf.upsert_month(period=period, due_date=due_date, amount=provisioned, is_simulated=is_simulated)
            darf.mark_paid(
                period=period,
                paid_date=paid_date,
                paid_amount=paid_amount,
                is_simulated=is_simulated,
            )
        except Exception:
            return redirect(url_for("darf_view", mode=mode, period=period))

        return redirect(url_for("darf_view", mode=mode, period=period))

    @app.post("/finance/add")
    def finance_add():
        form = request.form
        amount = _parse_form_float(form.get("amount"))
        type_str = form.get("type")
        desc = form.get("description") or "Movimentação manual"
        date = form.get("date") or datetime.date.today().isoformat()
        is_simulated = form.get("is_simulated") == "1"
        
        # Valid transaction type
        try:
            tx_type = finance.TransactionType(type_str)
        except ValueError:
            return redirect(url_for("cash_covered_put")) # Or error page

        # Negative amount for withdrawal
        if tx_type == finance.TransactionType.WITHDRAWAL and amount > 0:
            amount = -amount
            
        finance.add_transaction(
            date=date,
            type=tx_type,
            amount=amount,
            description=desc,
            is_simulated=is_simulated,
        )
        return redirect(url_for("cash_covered_put"))

    @app.post("/finance/assign")
    def finance_assign():
        form = request.form
        position_id = int(form.get("position_id"))
        strike = _parse_form_float(form.get("strike"))
        qty = int(form.get("qty"))
        date = form.get("date") or datetime.date.today().isoformat()
        assign_put(position_id=position_id, strike=strike, qty=qty, date=date)
        return redirect(url_for("cash_covered_put"))

    @app.post("/finance/callaway")
    def finance_callaway():
        form = request.form
        position_id = int(form.get("position_id"))
        date = _parse_form_date(form.get("date")) or datetime.date.today().isoformat()
        try:
            underlying = callaway(position_id=position_id, date=date)
        except FlowError as exc:
            if exc.underlying:
                return redirect(url_for("covered_call", underlying=exc.underlying))
            return redirect(url_for("covered_call"))

        if underlying:
            return redirect(url_for("covered_call", underlying=underlying))
        return redirect(url_for("covered_call"))

    @app.post("/finance/expire")
    def finance_expire():
        form = request.form
        try:
            position_id = int(form.get("position_id"))
        except (TypeError, ValueError):
            return redirect(url_for("positions"))

        date = _parse_form_date(form.get("date")) or datetime.date.today().isoformat()

        pos = get_position(position_id)
        if not pos:
            return redirect(url_for("positions"))

        ticker = pos.get("ticker")
        underlying = (pos.get("underlying") or "").strip().upper()
        opt_type = infer_option_type(ticker)

        if not underlying or (ticker and (str(ticker).strip().upper() == underlying)):
            return redirect(url_for("positions"))

        if (pos.get("status") or "").strip().lower() != "open":
            if opt_type == "PUT":
                return redirect(url_for("cash_covered_put", underlying=underlying) if underlying else url_for("cash_covered_put"))
            if opt_type == "CALL":
                return redirect(url_for("covered_call", underlying=underlying) if underlying else url_for("covered_call"))
            return redirect(url_for("positions"))

        if opt_type not in {"PUT", "CALL"}:
            return redirect(url_for("positions"))

        close_position(
            position_id=position_id,
            exit_date=date,
            exit_price=0.0,
            exit_reason="Expiração",
        )

        if opt_type == "PUT":
            return redirect(url_for("cash_covered_put", underlying=underlying) if underlying else url_for("cash_covered_put"))
        return redirect(url_for("covered_call", underlying=underlying) if underlying else url_for("covered_call"))

    @app.post("/finance/update/<int:tx_id>")
    def finance_update(tx_id: int):
        form = request.form
        date = form.get("date") or None
        type_str = form.get("type") or None
        desc = form.get("description") or None
        amount = _parse_form_float(form.get("amount"))
        is_simulated = form.get("is_simulated") == "1"

        tx_type = None
        if type_str:
            try:
                tx_type = finance.TransactionType(type_str)
            except ValueError:
                tx_type = None

        # mesma regra: retirada em valor positivo vira negativo
        if tx_type == finance.TransactionType.WITHDRAWAL and amount > 0:
            amount = -amount

        finance.update_transaction(
            tx_id,
            date=date,
            type=tx_type,
            amount=amount,
            description=desc,
            is_simulated=is_simulated,
        )
        return redirect(url_for("cash_covered_put"))

    @app.post("/finance/delete/<int:tx_id>")
    def finance_delete(tx_id: int):
        finance.delete_transaction(tx_id)
        return redirect(url_for("cash_covered_put"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_view() -> str:
        if request.method == "POST":
            form = request.form
            
            # Fee Settings
            equity_fixed = _parse_form_float(form.get("equity_fixed"))
            equity_percent = _parse_form_float(form.get("equity_percent"))
            option_fixed = _parse_form_float(form.get("option_fixed"))
            option_percent_notional = _parse_form_float(form.get("option_percent_notional"))
            update_fee_settings(
                equity_fixed=equity_fixed,
                equity_percent=equity_percent,
                option_fixed=option_fixed,
                option_percent_notional=option_percent_notional,
            )
            
            # Strategy Settings
            min_score = int(form.get("strat_min_score", 8))
            limit_opp = int(form.get("strat_limit_opportunities", 30))
            recur_days = int(form.get("strat_recurring_days", 30))
            update_strategy_settings(
                min_score=min_score,
                limit_opportunities=limit_opp,
                recurring_days=recur_days,
            )

            fund_cfg = get_fundamentus_settings()

            def _form_float_or_default(name: str, current: float) -> float:
                raw = form.get(name)
                if raw is None or not str(raw).strip():
                    return current
                return _parse_form_float(raw)

            update_fundamentus_settings(
                target_yield_pct=_form_float_or_default("fund_target_yield_pct", fund_cfg.target_yield_pct),
                put_distance_limit_pct=_form_float_or_default(
                    "fund_put_distance_limit_pct",
                    fund_cfg.put_distance_limit_pct,
                ),
                put_min_premium_pct=_form_float_or_default(
                    "fund_put_min_premium_pct",
                    fund_cfg.put_min_premium_pct,
                ),
                put_target_monthly_yield_pct=_form_float_or_default(
                    "fund_put_target_monthly_yield_pct",
                    fund_cfg.put_target_monthly_yield_pct,
                ),
                put_min_score=_form_float_or_default("fund_put_min_score", fund_cfg.put_min_score),
            )

            cash_put_cfg = get_cash_put_settings()
            buyback_raw = form.get("cash_put_buyback_target_pct")
            if buyback_raw and buyback_raw.strip():
                buyback_target_pct = _parse_form_float(buyback_raw)
            else:
                buyback_target_pct = cash_put_cfg.buyback_target_pct
            update_cash_put_settings(
                underlying=cash_put_cfg.underlying,
                min_yield_pct=cash_put_cfg.min_yield_pct,
                min_buffer_pct=cash_put_cfg.min_buffer_pct,
                min_days=cash_put_cfg.min_days,
                max_days=cash_put_cfg.max_days,
                contract_size=cash_put_cfg.contract_size,
                limit=cash_put_cfg.limit,
                cash_mode=cash_put_cfg.cash_mode,
                buyback_target_pct=buyback_target_pct,
            )

            ccall_cfg = get_covered_call_settings()
            ccall_buyback_raw = form.get("ccall_buyback_target_pct")
            if ccall_buyback_raw and ccall_buyback_raw.strip():
                ccall_buyback_target_pct = _parse_form_float(ccall_buyback_raw)
            else:
                ccall_buyback_target_pct = ccall_cfg.buyback_target_pct
            update_covered_call_settings(
                underlying=ccall_cfg.underlying,
                min_extrinsic=ccall_cfg.min_extrinsic,
                min_days=ccall_cfg.min_days,
                max_days=ccall_cfg.max_days,
                min_dist_strike=ccall_cfg.min_dist_strike,
                buyback_target_pct=ccall_buyback_target_pct,
            )

            return redirect(url_for("settings_view"))

        fees_cfg: FeeSettings = get_fee_settings()
        strat_cfg: StrategySettings = get_strategy_settings()
        fund_cfg: FundamentusSettings = get_fundamentus_settings()
        ccall_cfg: CoveredCallSettings = get_covered_call_settings()
        cash_put_cfg: CashCoveredPutSettings = get_cash_put_settings()
        return render_template(
            "settings.html",
            fees=fees_cfg,
            strat=strat_cfg,
            fund=fund_cfg,
            covered_call=ccall_cfg,
            cash_put=cash_put_cfg,
        )
    @app.route("/positions")
    def positions() -> str:
        ticker_contains = (request.args.get("ticker") or "").strip().upper()
        underlying_contains = (request.args.get("underlying") or "").strip().upper()
        strategy_tag = (request.args.get("strategy_tag") or "").strip()
        trade_type = (request.args.get("trade_type") or "").strip().lower()
        status = (request.args.get("status") or "all").strip().lower()
        is_simulated_raw = (request.args.get("is_simulated") or "").strip()

        include_closed = True
        only_closed = False
        if status == "open":
            include_closed = False
        elif status == "closed":
            only_closed = True

        is_simulated = None
        if is_simulated_raw in {"0", "1"}:
            is_simulated = is_simulated_raw == "1"

        next_url = request.full_path
        if next_url.endswith("?"):
            next_url = request.path

        positions = list_positions(
            include_closed=include_closed,
            only_closed=only_closed,
            ticker_contains=ticker_contains or None,
            underlying_contains=underlying_contains or None,
            strategy_tag=strategy_tag or None,
            trade_type=trade_type or None,
            is_simulated=is_simulated,
        )
        position_ids = [int(p["id"]) for p in positions if p.get("id") is not None]
        premium_ids = finance.get_premium_position_ids(position_ids)
        for pos in positions:
            pos_id = pos.get("id")
            pos["premium_recorded"] = bool(pos_id and int(pos_id) in premium_ids)
        return render_template(
            "positions.html",
            positions=positions,
            filter_ticker=ticker_contains,
            filter_underlying=underlying_contains,
            filter_strategy_tag=strategy_tag,
            filter_trade_type=trade_type,
            filter_status=status,
            filter_is_simulated=is_simulated_raw,
            next_url=next_url,
        )

    @app.route("/audit")
    def audit_view() -> str:
        mode = (request.args.get("mode") or "real").strip().lower()
        include_closed = (request.args.get("include_closed") or "1") == "1"

        is_simulated: Optional[bool]
        if mode == "simulated":
            is_simulated = True
        elif mode == "all":
            is_simulated = None
        else:
            mode = "real"
            is_simulated = False

        positions_all = list_positions(include_closed=True)
        if is_simulated is not None:
            positions_all = [p for p in positions_all if bool(p.get("is_simulated")) == is_simulated]

        ledger_sums = finance.get_ledger_sums_by_position(
            types=[finance.TransactionType.PREMIUM, finance.TransactionType.DARF, finance.TransactionType.BUY],
            is_simulated=is_simulated,
        )

        def _money_diff(actual: Optional[float], expected: Optional[float]) -> Optional[float]:
            if actual is None and expected is None:
                return None
            return round(float(actual or 0.0) - float(expected or 0.0), 2)

        rows = []
        totals = {
            "expected_premium": 0.0,
            "expected_darf": 0.0,
            "expected_buyback": 0.0,
            "actual_premium": 0.0,
            "actual_darf": 0.0,
            "actual_buyback": 0.0,
            "expected_net": 0.0,
            "actual_net": 0.0,
            "expected_cash_net": 0.0,
            "actual_cash_net": 0.0,
        }

        for pos in positions_all:
            if not include_closed and (pos.get("status") or "").strip().lower() == "closed":
                continue

            pid = int(pos.get("id") or 0)
            ticker = (pos.get("ticker") or "").strip()
            underlying = (pos.get("underlying") or "").strip()
            is_option = _is_option_ticker(ticker)
            side = (pos.get("side") or "").strip().lower()
            trade_type = (pos.get("trade_type") or "swing").strip().lower()
            entry_price = float(pos.get("entry_price") or 0.0)
            qty = int(pos.get("qty") or 0)
            fees = float(pos.get("fees") or 0.0)
            partial_qty = int(pos.get("partial_qty") or 0)
            close_qty = max(qty - partial_qty, 0)
            status_norm = (pos.get("status") or "").strip().lower()
            exit_price_raw = pos.get("exit_price")
            exit_price = float(exit_price_raw) if exit_price_raw is not None else None

            expected_premium = None
            expected_darf = None
            expected_buyback = None
            if is_option and side == "short":
                expected_premium = finance.calculate_option_premium(
                    entry_price=entry_price,
                    qty=qty,
                    fees=fees,
                )
                expected_darf = finance.calculate_darf_provision(
                    premium_amount=expected_premium,
                    trade_type=trade_type,
                )
                if status_norm == "closed" and exit_price is not None and close_qty > 0:
                    expected_buyback = -round(float(exit_price) * int(close_qty), 2)

            actual_premium = ledger_sums.get(pid, {}).get(finance.TransactionType.PREMIUM.value)
            actual_darf = ledger_sums.get(pid, {}).get(finance.TransactionType.DARF.value)
            actual_buyback = ledger_sums.get(pid, {}).get(finance.TransactionType.BUY.value)

            if (
                expected_premium is None
                and actual_premium is None
                and actual_darf is None
                and expected_buyback is None
                and actual_buyback is None
            ):
                continue

            expected_net = None
            actual_net = None
            expected_cash_net = None
            actual_cash_net = None
            if expected_premium is not None or expected_darf is not None:
                expected_net = float(expected_premium or 0.0) + float(expected_darf or 0.0)
                expected_cash_net = expected_net + float(expected_buyback or 0.0)
            if actual_premium is not None or actual_darf is not None:
                actual_net = float(actual_premium or 0.0) + float(actual_darf or 0.0)
                actual_cash_net = actual_net + float(actual_buyback or 0.0)

            rows.append(
                {
                    "id": pid,
                    "ticker": ticker,
                    "underlying": underlying,
                    "side": side,
                    "status": pos.get("status"),
                    "qty": qty,
                    "entry_price": entry_price,
                    "fees": fees,
                    "trade_type": trade_type,
                    "expected_premium": expected_premium,
                    "expected_darf": expected_darf,
                    "expected_buyback": expected_buyback,
                    "actual_premium": actual_premium,
                    "actual_darf": actual_darf,
                    "actual_buyback": actual_buyback,
                    "diff_premium": _money_diff(actual_premium, expected_premium),
                    "diff_darf": _money_diff(actual_darf, expected_darf),
                    "diff_buyback": _money_diff(actual_buyback, expected_buyback),
                    "expected_net": expected_net,
                    "actual_net": actual_net,
                    "diff_net": _money_diff(actual_net, expected_net),
                    "expected_cash_net": expected_cash_net,
                    "actual_cash_net": actual_cash_net,
                    "diff_cash_net": _money_diff(actual_cash_net, expected_cash_net),
                }
            )

            if expected_premium is not None:
                totals["expected_premium"] += float(expected_premium or 0.0)
            if expected_darf is not None:
                totals["expected_darf"] += float(expected_darf or 0.0)
            if expected_buyback is not None:
                totals["expected_buyback"] += float(expected_buyback or 0.0)
            if actual_premium is not None:
                totals["actual_premium"] += float(actual_premium or 0.0)
            if actual_darf is not None:
                totals["actual_darf"] += float(actual_darf or 0.0)
            if actual_buyback is not None:
                totals["actual_buyback"] += float(actual_buyback or 0.0)

        totals["expected_net"] = totals["expected_premium"] + totals["expected_darf"]
        totals["actual_net"] = totals["actual_premium"] + totals["actual_darf"]
        totals["expected_cash_net"] = totals["expected_net"] + totals["expected_buyback"]
        totals["actual_cash_net"] = totals["actual_net"] + totals["actual_buyback"]

        position_ids = {int(p.get("id") or 0) for p in positions_all}
        orphan_rows = [
            {
                "id": pid,
                "actual_premium": sums.get(finance.TransactionType.PREMIUM.value),
                "actual_darf": sums.get(finance.TransactionType.DARF.value),
                "actual_buyback": sums.get(finance.TransactionType.BUY.value),
                "actual_net": (sums.get(finance.TransactionType.PREMIUM.value) or 0.0)
                + (sums.get(finance.TransactionType.DARF.value) or 0.0),
                "actual_cash_net": (sums.get(finance.TransactionType.PREMIUM.value) or 0.0)
                + (sums.get(finance.TransactionType.DARF.value) or 0.0)
                + (sums.get(finance.TransactionType.BUY.value) or 0.0),
            }
            for pid, sums in ledger_sums.items()
            if pid not in position_ids
            and (
                sums.get(finance.TransactionType.PREMIUM.value) is not None
                or sums.get(finance.TransactionType.DARF.value) is not None
                or sums.get(finance.TransactionType.BUY.value) is not None
            )
        ]

        return render_template(
            "audit.html",
            rows=rows,
            totals=totals,
            mode=mode,
            include_closed=include_closed,
            orphan_rows=orphan_rows,
        )

    @app.post("/positions/add")
    def add_position_view():
        form = request.form
        underlying = form.get("underlying", "").strip()
        ticker = form.get("ticker", "").strip()
        is_simulated = form.get("is_simulated") == "1"
        if not underlying:
            underlying = _lookup_underlying_from_snapshot(ticker) or ""
        qty = int(form.get("qty", 0) or 0)
        entry_price = _parse_form_float(form.get("entry_price"))
        fees_input = form.get("fees")
        if fees_input:
            fees = _parse_form_float(fees_input)
        else:
            fees = _auto_fees(ticker=ticker, underlying=underlying or ticker, qty=qty, entry_price=entry_price)
        parent_raw = form.get("parent_position_id")
        parent_id = int(parent_raw) if parent_raw and parent_raw.strip() else None
        side_raw = (form.get("side") or "").strip()
        if not side_raw:
            # Se marcou prêmio, assume venda (short). Caso contrário, default long.
            side_raw = "short" if form.get("record_premium") == "1" else "long"

        pos_id = add_position(
            ticker=ticker,
            underlying=underlying,
            trade_date=form.get("trade_date", ""),
            qty=qty,
            entry_price=entry_price,
            fees=fees,
            trade_type=form.get("trade_type", "swing"),
            side=side_raw,
            irrf=float(form["irrf"]) if form.get("irrf") else None,
            notes=form.get("notes") or None,
            is_simulated=is_simulated,
            parent_position_id=parent_id,
            strategy_tag=form.get("strategy_tag") or None,
        )

        # Registro opcional: prêmio no caixa (venda) + provisão DARF (saldo limpo).
        if entry_price > 0 and qty > 0 and form.get("record_premium") == "1":
            t = (ticker or "").strip().upper()
            is_option = _is_option_ticker(t)

            if is_option:
                total_premium = finance.calculate_option_premium(
                    entry_price=entry_price,
                    qty=qty,
                    fees=fees,
                )
                finance.add_transaction(
                    date=form.get("trade_date", ""),
                    type=finance.TransactionType.PREMIUM,
                    amount=total_premium,
                    description=f"Prêmio {ticker} ({qty}x)",
                    position_id=pos_id,
                    is_simulated=is_simulated,
                )

                if form.get("reserve_darf") == "1":
                    trade_type = (form.get("trade_type") or "swing").strip().lower()
                    darf_amount = finance.calculate_darf_provision(
                        premium_amount=total_premium,
                        trade_type=trade_type,
                    )
                    if darf_amount != 0.0:
                        aliquota_opts = finance.option_tax_rate(trade_type)
                        finance.add_transaction(
                            date=form.get("trade_date", ""),
                            type=finance.TransactionType.DARF,
                            amount=darf_amount,
                            description=f"Provisão DARF {ticker} ({int(aliquota_opts*100)}%)",
                            position_id=pos_id,
                            is_simulated=is_simulated,
                        )

        return redirect(_safe_next_url(form.get("next")) or url_for("positions"))

    @app.post("/positions/register-premium/<int:position_id>")
    def register_position_premium(position_id: int):
        next_url = _safe_next_url(request.form.get("next")) or url_for("positions")
        pos = get_position(position_id)
        if not pos:
            return redirect(next_url)

        ticker = (pos.get("ticker") or "").strip()
        underlying = (pos.get("underlying") or "").strip()
        if not underlying:
            underlying = _lookup_underlying_from_snapshot(ticker) or ""
        if not ticker or not _is_option_ticker(ticker):
            return redirect(next_url)

        side = (pos.get("side") or "").strip().lower()
        if side != "short":
            return redirect(next_url)

        try:
            entry_price = float(pos.get("entry_price") or 0.0)
        except (TypeError, ValueError):
            entry_price = 0.0
        try:
            qty = int(pos.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if entry_price <= 0 or qty <= 0:
            return redirect(next_url)

        if finance.has_position_premium(position_id):
            return redirect(next_url)

        try:
            fees = float(pos.get("fees") or 0.0)
        except (TypeError, ValueError):
            fees = 0.0

        total_premium = finance.calculate_option_premium(
            entry_price=entry_price,
            qty=qty,
            fees=fees,
        )
        if total_premium <= 0:
            return redirect(next_url)

        trade_date = pos.get("trade_date") or datetime.date.today().isoformat()
        is_simulated = bool(pos.get("is_simulated") or 0)

        finance.add_transaction(
            date=trade_date,
            type=finance.TransactionType.PREMIUM,
            amount=total_premium,
            description=f"Prêmio {ticker} ({qty}x)",
            position_id=position_id,
            is_simulated=is_simulated,
        )

        reserve_darf = request.form.get("reserve_darf", "1") == "1"
        if reserve_darf:
            trade_type = (pos.get("trade_type") or "swing").strip().lower()
            darf_amount = finance.calculate_darf_provision(
                premium_amount=total_premium,
                trade_type=trade_type,
            )
            if darf_amount != 0.0:
                aliquota_opts = finance.option_tax_rate(trade_type)
                finance.add_transaction(
                    date=trade_date,
                    type=finance.TransactionType.DARF,
                    amount=darf_amount,
                    description=f"Provisão DARF {ticker} ({int(aliquota_opts*100)}%)",
                    position_id=position_id,
                    is_simulated=is_simulated,
                )

        return redirect(next_url)

    @app.post("/positions/recalc-premium/<int:position_id>")
    def recalc_position_premium(position_id: int):
        next_url = _safe_next_url(request.form.get("next")) or url_for("positions")
        pos = get_position(position_id)
        if not pos:
            return redirect(next_url)

        ticker = (pos.get("ticker") or "").strip()
        if not ticker or not _is_option_ticker(ticker):
            return redirect(next_url)

        side = (pos.get("side") or "").strip().lower()
        if side != "short":
            return redirect(next_url)

        try:
            entry_price = float(pos.get("entry_price") or 0.0)
        except (TypeError, ValueError):
            entry_price = 0.0
        try:
            qty = int(pos.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        try:
            fees = float(pos.get("fees") or 0.0)
        except (TypeError, ValueError):
            fees = 0.0

        if entry_price <= 0 or qty <= 0:
            return redirect(next_url)

        total_premium = finance.calculate_option_premium(
            entry_price=entry_price,
            qty=qty,
            fees=fees,
        )
        trade_date = pos.get("trade_date") or datetime.date.today().isoformat()
        trade_type = (pos.get("trade_type") or "swing").strip().lower()
        is_simulated = bool(pos.get("is_simulated") or 0)

        finance.recalc_position_premium_and_darf(
            position_id=position_id,
            trade_date=trade_date,
            ticker=ticker,
            qty=qty,
            premium_amount=total_premium,
            trade_type=trade_type,
            is_simulated=is_simulated,
        )
        return redirect(next_url)

    @app.post("/positions/update/<int:position_id>")
    def update_position_view(position_id: int):
        form = request.form
        status = form.get("status") or None
        is_simulated = None
        if form.get("is_simulated") is not None:
            is_simulated = form.get("is_simulated") == "1"
        parent_id = None
        if form.get("parent_position_id"):
            try:
                parent_id = int(form.get("parent_position_id"))
            except ValueError:
                parent_id = None
        side_raw = form.get("side") or None
        update_position(
            position_id=position_id,
            trade_date=form.get("trade_date") or None,
            qty=int(form["qty"]) if form.get("qty") else None,
            entry_price=_parse_form_float(form.get("entry_price")) if form.get("entry_price") else None,
            fees=_parse_form_float(form.get("fees")) if form.get("fees") else None,
            status=status,
            exit_date=form.get("exit_date") or None,
            exit_price=_parse_form_float(form.get("exit_price")) if form.get("exit_price") else None,
            notes=form.get("notes") or None,
            trade_type=form.get("trade_type") or None,
            side=side_raw,
            irrf=_parse_form_float(form.get("irrf")) if form.get("irrf") else None,
            partial_date=form.get("partial_date") or None,
            partial_price=_parse_form_float(form.get("partial_price")) if form.get("partial_price") else None,
            partial_qty=int(form["partial_qty"]) if form.get("partial_qty") else None,
            exit_reason=form.get("exit_reason") or None,
            is_simulated=is_simulated,
            parent_position_id=parent_id,
            strategy_tag=form.get("strategy_tag") or None,
        )
        ticker = (form.get("ticker") or "").strip().upper()
        side = (side_raw or "").strip().lower()
        if _is_option_ticker(ticker) and side == "short":
            qty_val = int(form["qty"]) if form.get("qty") else 0
            partial_qty_val = int(form["partial_qty"]) if form.get("partial_qty") else None
            is_simulated_flag = form.get("is_simulated") == "1"
            finance.sync_short_option_buyback(
                position_id=position_id,
                ticker=ticker,
                qty=qty_val,
                partial_qty=partial_qty_val,
                status=status,
                exit_date=form.get("exit_date") or None,
                exit_price=_parse_form_float(form.get("exit_price")) if form.get("exit_price") else None,
                is_simulated=is_simulated_flag,
            )
        return redirect(_safe_next_url(form.get("next")) or url_for("positions"))

    @app.post("/positions/delete/<int:position_id>")
    def delete_position_view(position_id: int):
        delete_position(position_id=position_id)
        return redirect(_safe_next_url(request.form.get("next")) or url_for("positions"))

    def _parse_form_float(value: str | None) -> float:
        if not value:
            return 0.0
        text = value.strip().replace("%", "").replace(",", ".")
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _parse_form_date(value: str | None) -> str | None:
        if not value:
            return None
        text = value.strip()
        if not text:
            return None
        # Aceita ISO (YYYY-MM-DD)
        try:
            return datetime.date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        # Aceita dd/mm/YYYY (vencimento da B3 no snapshot)
        try:
            return datetime.datetime.strptime(text, "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None

    def _lookup_underlying_from_snapshot(ticker: str) -> str | None:
        if not ticker:
            return None
        t = ticker.strip().upper()
        db_path = get_db_path()
        if not db_path.exists():
            return None
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                'SELECT underlying FROM option_snapshots WHERE ticker = ? ORDER BY snapshot_date DESC LIMIT 1',
                (t,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _safe_next_url(value: str | None) -> str | None:
        if not value:
            return None
        candidate = value.strip()
        if not candidate.startswith("/positions"):
            return None
        return candidate

    def _is_option_ticker(ticker: str | None) -> bool:
        return infer_option_type(ticker or "") in {"CALL", "PUT"}

    def _lookup_option_strike(ticker: str) -> float | None:
        """Recupera o strike do ticker de opção a partir do último snapshot."""

        if not ticker:
            return None
        t = ticker.strip().upper()
        db_path = get_db_path()
        if not db_path.exists():
            return None
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT strike
                FROM option_snapshots
                WHERE ticker = ?
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (t,),
            ).fetchone()
            if not row:
                return None
            return float(parse_ptbr_number(row["strike"]) or 0.0)
        finally:
            conn.close()

    def _auto_fees(
        *,
        ticker: str,
        underlying: str,
        qty: int,
        entry_price: float,
    ) -> float:
        """Calcula taxas padrão a partir das configurações, se possível."""

        fees_cfg: FeeSettings = get_fee_settings()
        t = (ticker or "").strip().upper()
        u = (underlying or "").strip().upper()
        qty = max(int(qty or 0), 0)
        entry_price = float(entry_price or 0.0)

        if not t or qty <= 0 or entry_price <= 0:
            return 0.0

        # Se ticker == underlying, tratamos como ação/ETF.
        if u and t == u:
            value = entry_price * qty
            return max(
                0.0,
                float(fees_cfg.equity_fixed)
                + (float(fees_cfg.equity_percent) / 100.0) * value,
            )

        # Caso contrário, usamos regra de opções.
        strike = _lookup_option_strike(t)
        if not strike or strike <= 0:
            # Sem strike conhecido, pelo menos aplicamos a parte fixa.
            return max(0.0, float(fees_cfg.option_fixed))
        # Interpretação: qty = número de opções (mesmo número de ações expostas).
        # Valor nocional aproximado = strike * qty.
        notional = strike * qty
        return max(
            0.0,
            float(fees_cfg.option_fixed)
            + (float(fees_cfg.option_percent_notional) / 100.0) * notional,
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
