"""Teste opcional (rede) para validar captura de bid/ask em vencimentos longos."""

from __future__ import annotations

import os

import pytest

from opcoes.scraper.far_expirations import fetch_far_expiration_quotes


RUN_E2E = os.getenv("RUN_E2E_TESTS") == "1"


@pytest.mark.skipif(not RUN_E2E, reason="Set RUN_E2E_TESTS=1 to run network-dependent test")
def test_far_expirations_has_bid_or_ask() -> None:
    quotes = fetch_far_expiration_quotes()
    assert quotes, "Nenhuma quote retornada de vencimentos longos"

    has_liquidity = any(
        (q.get("best_bid") is not None) or (q.get("best_ask") is not None) for q in quotes.values()
    )
    assert has_liquidity, "Nenhum bid/ask foi capturado no book de vencimentos longos"
