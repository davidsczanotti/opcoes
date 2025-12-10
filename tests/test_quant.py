import pytest
import math
from opcoes import quant

def test_score_moneyness():
    assert quant.score_moneyness(None) == 0.0
    assert quant.score_moneyness(-5.0) == 2.0  # ITM
    assert quant.score_moneyness(0.0) == 2.0   # ATM/Colada
    assert quant.score_moneyness(10.0) == 1.0  # 10% OTM -> 2.0 * (1 - 0.5) = 1.0
    assert quant.score_moneyness(20.0) == 0.0  # Limite
    assert quant.score_moneyness(25.0) == 0.0  # Fora

def test_score_liquidity():
    # Alta
    assert quant.score_liquidity(30, 50000, "Alta") >= 1.5
    # Média
    assert quant.score_liquidity(5, 5000, "Média") >= 0.8
    # Baixa
    assert quant.score_liquidity(1, 100, "Baixa") < 0.8

def test_score_double_scenario():
    assert quant.score_double_scenario("Dobra com até 20% no ativo") == 2
    assert quant.score_double_scenario("Dobra com 20-40% no ativo") == 1
    assert quant.score_double_scenario("Precisa de 40%+ no ativo") == 0

def test_score_theta():
    assert quant.score_theta(None) == 0.0
    assert quant.score_theta(-0.2) == 1.0 # |theta| < 0.3
    assert quant.score_theta(-1.5) == 0.0 # |theta| >= 1.5
    
def test_score_iv_rank():
    assert quant.score_iv_rank(None) == 0.0
    assert quant.score_iv_rank(30.0) == 2.0 # Core range 10-60 -> 1.0 * 2.0 = 2.0
    # Rank 5: Core 0, Bonus (20-5)/20 * 0.5 = 0.375
    assert quant.score_iv_rank(5.0) == 0.375

def test_black_scholes_call():
    # Exemplo simples: Spot=100, Strike=100, Vol=20%, T=1 ano, r=0
    # d1 = (0 + 0.5*0.04*1) / 0.2 = 0.1
    # d2 = 0.1 - 0.2 = -0.1
    # N(0.1) approx 0.5398
    # N(-0.1) approx 0.4602
    # Price = 100*0.5398 - 100*0.4602 = 7.96 approx
    bs = quant.calculate_black_scholes_call(100, 100, 0.2, 1.0)
    assert 7.9 < bs < 8.0

def test_calculate_probability_itm():
    # ATM, 50% chance approx (ignoring drift/risk-neutral shift mostly for short term)
    prob = quant.calculate_probability_itm(100, 100, 0.2, 1.0)
    assert 0.4 < prob < 0.6 

def test_calculate_weighted_score():
    score = quant.calculate_weighted_score(
        moneyness_score=2.0, # max
        prob_itm_pct=50.0,   # good
        prob_itm_delta_pct=None,
        extrinsic_pct_spot=2.0, # low extrinsic
        liquidity_score=2.0, # max
        iv_score=2.0,
        theta_score=1.0,
        em2x_score=2.0,
        double_score=2.0,
        status_remote=""
    )
    assert score > 5.0 # Deve ser alto
