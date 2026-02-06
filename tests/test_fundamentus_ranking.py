from opcoes.fundamentus import (
    FundamentusFilterConfig,
    fetch_approved_ranking,
    fetch_filter_run,
    save_signals,
)


def _signal(papel: str, status: str) -> dict:
    return {
        "papel": papel,
        "status": status,
        "failed_step": None,
        "failed_rule": None,
        "failed_value": None,
        "reason": status,
    }


def test_fetch_approved_ranking_total_and_window(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "opcoes.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    cfg = FundamentusFilterConfig()
    save_signals(
        [_signal("ABEV3", "approved"), _signal("ITUB4", "rejected")],
        snapshot_date="2026-01-01",
        cfg=cfg,
    )
    save_signals(
        [_signal("ABEV3", "approved"), _signal("ITUB4", "approved")],
        snapshot_date="2026-01-02",
        cfg=cfg,
    )
    save_signals(
        [_signal("ABEV3", "rejected"), _signal("ITUB4", "approved")],
        snapshot_date="2026-01-10",
        cfg=cfg,
    )

    total = fetch_approved_ranking(snapshot_date="2026-01-10", limit=10)
    assert total["end_date"] == "2026-01-10"
    assert [row["papel"] for row in total["rows"]] == ["ABEV3", "ITUB4"]
    assert [row["approvals"] for row in total["rows"]] == [2, 2]

    window = fetch_approved_ranking(snapshot_date="2026-01-10", window_days=7, limit=10)
    assert window["start_date"] == "2026-01-04"
    assert window["end_date"] == "2026-01-10"
    assert [row["papel"] for row in window["rows"]] == ["ITUB4"]
    assert [row["approvals"] for row in window["rows"]] == [1]

    run = fetch_filter_run(snapshot_date="2026-01-10")
    assert run is not None
    assert run["snapshot_date"] == "2026-01-10"
    assert run["liq_2m_min"] == cfg.liq_2m_min
    assert run["div_yield_min"] == cfg.div_yield_min
