from __future__ import annotations

import math

from opcoes import finance, portfolio


def _txs_for_position(position_id: int):
    txs = finance.get_transactions(limit=100)
    prem = [t for t in txs if t.type == finance.TransactionType.PREMIUM and t.position_id == position_id]
    darf = [t for t in txs if t.type == finance.TransactionType.DARF and t.position_id == position_id]
    return prem, darf


def test_recalc_premium_and_darf_overwrites(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "recalc.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    pos_id = portfolio.add_position(
        ticker="PETRN312",
        underlying="PETR4",
        trade_date="2026-01-09",
        qty=400,
        entry_price=0.61,
        fees=0.31,
        trade_type="swing",
        side="short",
    )

    finance.add_transaction(
        date="2026-01-09",
        type=finance.TransactionType.PREMIUM,
        amount=178.70,
        description="Prêmio antigo",
        position_id=pos_id,
    )
    finance.add_transaction(
        date="2026-01-09",
        type=finance.TransactionType.DARF,
        amount=-26.80,
        description="DARF antigo",
        position_id=pos_id,
    )

    premium_amount = (0.61 * 400) - 0.31
    result = finance.recalc_position_premium_and_darf(
        position_id=pos_id,
        trade_date="2026-01-09",
        ticker="PETRN312",
        qty=400,
        premium_amount=premium_amount,
        trade_type="swing",
        is_simulated=False,
    )

    assert math.isclose(result["premium"], premium_amount, abs_tol=1e-6)
    assert math.isclose(result["darf"], -round(premium_amount * 0.15, 2), abs_tol=1e-6)

    prem, darf = _txs_for_position(pos_id)
    assert len(prem) == 1
    assert len(darf) == 1
    assert math.isclose(prem[0].amount, premium_amount, abs_tol=1e-6)
    assert math.isclose(darf[0].amount, -round(premium_amount * 0.15, 2), abs_tol=1e-6)


def test_recalc_removes_when_no_premium(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "recalc_remove.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    pos_id = portfolio.add_position(
        ticker="PETRN312",
        underlying="PETR4",
        trade_date="2026-01-09",
        qty=400,
        entry_price=0.01,
        fees=10.00,
        trade_type="swing",
        side="short",
    )

    finance.add_transaction(
        date="2026-01-09",
        type=finance.TransactionType.PREMIUM,
        amount=5.00,
        description="Prêmio antigo",
        position_id=pos_id,
    )
    finance.add_transaction(
        date="2026-01-09",
        type=finance.TransactionType.DARF,
        amount=-0.75,
        description="DARF antigo",
        position_id=pos_id,
    )

    result = finance.recalc_position_premium_and_darf(
        position_id=pos_id,
        trade_date="2026-01-09",
        ticker="PETRN312",
        qty=400,
        premium_amount=0.0,
        trade_type="swing",
        is_simulated=False,
    )

    assert math.isclose(result["premium"], 0.0, abs_tol=1e-6)
    assert math.isclose(result["darf"], 0.0, abs_tol=1e-6)

    prem, darf = _txs_for_position(pos_id)
    assert prem == []
    assert darf == []


def test_get_ledger_sums_by_position(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "ledger_sums.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    pos_a = portfolio.add_position(
        ticker="ABCDM100",
        underlying="ABCD3",
        trade_date="2026-01-05",
        qty=100,
        entry_price=1.0,
        fees=0.1,
        trade_type="swing",
        side="short",
    )
    pos_b = portfolio.add_position(
        ticker="WXYZM200",
        underlying="WXYZ3",
        trade_date="2026-01-06",
        qty=200,
        entry_price=2.0,
        fees=0.2,
        trade_type="daytrade",
        side="short",
    )

    finance.add_transaction(
        date="2026-01-05",
        type=finance.TransactionType.PREMIUM,
        amount=99.90,
        description="Prêmio A",
        position_id=pos_a,
    )
    finance.add_transaction(
        date="2026-01-05",
        type=finance.TransactionType.DARF,
        amount=-14.99,
        description="DARF A",
        position_id=pos_a,
    )
    finance.add_transaction(
        date="2026-01-06",
        type=finance.TransactionType.PREMIUM,
        amount=399.80,
        description="Prêmio B",
        position_id=pos_b,
    )

    sums = finance.get_ledger_sums_by_position(
        types=[finance.TransactionType.PREMIUM, finance.TransactionType.DARF],
        is_simulated=False,
    )

    assert math.isclose(sums[pos_a][finance.TransactionType.PREMIUM.value], 99.90, abs_tol=1e-6)
    assert math.isclose(sums[pos_a][finance.TransactionType.DARF.value], -14.99, abs_tol=1e-6)
    assert math.isclose(sums[pos_b][finance.TransactionType.PREMIUM.value], 399.80, abs_tol=1e-6)
