from __future__ import annotations

from opcoes.scraper.run import _apply_execution_penalties


def _ptbr_to_float(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def test_execution_penalty_hits_watchlist_when_no_ask_or_spread() -> None:
    rows = [
        {
            "score_total": "8,50",
            "best_ask": "",
            "best_bid": "",
            "spread_pct": "",
            "data_hora": "01/11/2025",
            "num_neg": "1",
            "vol_financeiro": "300,00",
            "titulares": "1",
            "lancadores": "1",
        }
    ]
    _apply_execution_penalties(rows, "2025-12-16")
    assert _ptbr_to_float(rows[0]["score_total"]) < 2.0


def test_execution_penalty_keeps_score_when_book_is_complete() -> None:
    rows = [
        {
            "score_total": "8,50",
            "best_ask": "0,50",
            "best_bid": "0,45",
            "spread_pct": "10,00",
            "data_hora": "16/12/2025",
            "num_neg": "50",
            "vol_financeiro": "100000,00",
            "titulares": "50",
            "lancadores": "50",
        }
    ]
    _apply_execution_penalties(rows, "2025-12-16")
    assert rows[0]["score_total"] == "8,50"


def test_execution_penalty_hits_watchlist_when_no_spread() -> None:
    rows = [
        {
            "score_total": "8,50",
            "best_ask": "0,50",
            "best_bid": "",
            "spread_pct": "",
            "data_hora": "16/12/2025",
            "num_neg": "50",
            "vol_financeiro": "100000,00",
            "titulares": "50",
            "lancadores": "50",
        }
    ]
    _apply_execution_penalties(rows, "2025-12-16")
    assert _ptbr_to_float(rows[0]["score_total"]) < 8.5

