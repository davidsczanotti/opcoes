from __future__ import annotations

from opcoes.scraper.run import (
    _filter_processed_for_target,
    _resume_symbols_match,
    _symbols_signature,
)


def test_symbols_signature_ignores_order_and_duplicates() -> None:
    a = _symbols_signature(["BBAS3", "ABEV3", "BBAS3"])
    b = _symbols_signature(["ABEV3", "BBAS3"])
    assert a == b


def test_resume_symbols_match_by_signature() -> None:
    signature = _symbols_signature(["ABEV3", "BBAS3"])
    assert _resume_symbols_match(["BBAS3", "ABEV3"], ["XPTO4"], signature)
    assert not _resume_symbols_match(["ABEV3"], ["ABEV3"], signature)


def test_resume_symbols_match_without_signature_uses_set() -> None:
    assert _resume_symbols_match(["ABEV3", "BBAS3"], ["BBAS3", "ABEV3"], None)
    assert not _resume_symbols_match(["ABEV3"], ["ABEV3", "BBAS3"], None)


def test_filter_processed_for_target_removes_unknown_and_keeps_order() -> None:
    filtered = _filter_processed_for_target(
        ["abev3", "XXXX4", "BBAS3", "BBAS3", ""],
        ["BBAS3", "ABEV3"],
    )
    assert filtered == ["ABEV3", "BBAS3"]

