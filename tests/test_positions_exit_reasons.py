from __future__ import annotations

import pytest

from opcoes import portfolio
from opcoes.scraper.snapshots import SnapshotDB
from opcoes.web import create_app


def _ensure_snapshot_tables(db_path) -> None:
    snap = SnapshotDB(db_path)
    snap.close()


def test_positions_exit_reason_dropdown_includes_new_options(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "positions.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    portfolio.add_position(
        ticker="WIZCB103",
        underlying="WICZ3",
        trade_date="2026-02-05",
        qty=300,
        entry_price=0.23,
        side="short",
        strategy_tag="covered_call",
    )
    _ensure_snapshot_tables(db_path)

    app = create_app()
    app.testing = True
    client = app.test_client()

    response = client.get("/positions")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'value="recompra_encerramento"' in html
    assert 'value="rolagem"' in html
    assert 'value="exercicio"' in html
    assert 'value="vencimento_sem_valor"' in html
    assert 'value="ajuste_manual"' in html


@pytest.mark.parametrize(
    ("legacy_reason", "canonical_value"),
    [
        ("Exercício", "exercicio"),
        ("Expiração", "vencimento_sem_valor"),
    ],
)
def test_positions_exit_reason_legacy_values_are_mapped(monkeypatch, tmp_path, legacy_reason: str, canonical_value: str) -> None:
    db_path = tmp_path / f"positions_{canonical_value}.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    portfolio.add_position(
        ticker="WIZCB103",
        underlying="WICZ3",
        trade_date="2026-02-05",
        qty=300,
        entry_price=0.23,
        side="short",
        strategy_tag="covered_call",
        exit_reason=legacy_reason,
    )
    _ensure_snapshot_tables(db_path)

    app = create_app()
    app.testing = True
    client = app.test_client()

    response = client.get("/positions")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert f'value="{canonical_value}" selected' in html
