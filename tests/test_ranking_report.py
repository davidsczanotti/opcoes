from __future__ import annotations

import math
import sqlite3
import statistics
from typing import Iterable

import pytest

from opcoes.report import generate_report
from opcoes.strategies import get_ranking_context


def _build_prices(base_price: float, log_returns: Iterable[float]) -> list[float]:
    prices = [float(base_price)]
    current = float(base_price)
    for r in log_returns:
        current *= math.exp(r)
        prices.append(current)
    return prices


def _hv_from_prices(prices: list[float]) -> float:
    log_returns = [
        math.log(curr / prev) for prev, curr in zip(prices, prices[1:]) if prev > 0 and curr > 0
    ]
    std_dev = statistics.stdev(log_returns)
    return std_dev * math.sqrt(252) * 100.0


def _setup_db(db_path: str) -> float:
    """Cria um banco isolado com snapshots sintéticos para testar o ranking."""

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Tabela principal de opções com as colunas consumidas em report.py
    cur.execute(
        """
        CREATE TABLE option_snapshots (
            snapshot_date TEXT NOT NULL,
            ticker TEXT,
            underlying TEXT,
            option_type TEXT,
            "score_total" TEXT,
            "trend_flag" TEXT,
            "underlying_price_date" TEXT,
            "dias_uteis" TEXT,
            "Status_Moneyness" TEXT,
            "Status_Liquidez" TEXT,
            "Status_2x" TEXT,
            "iv_score" TEXT,
            "em2x_score" TEXT,
            "delta" TEXT,
            "ultimo" TEXT,
            "underlying_price" TEXT,
            "vencimento" TEXT,
            "strike" TEXT,
            "%_Alta_p_2x" TEXT,
            "custo_pct" TEXT,
            "intrinsic_value" TEXT,
            "extrinsic_value" TEXT,
            "extrinsic_pct_spot" TEXT,
            "breakeven_price" TEXT,
            "breakeven_dist_pct" TEXT,
            "vol_fluxo_5d" TEXT,
            "num_fluxo_5d" TEXT,
            "iv_rank_180d" TEXT,
            "vol_impl_perc" TEXT,
            "best_bid" TEXT,
            "best_ask" TEXT,
            "spread_pct" TEXT,
            "preco_teorico" TEXT,
            "distorcao_preco_pct" TEXT,
            "distorcao_flag" TEXT,
            "illiquidez_flag" TEXT,
            "Status_Remoto" TEXT,
            "prob_itm_pct" TEXT,
            "prob_itm_delta_pct" TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE underlying_snapshots (
            underlying TEXT,
            snapshot_date TEXT,
            price REAL
        )
        """
    )

    # Datas de snapshots e um registro "filler" para contar snapshot_days na recorrência.
    dates = [f"2025-11-{day:02d}" for day in range(19, 29)]  # 10 snapshots
    for d in dates:
        cur.execute(
            """
            INSERT INTO option_snapshots (snapshot_date, ticker, underlying, score_total, trend_flag)
            VALUES (?, 'FILL', 'FILL3', '5.0', '1')
            """,
            (d,),
        )

    # Ticker WEGEF425 apenas no snapshot mais recente: deve cair em "teóricas".
    cur.execute(
        """
        INSERT INTO option_snapshots (
            snapshot_date, ticker, underlying, score_total, trend_flag,
            underlying_price_date, dias_uteis, "Status_Moneyness", "Status_Liquidez",
            "delta", "ultimo", "underlying_price", "%_Alta_p_2x", "custo_pct",
            "extrinsic_pct_spot", "breakeven_price", "breakeven_dist_pct",
            "vol_impl_perc", "best_ask", "preco_teorico", "iv_rank_180d",
            "vol_fluxo_5d", "em2x_score", "Status_Remoto"
        )
        VALUES (
            '2025-11-28', 'WEGEF425', 'WEGE3', '8.03', '1',
            '2025-11-28', '40', 'ITM', '', '0,70', '5,94', '22,90', '10,30', '8,28',
            '4,41', '4,41', '46,20', '21,60', NULL, '3,66', '', '1,73', '2', ''
        )
        """
    )

    # Recorrências: SMAL11 em 5 dos 10 snapshots, VALED703 em 3 dos 10.
    for d in ["2025-11-20", "2025-11-22", "2025-11-24", "2025-11-26", "2025-11-27"]:
        cur.execute(
            """
            INSERT INTO option_snapshots (snapshot_date, ticker, underlying, score_total, trend_flag, "%_Alta_p_2x")
            VALUES (?, 'SMAL11', 'SMAL11', '8.58', '1', '10,20')
            """,
            (d,),
        )
    for d in ["2025-11-20", "2025-11-21", "2025-11-24"]:
        cur.execute(
            """
            INSERT INTO option_snapshots (snapshot_date, ticker, underlying, score_total, trend_flag, "%_Alta_p_2x")
            VALUES (?, 'VALED703', 'VALE3', '8.00', '1', '5,10')
            """,
            (d,),
        )

    # Preços diários do underlying WEGE3 para HV; 10 pontos para satisfazer min_obs (window 21d).
    log_returns = [0.022, 0.0, -0.01, 0.0147, 0.025, -0.015, 0.012, -0.004, 0.008]
    prices = _build_prices(20.0, log_returns)
    for day, price in zip(range(19, 29), prices):
        cur.execute(
            """
            INSERT INTO underlying_snapshots (underlying, snapshot_date, price)
            VALUES ('WEGE3', ?, ?)
            """,
            (f"2025-11-{day:02d}", price),
        )

    conn.commit()
    conn.close()
    return _hv_from_prices(prices)


