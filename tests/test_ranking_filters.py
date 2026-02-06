from opcoes.report import ReportData
from opcoes.strategies.ranking import calculate_ranking_strategy


def _base_report():
    return ReportData(
        snapshot_date="2025-12-11",
        opportunities=[
            {"ticker": "ABCD1", "underlying": "ABCD3", "option_type": "CALL", "delta": 0.8},
            {"ticker": "WXYZ1", "underlying": "WXYZ3", "option_type": "PUT", "delta": 0.2},
        ],
        theoretical_opportunities=[
            {"ticker": "ABCD2", "underlying": "ABCD3", "option_type": "CALL"},
            {"ticker": "WXYZ2", "underlying": "WXYZ3", "option_type": "PUT"},
        ],
        rational_opportunities=[
            {"ticker": "ABCD3", "underlying": "ABCD3", "option_type": "CALL"},
            {"ticker": "WXYZ3", "underlying": "WXYZ3", "option_type": "PUT"},
        ],
        lottery_opportunities=[
            {"ticker": "ABCD4", "underlying": "ABCD3", "option_type": "CALL"},
            {"ticker": "WXYZ4", "underlying": "WXYZ3", "option_type": "PUT"},
        ],
        positions=[
            {"id": 1, "is_simulated": False, "entry_price": 1.0, "qty": 1, "open_qty": 1},
            {"id": 2, "is_simulated": True, "entry_price": 1.0, "qty": 1, "open_qty": 1},
        ],
        alerts=[{"position": {"id": 1}, "reasons": ["dummy"]}],
        recurring_opportunities=[
            {"ticker": "ABCD5", "underlying": "ABCD3", "option_type": "CALL"},
            {"ticker": "WXYZ5", "underlying": "WXYZ3", "option_type": "PUT"},
        ],
        recurring_window_start="2025-12-01",
        recurring_window_days=10,
        recurring_snapshot_days=5,
        hv_window_days=21,
    )


def test_option_type_filter_call():
    data = _base_report()
    ctx = calculate_ranking_strategy(
        data=data,
        min_score=1,
        limit=10,
        recurring_days=30,
        recurring_limit=10,
        underlying_filter="",
        option_type_filter="CALL",
    )

    assert all(o["option_type"] == "CALL" for o in ctx["data"].opportunities)
    assert all(o["option_type"] == "CALL" for o in ctx["data"].theoretical_opportunities)
    assert all(o["option_type"] == "CALL" for o in ctx["data"].rational_opportunities)
    assert all(o["option_type"] == "CALL" for o in ctx["data"].lottery_opportunities)
    assert all(o["option_type"] == "CALL" for o in ctx["data"].recurring_opportunities)
    assert ctx["positions_real"] and ctx["positions_simulated"]  # split worked


def test_underlying_filter_contains_substring():
    data = _base_report()
    ctx = calculate_ranking_strategy(
        data=data,
        min_score=1,
        limit=10,
        recurring_days=30,
        recurring_limit=10,
        underlying_filter="ABCD",
        option_type_filter="",
    )

    # Only ABCD* should remain in all opportunity lists.
    assert {o["ticker"] for o in ctx["data"].opportunities} == {"ABCD1"}
    assert {o["ticker"] for o in ctx["data"].theoretical_opportunities} == {"ABCD2"}
    assert {o["ticker"] for o in ctx["data"].rational_opportunities} == {"ABCD3"}
    assert {o["ticker"] for o in ctx["data"].lottery_opportunities} == {"ABCD4"}
    assert {o["ticker"] for o in ctx["data"].recurring_opportunities} == {"ABCD5"}


def test_segment_uses_absolute_delta_for_puts():
    data = _base_report()
    data.opportunities = [
        {"ticker": "PUTITM1", "underlying": "WXYZ3", "option_type": "PUT", "delta": -0.75},
    ]
    data.theoretical_opportunities = []
    data.rational_opportunities = []
    data.lottery_opportunities = []
    data.recurring_opportunities = []

    ctx = calculate_ranking_strategy(
        data=data,
        min_score=1,
        limit=10,
        recurring_days=30,
        recurring_limit=10,
        underlying_filter="",
        option_type_filter="",
    )

    assert [o["ticker"] for o in ctx["segments"]["carteira"]] == ["PUTITM1"]
    assert ctx["segments"]["alavancagem"] == []
    assert ctx["segments"]["aposta"] == []
