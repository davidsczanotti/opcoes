from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from .config import get_db_path
from .portfolio import add_position, delete_position, list_positions, update_position, close_position, get_position
from .scraper.storage import _parse_ptbr_number
from .settings import (
    FeeSettings,
    StrategySettings,
    get_fee_settings,
    get_strategy_settings,
    update_fee_settings,
    update_strategy_settings,
)
from .strategies import get_cash_covered_put_context, get_covered_call_context, get_ranking_context
from . import finance


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
                ticker=pos["underlying"], # Now we own the stock
                underlying=pos["underlying"],
                trade_date=date,
                qty=qty,
                entry_price=strike,
                fees=0.0, # Fees handled in transaction? Or user adds later?
                trade_type="stock",
                notes=f"Exercício da opção {pos['ticker']}",
                parent_position_id=position_id
            )

        return redirect(url_for("cash_covered_put"))

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
        positions = list_positions(include_closed=True)
        return render_template("positions.html", positions=positions)

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

        # Se for venda de opção (PUT ou CALL) e não simulado, registra o prêmio no caixa
        if entry_price > 0:
            # Simplificação: se tem "trade_type" swing/daytrade, assumimos venda? 
            # Melhor checar se é option. infer_option_type não está importado aqui, mas podemos assumir pelo ticker.
            # Se for short position (venda), entra dinheiro.
            # O form add_position atual assume "Compra"? Não, o cli.py diz "add_position" e "entry_price".
            # Normalmente "add position" é "abrir". 
            # Se for "Venda Coberta" ou "Venda de Put", abrimos VENDIDO.
            # O sistema atual de portfolio não distingue explicitamente Long/Short no "add", apenas qtd.
            # Vamos assumir que se o usuário está na tela de "Cash Covered Put", ele está vendendo.
            # Mas o endpoint /positions/add é genérico.
            # Vamos adicionar um checkbox ou hidden field "credit_premium" no form da view, ou inferir.
            # Por enquanto, vamos deixar manual ou fazer uma verificação simples:
            # Se o usuário marcar "Registrar Prêmio no Caixa" (novo campo no form).
            if form.get("record_premium") == "1":
                total_premium = (entry_price * qty) - fees
                finance.add_transaction(
                    date=form.get("trade_date", ""),
                    type=finance.TransactionType.PREMIUM,
                    amount=total_premium,
                    description=f"Prêmio {ticker} ({qty}x)",
                    position_id=pos_id,
                    is_simulated=is_simulated,
                )

        return redirect(url_for("positions"))

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
        return redirect(url_for("positions"))

    @app.post("/positions/delete/<int:position_id>")
    def delete_position_view(position_id: int):
        delete_position(position_id=position_id)
        return redirect(url_for("positions"))

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