@pytest.mark.parametrize("hv_days", [21])
def test_generate_report_computes_iv_and_recurring(monkeypatch, tmp_path, hv_days: int) -> None:
    db_path = tmp_path / "opcoes.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))

    expected_hv = _setup_db(str(db_path))

    data = generate_report(
        min_score=8,
        limit=30,
        recurring_days=30,
        recurring_limit=15,
        hv_days=hv_days,
    )

    # Snapshot e oportunidades teóricas (sem ask visível).
    assert data.snapshot_date == "2025-11-28"
    assert not data.opportunities  # nenhum ask -> tudo vira lista teórica
    assert len(data.theoretical_opportunities) == 1

    theo = data.theoretical_opportunities[0]
    assert theo["ticker"] == "WEGEF425"
    assert theo["preco_teorico"] == pytest.approx(3.66)
    assert theo["preco_max_10_pct"] == pytest.approx(3.66 * 1.10)
    assert theo["hv_21d"] == pytest.approx(expected_hv)
    assert theo["iv_hv_spread"] == pytest.approx(theo["vol_impl_perc"] - expected_hv)
    assert theo["desconto_teorico_pct"] is None  # sem ask, não calcula desconto

    # Para oportunidades teóricas, a distorção de preço deve usar Último vs preço justo.
    expected_dist = (theo["ultimo"] - theo["preco_teorico"]) / theo["preco_teorico"] * 100.0
    assert theo["distorcao_preco_pct"] == pytest.approx(expected_dist)

    # Recorrentes: presença = hits / total de snapshots na janela.
    rec_map = {r["ticker"]: r for r in data.recurring_opportunities}
    assert rec_map["SMAL11"]["hits"] == 5
    assert rec_map["SMAL11"]["presence_pct"] == pytest.approx(5 / 10 * 100)
    assert rec_map["VALED703"]["hits"] == 3
    assert rec_map["VALED703"]["presence_pct"] == pytest.approx(3 / 10 * 100)

    # Segmentação: WEGEF425 é ITM, então cai em "carteira" no contexto de ranking.
    ctx = get_ranking_context({})
    assert any(o["ticker"] == "WEGEF425" for o in ctx["segments"]["carteira"])


def test_ranking_context_filters_by_option_type(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "opcoes.db"
    monkeypatch.setenv("OPCOES_DB_PATH", str(db_path))
    _setup_db(str(db_path))

    ctx_calls = get_ranking_context({"option_type": "CALL"})
    assert ctx_calls["data"].theoretical_opportunities  # WEGEF425 é CALL

    ctx_puts = get_ranking_context({"option_type": "PUT"})
    assert not ctx_puts["data"].opportunities
    assert not ctx_puts["data"].theoretical_opportunities
