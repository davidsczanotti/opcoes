from __future__ import annotations

import datetime as dt

from opcoes.strategies.fundamentus import _build_put_opportunities


def test_build_put_opportunities_uses_bid_first_and_scores() -> None:
    fundamentals = [
        {
            "papel": "PETR4",
            "cotacao": 37.0,
            "div_yield": 11.0,
            "roe": 20.0,
            "margem_liquida": 16.0,
            "div_bruta_patrim": 0.6,
        }
    ]
    option_rows = [
        {
            "underlying": "PETR4",
            "ticker": "PETRV36",
            "vencimento": "16/10/2026",
            "strike": "36,00",
            "ultimo": "1,50",
            "best_bid": "1,00",
        }
    ]

    rows = _build_put_opportunities(
        fundamentals=fundamentals,
        option_rows=option_rows,
        target_vencimento=dt.date(2026, 10, 16),
        distance_limit_pct=15.0,
        min_premium_pct=0.1,
        target_monthly_yield_pct=1.0,
        min_score=0.0,
        asof_date=dt.date(2026, 2, 5),
        price_map={},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["premium_source"] == "best_bid"
    assert row["preco_ref"] == 1.0
    assert row["put_score"] > 0
    assert row["premio_mensal_pct"] is not None
    assert row["execution_note"]


def test_build_put_opportunities_respects_minimum_filters() -> None:
    fundamentals = [{"papel": "ABEV3", "cotacao": 15.0, "div_yield": 6.0, "roe": 15.0}]
    option_rows = [
        {
            "underlying": "ABEV3",
            "ticker": "ABEVO150",
            "vencimento": "20/03/2026",
            "strike": "14,50",
            "ultimo": "0,05",
            "best_bid": "",
        }
    ]

    rows = _build_put_opportunities(
        fundamentals=fundamentals,
        option_rows=option_rows,
        target_vencimento=dt.date(2026, 3, 20),
        distance_limit_pct=15.0,
        min_premium_pct=1.0,  # exige premio minimo de 1% sobre strike
        target_monthly_yield_pct=1.0,
        min_score=4.0,
        asof_date=dt.date(2026, 2, 5),
        price_map={},
    )

    assert rows == []
