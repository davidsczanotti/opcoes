from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from .report import generate_report
from .portfolio import list_positions, add_position, update_position, delete_position


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    @app.route("/")
    def index() -> str:
        min_score = _get_int_arg("min_score", 8)
        limit = _get_int_arg("limit", 30)
        underlying_filter = request.args.get("underlying", "").strip().upper()

        data = generate_report(min_score=min_score, limit=limit)
        if underlying_filter:
            data.opportunities = [
                o for o in data.opportunities if underlying_filter in o["underlying"].upper() or underlying_filter in o["ticker"].upper()
            ]

        alerts_map = {}
        for alert in data.alerts:
            pos = alert.get("position")
            if not pos:
                continue
            alerts_map[pos.get("id")] = alert.get("reasons", [])

        totals = _compute_totals(data.positions)

        return render_template(
            "index.html",
            data=data,
            min_score=min_score,
            limit=limit,
            underlying_filter=underlying_filter,
            alerts_map=alerts_map,
            totals=totals,
        )

    @app.route("/positions")
    def positions() -> str:
        positions = list_positions(include_closed=True)
        return render_template("positions.html", positions=positions)

    @app.post("/positions/add")
    def add_position_view():
        form = request.form
        underlying = form.get("underlying", "").strip()
        ticker = form.get("ticker", "").strip()
        if not underlying:
            underlying = _lookup_underlying_from_snapshot(ticker) or ""
        add_position(
            ticker=ticker,
            underlying=underlying,
            trade_date=form.get("trade_date", ""),
            qty=int(form.get("qty", 0)),
            entry_price=float(form.get("entry_price", 0.0)),
            fees=float(form.get("fees", 0.0) or 0.0),
            trade_type=form.get("trade_type", "swing"),
            irrf=float(form["irrf"]) if form.get("irrf") else None,
            notes=form.get("notes") or None,
        )
        return redirect(url_for("positions"))

    @app.post("/positions/update/<int:position_id>")
    def update_position_view(position_id: int):
        form = request.form
        status = form.get("status") or None
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
        )
        return redirect(url_for("positions"))

    @app.post("/positions/delete/<int:position_id>")
    def delete_position_view(position_id: int):
        delete_position(position_id=position_id)
        return redirect(url_for("positions"))

    def _get_int_arg(name: str, default: int) -> int:
        try:
            return int(request.args.get(name, default))
        except (TypeError, ValueError):
            return default

    def _lookup_underlying_from_snapshot(ticker: str) -> str | None:
        if not ticker:
            return None
        t = ticker.strip().upper()
        db_path = Path("data/opcoes_snapshots.db")
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

    def _compute_totals(positions: list[dict]) -> dict:
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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
