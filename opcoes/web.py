from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from .config import get_db_path
from .portfolio import add_position, delete_position, list_positions, update_position
from .scraper.storage import _parse_ptbr_number
from .settings import FeeSettings, get_fee_settings, update_fee_settings
from .strategies import get_cash_covered_put_context, get_covered_call_context, get_ranking_context


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

    @app.route("/settings", methods=["GET", "POST"])
    def settings_view() -> str:
        if request.method == "POST":
            form = request.form
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
            return redirect(url_for("settings_view"))

        fees_cfg: FeeSettings = get_fee_settings()
        return render_template("settings.html", fees=fees_cfg)
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
        add_position(
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
