from __future__ import annotations

import sqlite3

from opcoes import finance, portfolio
from opcoes.scraper.snapshots import SnapshotDB
from opcoes.scraper.storage import CSV_FIELDS
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
            "ultimo": "1,00",
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


def test_record_premium_with_darf_provision(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "premium_darf.db"
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
    _insert_underlying_snapshot(db_path, snapshot_date="2025-01-01", underlying=underlying, price=10.50)

    app = create_app()
    app.testing = True
    client = app.test_client()

    qty = 100
    entry_price = 1.00
    gross = entry_price * qty

    for sim in (False, True):
        sim_flag = "1" if sim else "0"

        # PUT swing -> 15%
        res = client.post(
            "/positions/add",
            data={
                "ticker": put_ticker,
                "underlying": "",  # auto from snapshot
                "qty": str(qty),
                "entry_price": f"{entry_price:.2f}",
                "trade_date": "2025-01-02",
                "trade_type": "swing",
                "strategy_tag": "cash_put",
                "record_premium": "1",
                "reserve_darf": "1",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        put_pos = portfolio.list_positions(ticker=put_ticker, include_closed=False, is_simulated=sim)[0]

        txs = finance.get_transactions(limit=50)
        prem = [t for t in txs if t.type == finance.TransactionType.PREMIUM and t.position_id == put_pos["id"]]
        darf = [t for t in txs if t.type == finance.TransactionType.DARF and t.position_id == put_pos["id"]]
        assert len(prem) == 1
        assert len(darf) == 1
        assert abs(prem[0].amount - gross) < 1e-6
        assert abs(darf[0].amount - (-(gross * 0.15))) < 1e-6
        assert bool(prem[0].is_simulated) is sim
        assert bool(darf[0].is_simulated) is sim

        # CALL daytrade -> 20%
        res = client.post(
            "/positions/add",
            data={
                "ticker": call_ticker,
                "underlying": "",  # auto from snapshot
                "qty": str(qty),
                "entry_price": f"{entry_price:.2f}",
                "trade_date": "2025-01-03",
                "trade_type": "daytrade",
                "strategy_tag": "covered_call",
                "record_premium": "1",
                "reserve_darf": "1",
                "is_simulated": sim_flag,
            },
        )
        assert res.status_code in (302, 303)

        call_pos = portfolio.list_positions(ticker=call_ticker, include_closed=False, is_simulated=sim)[0]

        txs = finance.get_transactions(limit=50)
        prem = [t for t in txs if t.type == finance.TransactionType.PREMIUM and t.position_id == call_pos["id"]]
        darf = [t for t in txs if t.type == finance.TransactionType.DARF and t.position_id == call_pos["id"]]
        assert len(prem) == 1
        assert len(darf) == 1
        assert abs(prem[0].amount - gross) < 1e-6
        assert abs(darf[0].amount - (-(gross * 0.20))) < 1e-6
        assert bool(prem[0].is_simulated) is sim
        assert bool(darf[0].is_simulated) is sim

