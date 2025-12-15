from __future__ import annotations

import sqlite3

from opcoes import finance, portfolio
from opcoes.scraper.snapshots import SnapshotDB
from opcoes.scraper.storage import CSV_FIELDS
from opcoes.strategies.cash_covered_put import get_cash_covered_put_context
from opcoes.strategies.covered_call import get_covered_call_context
from opcoes.web import create_app


def _build_option_row(*, underlying: str, ticker: str, option_type: str, strike: str) -> dict:
    row = {col: "" for col in CSV_FIELDS}
    row.update(
        {
            "underlying": underlying,
            "ticker": ticker,
            "option_type": option_type,
            "vencimento": "21/02/2025",
            "dias_uteis": "30",
            "strike": strike,
            "ultimo": "0,50",
            "underlying_price": "10,50",
        }
    )
    return row


def _insert_underlying_snapshot(db_path, *, snapshot_date: str, underlying: str, price: float) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO underlying_snapshots
            (snapshot_date, underlying, price, price_date)
            VALUES (?, ?, ?, ?)
            """,
            (snapshot_date, underlying, float(price), snapshot_date),
        )
        conn.commit()
    finally:
        conn.close()


def test_expire_put_closes_position_and_releases_collateral(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "expire_put.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    underlying = "CMIG4"
    put_ticker = "CMIGM100"

    snap = SnapshotDB(db_path)
    snap.record_options(
        "2025-01-01",
        [
            _build_option_row(underlying=underlying, ticker=put_ticker, option_type="PUT", strike="10,00"),
        ],
    )
    snap.close()
    _insert_underlying_snapshot(db_path, snapshot_date="2025-01-01", underlying=underlying, price=10.50)

    app = create_app()
    app.testing = True
    client = app.test_client()

    deposit = 1000.0
    strike = 10.0
    premium = 0.50
    qty = 100

    for sim in (False, True):
        sim_flag = "1" if sim else "0"
        cash_mode = "simulated" if sim else "real"

        # 1) cash in
        res = client.post(
            "/finance/add",
            data={
                "amount": f"{deposit:.2f}",
                "type": "DEPOSIT",
                "description": "Aporte teste",
                "date": "2025-01-01",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        # 2) sell put (credit premium)
        res = client.post(
            "/positions/add",
            data={
                "underlying": underlying,
                "ticker": put_ticker,
                "qty": str(qty),
                "entry_price": f"{premium:.2f}",
                "trade_date": "2025-01-02",
                "trade_type": "swing",
                "strategy_tag": "cash_put",
                "record_premium": "1",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        put_pos = portfolio.list_positions(ticker=put_ticker, include_closed=False, is_simulated=sim)[0]

        ctx_before = get_cash_covered_put_context({"underlying": underlying, "cash_mode": cash_mode})
        assert abs(ctx_before["finance"]["collateral_locked"] - (strike * qty)) < 1e-6
        assert abs(ctx_before["finance"]["available_cash"] - (premium * qty)) < 1e-6

        # 3) expire put (should close at 0 and release collateral)
        res = client.post(
            "/finance/expire",
            data={"position_id": str(put_pos["id"]), "date": "21/02/2025"},
        )
        assert res.status_code in (302, 303)

        assert portfolio.get_position(put_pos["id"])["status"] == "closed"

        # no stock created
        stock_positions = [
            p
            for p in portfolio.list_positions(include_closed=False, is_simulated=sim)
            if p["ticker"] == underlying and p.get("trade_type") == "stock"
        ]
        assert not stock_positions

        ctx_after = get_cash_covered_put_context({"underlying": underlying, "cash_mode": cash_mode})
        expected_cash = deposit + (premium * qty)
        assert abs(ctx_after["finance"]["collateral_locked"] - 0.0) < 1e-6
        assert abs(ctx_after["finance"]["total_cash"] - expected_cash) < 1e-6
        assert abs(ctx_after["finance"]["available_cash"] - expected_cash) < 1e-6

        txs = finance.get_transactions(limit=200)
        assignments = [t for t in txs if t.type == finance.TransactionType.ASSIGNMENT and bool(t.is_simulated) is sim]
        assert not assignments


def test_expire_call_closes_call_and_frees_shares(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "expire_call.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    underlying = "CMIG4"
    call_ticker = "CMIGB110"

    snap = SnapshotDB(db_path)
    snap.record_options(
        "2025-01-01",
        [
            _build_option_row(underlying=underlying, ticker=call_ticker, option_type="CALL", strike="11,00"),
        ],
    )
    snap.close()
    _insert_underlying_snapshot(db_path, snapshot_date="2025-01-01", underlying=underlying, price=10.50)

    app = create_app()
    app.testing = True
    client = app.test_client()

    deposit = 1000.0
    premium = 0.40
    lot_qty = 100

    for sim in (False, True):
        sim_flag = "1" if sim else "0"
        cash_mode = "simulated" if sim else "real"

        # cash in (only to keep ledger non-empty)
        res = client.post(
            "/finance/add",
            data={
                "amount": f"{deposit:.2f}",
                "type": "DEPOSIT",
                "description": "Aporte teste",
                "date": "2025-01-01",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        lot_id = portfolio.add_position(
            ticker=underlying,
            underlying=underlying,
            trade_date="2025-01-05",
            qty=lot_qty,
            entry_price=10.0,
            trade_type="stock",
            is_simulated=sim,
            strategy_tag="covered_call",
        )

        # sell call (credit premium)
        res = client.post(
            "/positions/add",
            data={
                "underlying": underlying,
                "ticker": call_ticker,
                "qty": str(lot_qty),
                "entry_price": f"{premium:.2f}",
                "trade_date": "2025-01-06",
                "trade_type": "swing",
                "strategy_tag": "covered_call",
                "parent_position_id": str(lot_id),
                "record_premium": "1",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        call_pos = portfolio.list_positions(ticker=call_ticker, include_closed=False, is_simulated=sim)[0]

        ctx_before = get_covered_call_context({"underlying": underlying})
        stock_summary_before = ctx_before["stock_sim"] if sim else ctx_before["stock_real"]
        assert stock_summary_before["shares_total"] == lot_qty
        assert stock_summary_before["shares_covered"] == lot_qty
        assert stock_summary_before["shares_free"] == 0

        # expire call (close at 0, keep stock open)
        res = client.post(
            "/finance/expire",
            data={"position_id": str(call_pos["id"]), "date": "21/02/2025"},
        )
        assert res.status_code in (302, 303)

        assert portfolio.get_position(call_pos["id"])["status"] == "closed"
        assert portfolio.get_position(lot_id)["status"] == "open"

        ctx_after = get_covered_call_context({"underlying": underlying})
        stock_summary_after = ctx_after["stock_sim"] if sim else ctx_after["stock_real"]
        assert stock_summary_after["shares_total"] == lot_qty
        assert stock_summary_after["shares_covered"] == 0
        assert stock_summary_after["shares_free"] == lot_qty

        # no SELL on expiration
        txs = finance.get_transactions(limit=200)
        sells = [t for t in txs if t.type == finance.TransactionType.SELL and bool(t.is_simulated) is sim]
        assert not sells

        # still has expected cash balance (deposit + premium credit)
        expected_cash = deposit + (premium * lot_qty)
        assert abs(finance.get_balance(mode=cash_mode) - expected_cash) < 1e-6

