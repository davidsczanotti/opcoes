from __future__ import annotations

import sqlite3

from opcoes import finance, portfolio
from opcoes.scraper.snapshots import SnapshotDB
from opcoes.scraper.storage import CSV_FIELDS
from opcoes.strategies.covered_call import get_covered_call_context
from opcoes.web import create_app


def _build_option_row(*, underlying: str, ticker: str, option_type: str, strike: str) -> dict:
    row = {col: "" for col in CSV_FIELDS}
    row.update(
        {
            "underlying": underlying,
            "ticker": ticker,
            "option_type": option_type,
            "vencimento": "2025-02-21",
            "dias_uteis": "30",
            "strike": strike,
            "ultimo": "0,50",
            "underlying_price": "10,50",
        }
    )
    return row


def test_wheel_flow_endpoints_real_and_simulated(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "wheel.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    underlying = "CMIG4"
    put_ticker = "CMIGM100"
    call_ticker = "CMIGB110"

    snap = SnapshotDB(db_path)
    snap.record_options(
        "2025-01-01",
        [
            _build_option_row(underlying=underlying, ticker=put_ticker, option_type="PUT", strike="10,00"),
            _build_option_row(underlying=underlying, ticker=call_ticker, option_type="CALL", strike="11,00"),
        ],
    )
    snap.close()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO underlying_snapshots
            (snapshot_date, underlying, price, price_date)
            VALUES (?, ?, ?, ?)
            """,
            ("2025-01-01", underlying, 10.50, "2025-01-01"),
        )
        conn.commit()
    finally:
        conn.close()

    app = create_app()
    app.testing = True
    client = app.test_client()

    deposit = 1000.0
    put_strike = 10.0
    call_strike = 11.0
    put_premium = 0.50
    call_premium = 0.40
    qty = 100
    expected_end_balance = deposit + (put_premium * qty) - (put_strike * qty) + (call_premium * qty) + (call_strike * qty)

    for sim in (False, True):
        sim_flag = "1" if sim else "0"

        # 1) deposit
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

        # 2) sell put + record premium
        res = client.post(
            "/positions/add",
            data={
                "underlying": underlying,
                "ticker": put_ticker,
                "qty": str(qty),
                "entry_price": f"{put_premium:.2f}",
                "trade_date": "2025-01-02",
                "trade_type": "swing",
                "strategy_tag": "cash_put",
                "record_premium": "1",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        put_pos = portfolio.list_positions(ticker=put_ticker, include_closed=False, is_simulated=sim)[0]

        # 3) exercise put (assignment): must create STOCK position with same sim flag
        res = client.post(
            "/finance/assign",
            data={
                "position_id": str(put_pos["id"]),
                "qty": str(qty),
                "strike": f"{put_strike:.2f}",
                "date": "2025-01-20",
            },
        )
        assert res.status_code in (302, 303)

        stock_positions = [
            p
            for p in portfolio.list_positions(include_closed=False, is_simulated=sim)
            if p["ticker"] == underlying and p.get("trade_type") == "stock" and p.get("parent_position_id") == put_pos["id"]
        ]
        assert len(stock_positions) == 1
        stock_pos = stock_positions[0]
        assert bool(stock_pos["is_simulated"]) is sim

        # This is what drives the \"covered call\" dashboard lot list.
        ctx_cc = get_covered_call_context({"underlying": underlying})
        lots = ctx_cc["lots_sim"] if sim else ctx_cc["lots_real"]
        assert any(int(lot["id"]) == int(stock_pos["id"]) for lot in lots)

        # 4) sell covered call + record premium
        res = client.post(
            "/positions/add",
            data={
                "underlying": underlying,
                "ticker": call_ticker,
                "qty": str(qty),
                "entry_price": f"{call_premium:.2f}",
                "trade_date": "2025-01-21",
                "trade_type": "swing",
                "strategy_tag": "covered_call",
                "parent_position_id": str(stock_pos["id"]),
                "record_premium": "1",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        call_pos = portfolio.list_positions(ticker=call_ticker, include_closed=False, is_simulated=sim)[0]

        # 5) call away: closes call + closes stock and credits cash as SELL
        res = client.post(
            "/finance/callaway",
            data={
                "position_id": str(call_pos["id"]),
                "date": "2025-02-21",
            },
        )
        assert res.status_code in (302, 303)

        assert portfolio.get_position(call_pos["id"])["status"] == "closed"
        assert portfolio.get_position(stock_pos["id"])["status"] == "closed"

        mode = "simulated" if sim else "real"
        assert abs(finance.get_balance(mode=mode) - expected_end_balance) < 1e-6

        txs = finance.get_transactions(limit=200)
        sells = [t for t in txs if t.type == finance.TransactionType.SELL and bool(t.is_simulated) is sim]
        assert sells

