from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from .config import get_db_path
from .portfolio import add_position, delete_position, list_positions, update_position, close_position, get_position
from .scraper.storage import _parse_ptbr_number
from .utils import infer_option_type
from .settings import (
    FeeSettings,
    StrategySettings,
    get_fee_settings,
    get_strategy_settings,
    update_fee_settings,
    update_strategy_settings,
)
from .strategies import get_cash_covered_put_context, get_covered_call_context, get_fundamentus_context, get_ranking_context
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

        pos = get_position(position_id)
        is_simulated = bool(pos["is_simulated"]) if pos else False
        
        # 1. Close the PUT position
        # Assuming exit price 0 or current market price? Usually 0 if exercised ITM? 
        # Actually, if assigned, we keep the premium, but buy the stock.
        # So we close the option position effectively.
        close_position(position_id=position_id, exit_date=date, exit_price=0.0, exit_reason="Exercício")

        # 2. Debit the cash (Strike * Qty)
        cost = strike * qty
        finance.add_transaction(
            date=date,
            type=finance.TransactionType.ASSIGNMENT,
            amount=-cost,
            description=f"Exercício PUT {position_id} @ {strike}",
            position_id=position_id,
            is_simulated=is_simulated,
        )

        # 3. Open STOCK position (optional, but good for tracking)
        # We need the underlying ticker.
        if pos:
            add_position(
                ticker=pos["underlying"],  # Agora passamos a ter o papel
                underlying=pos["underlying"],
                trade_date=date,
                qty=qty,
                entry_price=strike,
                fees=0.0,  # Taxas podem ser lançadas manualmente depois
                trade_type="stock",
                notes=f"Exercício da opção {pos['ticker']}",
                is_simulated=is_simulated,
                parent_position_id=position_id,
                strategy_tag="covered_call",
            )

        return redirect(url_for("cash_covered_put"))

    @app.post("/finance/callaway")
    def finance_callaway():
        form = request.form
        position_id = int(form.get("position_id"))
        date = _parse_form_date(form.get("date")) or datetime.date.today().isoformat()

        call_pos = get_position(position_id)
        if not call_pos:
            return redirect(url_for("covered_call"))

        underlying = (call_pos.get("underlying") or "").strip().upper()
        is_simulated = bool(call_pos.get("is_simulated") or 0)

        if (call_pos.get("status") or "").strip().lower() != "open":
            return redirect(url_for("covered_call", underlying=underlying))

        if infer_option_type(call_pos.get("ticker")) != "CALL":
            return redirect(url_for("covered_call", underlying=underlying))

        # Para exercício, usamos a quantidade em aberto da CALL e o strike do último snapshot.
        try:
            qty = int(call_pos.get("open_qty") or call_pos.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        strike = call_pos.get("strike")

        if qty <= 0 or strike is None:
            return redirect(url_for("covered_call", underlying=underlying))

        lot_id = call_pos.get("parent_position_id")
        if lot_id is None:
            # Sem vínculo com lote: não dá para dar baixa das ações com segurança.
            return redirect(url_for("covered_call", underlying=underlying))

        lot_pos = get_position(int(lot_id))
        if not lot_pos:
            return redirect(url_for("covered_call", underlying=underlying))

        if (lot_pos.get("status") or "").strip().lower() != "open":
            return redirect(url_for("covered_call", underlying=underlying))

        if bool(lot_pos.get("is_simulated") or 0) != is_simulated:
            return redirect(url_for("covered_call", underlying=underlying))

        lot_ticker = (lot_pos.get("ticker") or "").strip().upper()
        if underlying and lot_ticker and lot_ticker != underlying:
            return redirect(url_for("covered_call", underlying=underlying))

        try:
            lot_open_qty = int(lot_pos.get("open_qty") or lot_pos.get("qty") or 0)
        except (TypeError, ValueError):
            lot_open_qty = 0

        if lot_open_qty < qty:
            return redirect(url_for("covered_call", underlying=underlying))

        # 1) Dá baixa nas ações (fecha lote inteiro ou registra parcial).
        if lot_open_qty == qty:
            close_position(
                position_id=int(lot_id),
                exit_date=date,
                exit_price=float(strike),
                exit_reason="Exercício",
            )
        else:
            existing_partial_qty = int(lot_pos.get("partial_qty") or 0)
            existing_partial_price = lot_pos.get("partial_price")
            total_qty = int(lot_pos.get("qty") or 0)
            new_partial_qty = existing_partial_qty + qty
            if new_partial_qty > total_qty:
                return redirect(url_for("covered_call", underlying=underlying))

            new_partial_price = float(strike)
            if existing_partial_qty > 0 and existing_partial_price is not None:
                try:
                    new_partial_price = (
                        (float(existing_partial_price) * existing_partial_qty) + (float(strike) * qty)
                    ) / new_partial_qty
                except Exception:
                    new_partial_price = float(strike)

            update_position(
                position_id=int(lot_id),
                partial_qty=new_partial_qty,
                partial_price=float(new_partial_price),
                partial_date=date,
                exit_reason="Exercício",
            )
            if new_partial_qty == total_qty:
                close_position(
                    position_id=int(lot_id),
                    exit_date=date,
                    exit_price=float(strike),
                    exit_reason="Exercício",
                )

        # 2) Fecha a CALL (exercida).
        close_position(
            position_id=position_id,
            exit_date=date,
            exit_price=0.0,
            exit_reason="Exercício",
        )

        # 3) Credita o caixa liberado (Strike * Qty) no modo (real/simulado) correspondente.
        proceeds = float(strike) * qty
        finance.add_transaction(
            date=date,
            type=finance.TransactionType.SELL,
            amount=proceeds,
            description=f"Venda (CALL exercida) {call_pos.get('ticker')} @ {float(strike):.2f}",
            position_id=position_id,
            is_simulated=is_simulated,
        )

        return redirect(url_for("covered_call", underlying=underlying))

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

            return redirect(url_for("settings_view"))

        fees_cfg: FeeSettings = get_fee_settings()
        strat_cfg: StrategySettings = get_strategy_settings()
        return render_template("settings.html", fees=fees_cfg, strat=strat_cfg)
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
        
        pos_id = add_position(
            ticker=ticker,
            underlying=underlying,
            trade_date=form.get("trade_date", ""),
            qty=qty,
            entry_price=entry_price,
            fees=fees,
            trade_type=form.get("trade_type", "swing"),
            irrf=float(form["irrf"]) if form.get("irrf") else None,
            notes=form.get("notes") or None,
            is_simulated=is_simulated,
            parent_position_id=parent_id,
            strategy_tag=form.get("strategy_tag") or None,
        )

        # Registro opcional: prêmio no caixa (venda) + provisão DARF (saldo limpo).
        if entry_price > 0 and qty > 0 and form.get("record_premium") == "1":
            t = (ticker or "").strip().upper()
            u = (underlying or "").strip().upper()
            is_option = bool(u) and t and t != u

            if is_option:
                total_premium = (entry_price * qty) - fees
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
                    aliquota_opts = 0.20 if "day" in trade_type else 0.15
                    base_ir = max(0.0, float(total_premium))
                    darf = base_ir * aliquota_opts
                    if darf > 0:
                        finance.add_transaction(
                            date=form.get("trade_date", ""),
                            type=finance.TransactionType.DARF,
                            amount=-darf,
                            description=f"Provisão DARF {ticker} ({int(aliquota_opts*100)}%)",
                            position_id=pos_id,
                            is_simulated=is_simulated,
                        )

        return redirect(_safe_next_url(form.get("next")) or url_for("positions"))

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
        update_position(
            position_id=position_id,
            trade_date=form.get("trade_date") or None,
            qty=int(form["qty"]) if form.get("qty") else None,
            entry_price=float(form["entry_price"]) if form.get("entry_price") else None,
            fees=float(form["fees"]) if form.get("fees") else None,
            status=status,
            exit_date=form.get("exit_date") or None,
            exit_price=float(form["exit_price"]) if form.get("exit_price") else None,
            notes=form.get("notes") or None,
            trade_type=form.get("trade_type") or None,
            irrf=float(form["irrf"]) if form.get("irrf") else None,
            partial_date=form.get("partial_date") or None,
            partial_price=float(form["partial_price"]) if form.get("partial_price") else None,
            partial_qty=int(form["partial_qty"]) if form.get("partial_qty") else None,
            exit_reason=form.get("exit_reason") or None,
            is_simulated=is_simulated,
            parent_position_id=parent_id,
            strategy_tag=form.get("strategy_tag") or None,
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
            return float(_parse_ptbr_number(row["strike"]) or 0.0)
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
