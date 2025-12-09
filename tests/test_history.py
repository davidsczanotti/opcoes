from __future__ import annotations

import datetime as dt
import json
import sqlite3

from opcoes.history import cleanup_history, list_decisions, record_decision, record_ranking_entries
from opcoes.scraper.snapshots import SnapshotDB
from opcoes.scraper.storage import CSV_FIELDS


def _build_sample_row() -> dict:
    row = {col: "" for col in CSV_FIELDS}
    row.update(
        {
            "ticker": "TESTA1",
            "underlying": "TEST3",
            "option_type": "CALL",
            "vencimento": "02/01/2025",
            "dias_uteis": "10",
            "strike": "10,00",
            "best_ask": "0,50",
            "best_bid": "0,45",
            "preco_teorico": "0,52",
            "score_total": "8,50",
            "vol_impl_perc": "35,0",
            "iv_rank_180d": "50,0",
            "underlying_price": "11,00",
            "underlying_price_date": "2025-01-01",
        }
    )
    return row


def test_record_ranking_and_decision(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "opcoes.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    # Cria snapshot sintético
    snap = SnapshotDB(db_path)
    sample_row = _build_sample_row()
    snap.record_options("2025-01-01", [sample_row])
    snap.close()

    # Persiste ranking
    record_ranking_entries(
        "2025-01-01",
        categories={"top": [sample_row], "teorica": []},
        params={"min_score": 8, "limit": 20},
    )

    conn = sqlite3.connect(db_path)
    count_rank = conn.execute("SELECT COUNT(*) FROM ranking_entries").fetchone()[0]
    assert count_rank == 1
    conn.close()

    # Grava decisão
    decision_id = record_decision("TESTA1", snapshot_date="2025-01-01", notes="compra teste")
    assert decision_id is not None
    decisions = list_decisions(limit=5)
    assert decisions and decisions[0]["ticker"] == "TESTA1"
    raw = json.loads(decisions[0]["raw_row"])
    assert raw["ticker"] == "TESTA1"

    # Limpeza: com cutoff em 2025-02-01 remove ranking de 2025-01-01
    removed = cleanup_history(retention_days=1, today=dt.date(2025, 2, 1), purge_snapshots=False)
    assert removed["ranking_entries"] >= 1
    conn = sqlite3.connect(db_path)
    count_rank_after = conn.execute("SELECT COUNT(*) FROM ranking_entries").fetchone()[0]
    conn.close()
    assert count_rank_after == 0
