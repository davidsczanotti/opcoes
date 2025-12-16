from __future__ import annotations

from opcoes import darf, finance
from opcoes.web import create_app


def test_darf_generate_and_pay_does_not_change_cash(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "darf_flow.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    app = create_app()
    app.testing = True
    client = app.test_client()

    # Cria uma venda de opção com provisão de DARF (saldo limpo) em 2025-01.
    res = client.post(
        "/positions/add",
        data={
            "ticker": "CMIGM100",
            "underlying": "CMIG4",
            "qty": "100",
            "entry_price": "1.00",
            "fees": "0",
            "trade_date": "2025-01-15",
            "trade_type": "swing",
            "strategy_tag": "cash_put",
            "record_premium": "1",
            "reserve_darf": "1",
            "is_simulated": "0",
        },
    )
    assert res.status_code in (302, 303)

    # Caixa após prêmio líquido (100 - 15).
    balance_before = finance.get_balance(mode="real")
    assert abs(balance_before - 85.0) < 1e-6

    # Gera DARF da competência.
    res = client.post("/darf/generate", data={"period": "2025-01", "is_simulated": "0"})
    assert res.status_code in (302, 303)

    rec = darf.get_month(period="2025-01", is_simulated=False)
    assert rec is not None
    assert rec.due_date == "2025-02-28"
    assert abs(rec.amount - 15.0) < 1e-6
    assert rec.paid_date is None

    # Não altera o saldo (registro é em tabela própria, não no ledger).
    balance_after_generate = finance.get_balance(mode="real")
    assert abs(balance_after_generate - balance_before) < 1e-6

    # Marca como pago.
    res = client.post(
        "/darf/pay",
        data={
            "period": "2025-01",
            "is_simulated": "0",
            "paid_date": "2025-02-28",
        },
    )
    assert res.status_code in (302, 303)

    rec2 = darf.get_month(period="2025-01", is_simulated=False)
    assert rec2 is not None
    assert rec2.paid_date == "2025-02-28"
    assert abs((rec2.paid_amount or 0.0) - 15.0) < 1e-6

    balance_after_pay = finance.get_balance(mode="real")
    assert abs(balance_after_pay - balance_before) < 1e-6


def test_darf_simulated_mode(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "darf_sim.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    app = create_app()
    app.testing = True
    client = app.test_client()

    res = client.post(
        "/positions/add",
        data={
            "ticker": "CMIGB110",
            "underlying": "CMIG4",
            "qty": "100",
            "entry_price": "1.00",
            "fees": "0",
            "trade_date": "2025-01-20",
            "trade_type": "daytrade",
            "strategy_tag": "covered_call",
            "record_premium": "1",
            "reserve_darf": "1",
            "is_simulated": "1",
        },
    )
    assert res.status_code in (302, 303)

    res = client.get("/darf?mode=simulated&period=2025-01")
    assert res.status_code == 200

    # daytrade: 20% do prêmio (100 -> 20)
    res = client.post("/darf/generate", data={"period": "2025-01", "is_simulated": "1"})
    assert res.status_code in (302, 303)

    rec = darf.get_month(period="2025-01", is_simulated=True)
    assert rec is not None
    assert abs(rec.amount - 20.0) < 1e-6

