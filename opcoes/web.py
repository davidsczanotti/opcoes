from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from .report import generate_report
from .portfolio import list_positions, add_position, update_position, delete_position
from .scraper.storage import _parse_ptbr_number
from .settings import FeeSettings, get_fee_settings, update_fee_settings


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    @app.route("/")
    def index() -> str:
        min_score = _get_int_arg("min_score", 8)
        limit = _get_int_arg("limit", 30)
        recurring_days = _get_int_arg("recurring_days", 30)
        recurring_limit = _get_int_arg("recurring_limit", 15)
        underlying_filter = request.args.get("underlying", "").strip().upper()

        data = generate_report(min_score=min_score, limit=limit, recurring_days=recurring_days, recurring_limit=recurring_limit)
        if underlying_filter:
            data.opportunities = [
                o
                for o in data.opportunities
                if underlying_filter in (o.get("underlying") or "").upper() or underlying_filter in (o.get("ticker") or "").upper()
            ]
            data.recurring_opportunities = [
                o
                for o in data.recurring_opportunities
                if underlying_filter in (o.get("underlying") or "").upper() or underlying_filter in (o.get("ticker") or "").upper()
            ]

        alerts_map = {}
        for alert in data.alerts:
            pos = alert.get("position")
            if not pos:
                continue
            alerts_map[pos.get("id")] = alert.get("reasons", [])

        positions_real = [p for p in data.positions if not p.get("is_simulated")]
        positions_simulated = [p for p in data.positions if p.get("is_simulated")]
        totals_real = _compute_totals(positions_real)
        totals_simulated = _compute_totals(positions_simulated)
        all_opps = list(data.opportunities) + list(data.theoretical_opportunities)
        segments = _segment_opportunities(all_opps)

        return render_template(
            "index.html",
            data=data,
            min_score=min_score,
            limit=limit,
            recurring_days=recurring_days,
            recurring_limit=recurring_limit,
            underlying_filter=underlying_filter,
            alerts_map=alerts_map,
            totals_real=totals_real,
            totals_simulated=totals_simulated,
            positions_real=positions_real,
            positions_simulated=positions_simulated,
            segments=segments,
        )

    @app.route("/covered-call")
    def covered_call() -> str:
        # Ativo base padrão para covered call (pode ser alterado via query string).
        underlying = (request.args.get("underlying", "CMIG4") or "CMIG4").strip().upper()
        # Filtros padrão para a estratégia:
        # - prêmio extrínseco >= 2% sobre o spot
        # - vencimentos a partir de ~30 dias (até 200 por padrão, ajustável)
        # - strike ao menos 1% acima do spot (dist_perc_strike >= 1)
        min_extrinsic = float(request.args.get("min_extrinsic", 2.0) or 0.0)
        min_days = _get_int_arg("min_days", 30)
        max_days = _get_int_arg("max_days", 200)
        min_dist_strike = float(request.args.get("min_dist_strike", 1.0) or 0.0)

        positions_open = list_positions(include_closed=False)
        positions_real = [p for p in positions_open if not p.get("is_simulated")]
        positions_simulated = [p for p in positions_open if p.get("is_simulated")]

        stock_real, lots_real, covered_real = _bova_coverage(positions_real, underlying)
        stock_sim, lots_sim, covered_sim = _bova_coverage(positions_simulated, underlying)

        suggestions = _fetch_bova_suggestions(
            db_path=Path("data/opcoes_snapshots.db"),
            underlying=underlying,
            min_extrinsic=min_extrinsic,
            min_days=min_days,
            max_days=max_days,
            min_dist_strike=min_dist_strike,
        )

        return render_template(
            "covered_call.html",
            underlying=underlying,
            stock_real=stock_real,
            stock_sim=stock_sim,
            covered_real=covered_real,
            covered_sim=covered_sim,
            lots_real=lots_real,
            lots_sim=lots_sim,
            suggestions=suggestions,
        )

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
        )
        return redirect(url_for("positions"))

    @app.post("/positions/update/<int:position_id>")
    def update_position_view(position_id: int):
        form = request.form
        status = form.get("status") or None
        is_simulated = None
        if form.get("is_simulated") is not None:
            is_simulated = form.get("is_simulated") == "1"
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

    def _lookup_option_strike(ticker: str) -> float | None:
        """Recupera o strike do ticker de opção a partir do último snapshot."""

        if not ticker:
            return None
        t = ticker.strip().upper()
        db_path = Path("data/opcoes_snapshots.db")
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

    def _segment_opportunities(opps: list[dict]) -> dict:
        segments = {
            "carteira": [],
            "alavancagem": [],
            "aposta": [],
        }
        for o in opps:
            status = (o.get("Status_Moneyness") or "").lower()
            delta = o.get("delta")
            try:
                delta_val = float(delta) if delta is not None else None
            except (TypeError, ValueError):
                delta_val = None

            if "itm" in status or (delta_val is not None and delta_val >= 0.7):
                segments["carteira"].append(o)
                continue
            if "0-5% otm" in status or "colada" in status or "atm" in status:
                segments["alavancagem"].append(o)
                continue
            segments["aposta"].append(o)
        return segments

    def _bova_coverage(positions: list[dict], underlying: str) -> tuple[dict, list[dict], list[dict]]:
        """Calcula alocação de underlying -> calls via FIFO, por grupo (real/simulado).

        Retorna:
        - resumo de estoque (shares_total/cobertas/livres + min/máx/média dos livres)
        - lista de lotes de BOVA11 com qtd total, coberta e livre
        - lista de calls de BOVA11 em aberto (para conveniência do template)
        """

        # Lotes do ativo-objeto (ticker == underlying)
        bova_lots = [
            p
            for p in positions
            if (p.get("ticker") or "").upper() == underlying.upper()
        ]
        # Calls do ativo-objeto (underlying == underlying, ticker != underlying)
        call_positions = [
            p
            for p in positions
            if (p.get("underlying") or "").upper() == underlying.upper() and (p.get("ticker") or "").upper() != underlying.upper()
        ]

        # Ordena lotes e calls por data (FIFO)
        def _key_date(pos: dict) -> str:
            return str(pos.get("trade_date") or "")

        bova_lots = sorted(bova_lots, key=_key_date)
        call_positions = sorted(call_positions, key=_key_date)

        # Inicializa cobertura por lote
        lot_infos: list[dict] = []
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

        # Alocação FIFO: assumimos 1:1 entre opções e ações
        lot_index = 0
        for call in call_positions:
            open_contracts = int(call.get("open_qty") or call.get("qty") or 0)
            # Interpretamos qty como quantidade de opções e usamos 1:1
            # com ações do underlying para cobertura.
            need = open_contracts
            while need > 0 and lot_index < len(lot_infos):
                lot = lot_infos[lot_index]
                available = max(lot["open_qty"] - lot["covered"], 0)
                if available <= 0:
                    lot_index += 1
                    continue
                alloc = min(available, need)
                lot["covered"] += alloc
                lot["free"] = max(lot["open_qty"] - lot["covered"], 0)
                need -= alloc
                if lot["free"] <= 0:
                    lot_index += 1

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

        stock_summary = {
            "shares_total": int(shares_total),
            "shares_covered": int(shares_covered),
            "shares_free": int(shares_free),
            "free_min_price": free_min,
            "free_max_price": free_max,
            "free_avg_price": free_avg,
        }

        return stock_summary, lot_infos, call_positions

    def _fetch_bova_suggestions(
        *,
        db_path: Path,
        underlying: str,
        min_extrinsic: float,
        min_days: int,
        max_days: int,
        min_dist_strike: float,
    ) -> list[dict]:
        if not db_path.exists():
            return []
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT MAX(snapshot_date) AS d FROM option_snapshots").fetchone()
            snapshot_date = row["d"] if row else None
            if not snapshot_date:
                return []
            rows = conn.execute(
                """
                SELECT
                    ticker,
                    underlying,
                    vencimento,
                    dias_uteis,
                    strike,
                    dist_perc_strike,
                    underlying_price,
                    extrinsic_pct_spot,
                    "%_Alta_p_2x" AS pct_2x,
                    score_total
                FROM option_snapshots
                WHERE snapshot_date = ?
                  AND UPPER(underlying) = ?
                  AND dias_uteis IS NOT NULL
                """,
                (snapshot_date, underlying.upper()),
            ).fetchall()
        finally:
            conn.close()

        def _parse_float(value) -> float | None:
            try:
                return float(_parse_ptbr_number(value))
            except Exception:
                return None

        suggestions: list[dict] = []
        for r in rows:
            dias_uteis = _parse_float(r["dias_uteis"])
            if dias_uteis is None:
                continue
            if dias_uteis < min_days or dias_uteis > max_days:
                continue
            extrinsic = _parse_float(r["extrinsic_pct_spot"])
            if extrinsic is None or extrinsic < min_extrinsic:
                continue
            dist = _parse_float(r["dist_perc_strike"])
            # dist_perc_strike é a distância do strike ao spot em %, positiva = OTM
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

        suggestions.sort(key=lambda s: (-(s.get("extrinsic_pct_spot") or 0.0), s.get("dist_perc_strike") or 0.0))
        return suggestions

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
