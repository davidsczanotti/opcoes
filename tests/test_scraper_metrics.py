from __future__ import annotations

import sqlite3
from pathlib import Path

from opcoes.scraper.activity import FlowStore
from opcoes.scraper.ivrank import IVRankStore
from opcoes.scraper.run import _history_store_path, _recalculate_snapshot_metrics


def _ptbr_to_float(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def test_history_store_path_follows_db_parent(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "custom-data" / "opcoes_snapshots.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))
    assert _history_store_path("iv_history.db") == db_path.parent / "iv_history.db"
    assert _history_store_path("flow_history.db") == db_path.parent / "flow_history.db"


def test_recalculate_snapshot_metrics_records_iv_and_flow_on_snapshot_date(tmp_path: Path) -> None:
    iv_path = tmp_path / "iv_history.db"
    flow_path = tmp_path / "flow_history.db"
    iv_store = IVRankStore(iv_path)
    flow_store = FlowStore(flow_path)
    try:
        iv_store.record_many(
            [
                ("PETR4", "20/02/2026", "2026-01-29", 20.0),
                ("PETR4", "20/02/2026", "2026-01-30", 30.0),
                ("PETR4", "20/02/2026", "2026-02-02", 40.0),
                ("PETR4", "20/02/2026", "2026-02-03", 50.0),
                ("PETR4", "20/02/2026", "2026-02-04", 60.0),
            ]
        )
        flow_store.record_many(
            [
                ("PETRA123", "2026-01-30", 1000.0, 10.0),
                ("PETRA123", "2026-02-03", 1200.0, 12.0),
            ]
        )

        rows = [
            {
                "ticker": "PETRA123",
                "underlying": "PETR4",
                "vencimento": "20/02/2026",
                "vol_impl_perc": "25,0",
                "vol_financeiro": "1500,00",
                "num_neg": "15",
                "moneyness_score": "2,00",
                "prob_itm_pct": "55,0",
                "prob_itm_delta_pct": "45,0",
                "extrinsic_pct_spot": "2,00",
                "liquidez_score": "2,00",
                "theta_score": "1,00",
                "em2x_score": "2",
                "dobro_score": "2",
                "Status_Remoto": "",
                "score_total": "0,00",
            }
        ]

        _recalculate_snapshot_metrics(
            rows,
            snapshot_date="2026-02-05",
            iv_store=iv_store,
            flow_store=flow_store,
        )

        row = rows[0]
        assert row["iv_rank_180d"] != ""
        assert row["iv_score"] != ""
        assert row["vol_fluxo_5d"] != ""
        assert row["num_fluxo_5d"] != ""
        assert _ptbr_to_float(row["score_total"]) > 0.0

        with sqlite3.connect(iv_path) as conn:
            iv_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM iv_history
                WHERE underlying = ? AND vencimento = ? AND snapshot_date = ?
                """,
                ("PETR4", "20/02/2026", "2026-02-05"),
            ).fetchone()[0]
        assert iv_count == 1

        with sqlite3.connect(flow_path) as conn:
            flow_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM flow_history
                WHERE ticker = ? AND snapshot_date = ?
                """,
                ("PETRA123", "2026-02-05"),
            ).fetchone()[0]
        assert flow_count == 1
    finally:
        iv_store.close()
        flow_store.close()

