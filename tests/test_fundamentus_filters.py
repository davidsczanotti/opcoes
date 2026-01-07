from opcoes.fundamentus import FundamentusFilterConfig, evaluate_row, evaluate_rows


def _base_row() -> dict:
    return {
        "papel": "TEST3",
        "liquidez_2m": 1_000_000.0,
        "div_bruta_patrim": 2.0,
        "cresc_rec_5a": 0.0,
        "div_yield": 6.0,
        "roe": 15.0,
        "margem_liquida": 10.0,
    }


def test_evaluate_row_approves_at_thresholds() -> None:
    row = _base_row()
    result = evaluate_row(row, FundamentusFilterConfig())
    assert result["status"] == "approved"
    assert result["failed_step"] is None
    assert result["reason"] == "approved"


def test_evaluate_row_allows_zero_margem_liquida() -> None:
    row = _base_row()
    row["margem_liquida"] = 0.0
    result = evaluate_row(row, FundamentusFilterConfig())
    assert result["status"] == "approved"


def test_evaluate_row_rejects_zero_margem_liquida_when_disabled() -> None:
    row = _base_row()
    row["margem_liquida"] = 0.0
    cfg = FundamentusFilterConfig(margem_liquida_allow_zero=False)
    result = evaluate_row(row, cfg)
    assert result["status"] == "rejected"
    assert result["failed_step"] == 6
    assert result["reason"] == "margem_liquida_out_of_rule"


def test_evaluate_row_rejects_margem_liquida_below_min() -> None:
    row = _base_row()
    row["margem_liquida"] = 9.0
    result = evaluate_row(row, FundamentusFilterConfig())
    assert result["status"] == "rejected"
    assert result["failed_step"] == 6
    assert result["reason"] == "margem_liquida_out_of_rule"


def test_evaluate_row_rejects_missing_liquidez() -> None:
    row = _base_row()
    row["liquidez_2m"] = None
    result = evaluate_row(row, FundamentusFilterConfig())
    assert result["status"] == "rejected"
    assert result["failed_step"] == 1
    assert result["reason"] == "missing_liquidez_2m"


def test_evaluate_row_stops_at_first_failure() -> None:
    row = _base_row()
    row["liquidez_2m"] = 500_000.0
    row["div_bruta_patrim"] = 5.0
    result = evaluate_row(row, FundamentusFilterConfig())
    assert result["status"] == "rejected"
    assert result["failed_step"] == 1
    assert result["reason"] == "liquidez_2m_below_min"


def test_evaluate_rows_skips_missing_papel() -> None:
    rows = [{"papel": ""}, _base_row()]
    result = evaluate_rows(rows)
    assert len(result) == 1
    assert result[0]["papel"] == "TEST3"
