from __future__ import annotations

from pathlib import Path

from opcoes.scraper.checkpoint import ScrapeCheckpointStore, default_checkpoint_db_path
from opcoes.scraper.run import _symbols_signature


def _sample_row(*, ticker: str, underlying: str) -> dict:
    return {
        "ticker": ticker,
        "underlying": underlying,
        "option_type": "CALL",
        "vencimento": "20/03/2026",
        "strike": "10,00",
        "vol_impl_perc": "25,0",
    }


def test_checkpoint_roundtrip_resume_state(tmp_path: Path) -> None:
    output_csv = tmp_path / "opcoes_latest.csv"
    checkpoint_path = default_checkpoint_db_path(output_csv)
    store = ScrapeCheckpointStore(checkpoint_path)
    try:
        symbols = ["ABEV3", "BBAS3"]
        signature = _symbols_signature(symbols)
        initial = store.prepare(
            output_csv=output_csv,
            target_symbols=symbols,
            symbols_signature=signature,
        )
        assert initial.processed_symbols == []
        assert initial.snapshot_rows == []
        assert initial.snapshot_date is None

        store.mark_symbol_running(output_csv=output_csv, symbol="ABEV3")
        store.mark_symbol_success(
            output_csv=output_csv,
            symbol="ABEV3",
            rows=[_sample_row(ticker="ABEVA123", underlying="ABEV3")],
            snapshot_date="2026-02-06",
        )

        resumed = store.prepare(
            output_csv=output_csv,
            target_symbols=symbols,
            symbols_signature=signature,
        )
        assert resumed.processed_symbols == ["ABEV3"]
        assert resumed.snapshot_date == "2026-02-06"
        assert len(resumed.snapshot_rows) == 1
        assert resumed.snapshot_rows[0]["ticker"] == "ABEVA123"
    finally:
        store.close()


def test_checkpoint_reconcile_changed_symbol_list(tmp_path: Path) -> None:
    output_csv = tmp_path / "opcoes_latest.csv"
    checkpoint_path = default_checkpoint_db_path(output_csv)
    store = ScrapeCheckpointStore(checkpoint_path)
    try:
        base_symbols = ["ABEV3", "BBAS3"]
        store.prepare(
            output_csv=output_csv,
            target_symbols=base_symbols,
            symbols_signature=_symbols_signature(base_symbols),
        )
        store.mark_symbol_success(
            output_csv=output_csv,
            symbol="ABEV3",
            rows=[_sample_row(ticker="ABEVA123", underlying="ABEV3")],
            snapshot_date="2026-02-06",
        )
        store.mark_symbol_success(
            output_csv=output_csv,
            symbol="BBAS3",
            rows=[_sample_row(ticker="BBASA123", underlying="BBAS3")],
            snapshot_date="2026-02-06",
        )

        new_symbols = ["ABEV3", "PETR4"]
        reconciled = store.prepare(
            output_csv=output_csv,
            target_symbols=new_symbols,
            symbols_signature=_symbols_signature(new_symbols),
        )
        assert reconciled.processed_symbols == ["ABEV3"]
        assert len(reconciled.snapshot_rows) == 1
        assert reconciled.snapshot_rows[0]["ticker"] == "ABEVA123"
        counts = store.status_counts(output_csv=output_csv, target_symbols=new_symbols)
        assert counts["total"] == 2
        assert counts["done"] == 1
        assert counts["pending"] == 1
    finally:
        store.close()


def test_checkpoint_clear_after_complete(tmp_path: Path) -> None:
    output_csv = tmp_path / "opcoes_latest.csv"
    checkpoint_path = default_checkpoint_db_path(output_csv)
    store = ScrapeCheckpointStore(checkpoint_path)
    try:
        symbols = ["ABEV3"]
        signature = _symbols_signature(symbols)
        store.prepare(
            output_csv=output_csv,
            target_symbols=symbols,
            symbols_signature=signature,
        )
        store.mark_symbol_success(
            output_csv=output_csv,
            symbol="ABEV3",
            rows=[_sample_row(ticker="ABEVA123", underlying="ABEV3")],
            snapshot_date="2026-02-06",
        )
        assert store.is_complete(output_csv=output_csv, target_symbols=symbols)
        store.clear(output_csv=output_csv)

        fresh = store.prepare(
            output_csv=output_csv,
            target_symbols=symbols,
            symbols_signature=signature,
        )
        assert fresh.processed_symbols == []
        assert fresh.snapshot_rows == []
    finally:
        store.close()

