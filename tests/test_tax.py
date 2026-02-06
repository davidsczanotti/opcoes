from __future__ import annotations

from opcoes import portfolio
from opcoes.tax import compute_tax


def _create_closed_position(*, is_simulated: bool, exit_price: float) -> int:
    pos_id = portfolio.add_position(
        ticker="ABCDM100",
        underlying="ABCD3",
        trade_date="2026-01-05",
        qty=10,
        entry_price=10.0,
        fees=0.0,
        trade_type="swing",
        is_simulated=is_simulated,
    )
    portfolio.close_position(
        position_id=pos_id,
        exit_date="2026-01-20",
        exit_price=exit_price,
    )
    return pos_id


def test_compute_tax_filters_real_and_simulated(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "tax.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    _create_closed_position(is_simulated=False, exit_price=12.0)  # +20
    _create_closed_position(is_simulated=True, exit_price=13.0)   # +30

    real = compute_tax(month=1, year=2026, is_simulated=False)
    simulated = compute_tax(month=1, year=2026, is_simulated=True)
    all_modes = compute_tax(month=1, year=2026, is_simulated=None)

    assert real.swing_net == 20.0
    assert real.swing_ir == 3.0
    assert simulated.swing_net == 30.0
    assert simulated.swing_ir == 4.5
    assert all_modes.swing_net == 50.0
    assert all_modes.swing_ir == 7.5
