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


def test_black_scholes_put_parity():
    call = quant.calculate_black_scholes_call(100, 100, 0.2, 1.0)
    put = quant.calculate_black_scholes_put(100, 100, 0.2, 1.0)
    assert abs(call - put) < 1e-6

def test_calculate_probability_itm():
    # ATM, 50% chance approx (ignoring drift/risk-neutral shift mostly for short term)
    prob = quant.calculate_probability_itm(100, 100, 0.2, 1.0)
    assert 0.4 < prob < 0.6 


def test_calculate_probability_itm_put_complements_call():
    prob_call = quant.calculate_probability_itm(100, 100, 20.0, 252, option_type="CALL")
    prob_put = quant.calculate_probability_itm(100, 100, 20.0, 252, option_type="PUT")
    assert prob_call is not None
    assert prob_put is not None
    assert prob_put > prob_call
    assert abs((prob_call + prob_put) - 1.0) < 1e-6


def test_calculate_probability_move_put_direction():
    prob_up = quant.calculate_probability_move(100, 10.0, 20.0, 252, option_type="CALL")
    prob_down = quant.calculate_probability_move(100, 10.0, 20.0, 252, option_type="PUT")
    assert prob_up is not None
    assert prob_down is not None
    assert 0.0 < prob_up < 1.0
    assert 0.0 < prob_down < 1.0
    assert prob_down > prob_up


def test_calculate_intrinsic_extrinsic_put():
    intrinsic, extrinsic = quant.calculate_intrinsic_extrinsic(15.0, 100.0, 90.0, option_type="PUT")
    assert intrinsic == pytest.approx(10.0)
    assert extrinsic == pytest.approx(5.0)


def test_calculate_breakeven_put():
    be_price, dist = quant.calculate_breakeven(100.0, 100.0, 5.0, option_type="PUT")
    assert be_price == pytest.approx(95.0)
    assert dist == pytest.approx(-5.0)

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


def test_calculate_weighted_score_put_metrics():
    spot = 100.0
    strike = 110.0
    vol = 20.0
    days = 252.0
    option_price = 12.0

    intrinsic, extrinsic = quant.calculate_intrinsic_extrinsic(
        option_price, strike, spot, option_type="PUT"
    )
    extrinsic_pct = quant.calculate_extrinsic_pct(extrinsic or 0.0, spot)
    prob_itm = quant.calculate_probability_itm(spot, strike, vol, days, option_type="PUT")

    score = quant.calculate_weighted_score(
        moneyness_score=2.0,
        prob_itm_pct=(prob_itm or 0.0) * 100.0,
        prob_itm_delta_pct=None,
        extrinsic_pct_spot=extrinsic_pct,
        liquidity_score=2.0,
        iv_score=2.0,
        theta_score=1.0,
        em2x_score=2.0,
        double_score=2.0,
        status_remote="",
    )
    assert score > 6.0
