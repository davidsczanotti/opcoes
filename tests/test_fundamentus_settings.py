from __future__ import annotations

from opcoes.settings import get_fundamentus_settings, update_fundamentus_settings


def test_fundamentus_settings_roundtrip(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "opcoes.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    update_fundamentus_settings(
        target_yield_pct=9.5,
        put_distance_limit_pct=12.0,
        put_min_premium_pct=0.8,
        put_target_monthly_yield_pct=1.2,
        put_min_score=5.5,
    )
    cfg = get_fundamentus_settings()

    assert cfg.target_yield_pct == 9.5
    assert cfg.put_distance_limit_pct == 12.0
    assert cfg.put_min_premium_pct == 0.8
    assert cfg.put_target_monthly_yield_pct == 1.2
    assert cfg.put_min_score == 5.5
