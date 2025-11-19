from __future__ import annotations

from pathlib import Path

from flask import Flask, render_template

from .report import generate_report
from .portfolio import list_positions


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    @app.route("/")
    def index() -> str:
        data = generate_report(min_score=8, limit=30)
        return render_template("index.html", data=data)

    @app.route("/positions")
    def positions() -> str:
        positions = list_positions(include_closed=True)
        return render_template("positions.html", positions=positions)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)

