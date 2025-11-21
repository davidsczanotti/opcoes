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

        return render_template(
            "index.html",
            data=data,
            min_score=min_score,
            limit=limit,
            underlying_filter=underlying_filter,
            alerts_map=alerts_map,
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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
