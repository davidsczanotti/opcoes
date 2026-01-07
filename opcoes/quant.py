import math
import statistics
from typing import Dict, List, Optional, Tuple


def _normalize_option_type(option_type: Optional[str]) -> str:
    text = (option_type or "").strip().upper()
    if text in {"CALL", "PUT"}:
        return text
    return "CALL"


def score_moneyness(dist_perc: Optional[float]) -> float:
    """Escala contínua: melhor quanto mais colado/ITM; zera a 20% OTM."""
    if dist_perc is None:
        return 0.0
    if dist_perc <= 0:
        return 2.0
    if dist_perc >= 20.0:
        return 0.0
    return max(0.0, 2.0 * (1.0 - dist_perc / 20.0))


def get_liquidity_status(num_neg: float, vol_fin: float) -> str:
    if num_neg >= 30 or vol_fin >= 50000:
        return "Alta"
    if num_neg >= 5 or vol_fin >= 5000:
        return "Média"
    if num_neg > 0 or vol_fin > 0:
        return "Baixa"
    return ""


def score_liquidity(num_neg: float, vol_fin: float, label: str) -> float:
    """Score contínuo usando log de num_neg e vol_fin; etiqueta mantém compatibilidade."""

    def _scale_log(val: float, lo: float, hi: float) -> float:
        if val <= 0:
            return 0.0
        x = math.log10(val)
        return max(0.0, min(1.0, (x - lo) / (hi - lo)))

    s_num = _scale_log(num_neg, 0.0, 1.7)  # ~1 até 50 negócios
    s_vol = _scale_log(vol_fin, 3.0, 5.0)  # ~1k até 100k R$
    score = (s_num + s_vol) / 2.0 * 2.0  # escala para 0-2

    # Usa label para reforçar casos extremos (por compatibilidade)
    if label == "Alta":
        score = max(score, 1.5)
    elif label == "Média":
        score = max(score, 0.8)
    return min(2.0, score)


def score_double_scenario(label: str) -> int:
    if label == "Dobra com até 20% no ativo":
        return 2
    if label == "Dobra com 20-40% no ativo":
        return 1
    return 0


def calculate_double_upside(option_price: float, delta: float, spot: float) -> Optional[float]:
    if abs(delta) < 1e-4 or spot <= 0 or option_price <= 0:
        return None
    move_abs = option_price / abs(delta)
    if move_abs <= 0:
        return None
    pct = (move_abs / spot) * 100.0
    if not math.isfinite(pct) or pct <= 0:
        return None
    return pct


def get_double_status(pct: float) -> str:
    if pct <= 20:
        return "Dobra com até 20% no ativo"
    if pct <= 40:
        return "Dobra com 20-40% no ativo"
    return "Precisa de 40%+ no ativo"



def get_theta_status(theta_perc: Optional[float]) -> str:
    if theta_perc is None:
        return ""
    abs_theta = abs(theta_perc)
    if abs_theta < 0.5:
        return "Theta baixo"
    if abs_theta < 1.0:
        return "Theta médio"
    return "Theta alto"


def score_theta(theta_perc: Optional[float]) -> float:
    """Escala contínua: melhor para |theta| baixo, saturando em 0.3 e zerando após 1.5."""
    if theta_perc is None:
        return 0.0
    abs_theta = abs(theta_perc)
    best = 0.3
    worst = 1.5
    if abs_theta <= best:
        return 1.0
    if abs_theta >= worst:
        return 0.0
    return max(0.0, 1.0 - (abs_theta - best) / (worst - best))


def score_iv_rank(rank: Optional[float], vol_impl: Optional[float] = None) -> float:
    """IV contínuo com bônus em ranks baixos e penalidade para IV cara."""
    if rank is None:
        return 0.0
    rank = max(0.0, min(100.0, rank))
    # Base trapezoide
    if rank < 5.0:
        core = 0.0
    elif rank < 10.0:
        core = (rank - 5.0) / 5.0
    elif rank <= 60.0:
        core = 1.0
    elif rank < 90.0:
        core = (90.0 - rank) / 10.0
    else:
        core = 0.0
    core = max(0.0, min(core, 1.0)) * 2.0

    # Bônus
    bonus = 0.0
    if rank < 20.0:
        bonus = (20.0 - rank) / 20.0 * 0.5

    # Penalidade
    penalty = 0.0
    if rank > 80.0:
        penalty += (rank - 80.0) / 20.0 * 1.0
    if vol_impl is not None and vol_impl > 120.0:
        penalty += min(1.0, (vol_impl - 120.0) / 80.0)

    score = core + bonus - penalty
    return max(-1.0, min(3.0, score))


def calculate_em_movement(vol: float, days: float, pct_to_double: Optional[float]) -> Tuple[float, Optional[float]]:
    if days <= 0:
        return 0.0, None
    em_sigma = (vol / 100.0) * math.sqrt(days / 252.0) * 100.0
    ratio = None
    if pct_to_double is not None and pct_to_double > 0:
        ratio = em_sigma / pct_to_double
    return em_sigma, ratio


def score_em_ratio(ratio: Optional[float]) -> int:
    if ratio is None:
        return 0
    if ratio >= 1.0:
        return 2
    if ratio >= 0.5:
        return 1
    return 0


def score_delta_prob(delta: Optional[float]) -> float:
    """Faixa doce entre 0.3-0.6, decai para 0 fora de 0.2-0.8."""
    if delta is None:
        return 0.0
    abs_delta = abs(delta)
    if abs_delta < 0.2 or abs_delta > 0.8:
        return 0.0
    if abs_delta < 0.3:
        return (abs_delta - 0.2) / 0.1
    if abs_delta <= 0.6:
        return 1.0
    return max(0.0, (0.8 - abs_delta) / 0.2)


def score_extrinsic(extrinsic_pct: Optional[float]) -> float:
    """Quanto menor a extrínseca/spot, melhor; zera acima de 15%."""
    if extrinsic_pct is None or extrinsic_pct < 0:
        return 0.0
    if extrinsic_pct >= 15.0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - extrinsic_pct / 15.0))


def score_prob_itm(prob: Optional[float]) -> float:
    """Faixa doce 30–60% de prob ITM; zera fora de 20–80%."""
    if prob is None:
        return 0.0
    if prob < 0.2 or prob > 0.8:
        return 0.0
    if prob < 0.3:
        return (prob - 0.2) / 0.1
    if prob <= 0.6:
        return 1.0
    return max(0.0, (0.8 - prob) / 0.2)


def calculate_weighted_score(
    moneyness_score: float,
    prob_itm_pct: Optional[float],
    prob_itm_delta_pct: Optional[float],
    extrinsic_pct_spot: Optional[float],
    liquidity_score: float,
    iv_score: float,
    theta_score: float,
    em2x_score: float,
    double_score: float,
    status_remote: str,
) -> float:
    """Combina métricas contínuas com pesos explícitos."""

    def _clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    m_norm = _clamp01(moneyness_score / 2.0)
    
    prob = prob_itm_pct
    if prob is None:
        prob = prob_itm_delta_pct
    prob_norm = score_prob_itm((prob or 0.0) / 100.0 if prob is not None else None)
    
    extr_norm = score_extrinsic(extrinsic_pct_spot)
    liq_norm = _clamp01(liquidity_score / 2.0)
    iv_norm = _clamp01(iv_score / 3.0)
    theta_norm = _clamp01(theta_score)
    asym_norm = _clamp01(((em2x_score / 2.0) + (double_score / 2.0)) / 2.0)

    status = (status_remote or "").lower()
    if "aposta" in status:
        weights = {
            "m": 0.30,
            "prob": 0.15,
            "extr": 0.10,
            "liq": 0.15,
            "iv": 0.10,
            "asym": 0.20,
            "theta": 0.0,
        }
    else:
        weights = {
            "m": 0.15,
            "prob": 0.30,
            "extr": 0.15,
            "liq": 0.15,
            "iv": 0.10,
            "theta": 0.15,
            "asym": 0.0,
        }

    score = (
        weights["m"] * m_norm
        + weights["prob"] * prob_norm
        + weights["extr"] * extr_norm
        + weights["liq"] * liq_norm
        + weights["iv"] * iv_norm
        + weights["theta"] * theta_norm
        + weights["asym"] * asym_norm
    )
    return score * 10.0


def calculate_black_scholes_call(
    spot: float, strike: float, vol: float, years: float, rate: float = 0.0, div: float = 0.0
) -> float:
    if spot <= 0 or strike <= 0 or vol <= 0 or years <= 0:
        return 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2)))
    nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2)))
    return spot * math.exp(-div * years) * nd1 - strike * math.exp(-rate * years) * nd2


def calculate_black_scholes_put(
    spot: float, strike: float, vol: float, years: float, rate: float = 0.0, div: float = 0.0
) -> float:
    if spot <= 0 or strike <= 0 or vol <= 0 or years <= 0:
        return 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    nd1 = 0.5 * (1.0 + math.erf(-d1 / math.sqrt(2)))
    nd2 = 0.5 * (1.0 + math.erf(-d2 / math.sqrt(2)))
    return strike * math.exp(-rate * years) * nd2 - spot * math.exp(-div * years) * nd1


def calculate_probability_itm(
    spot: float,
    strike: float,
    vol: float,
    days: float,
    option_type: Optional[str] = "CALL",
) -> Optional[float]:
    """Probabilidade neutra ao risco de expirar ITM (CALL: N(d2), PUT: N(-d2))."""
    if spot <= 0 or strike <= 0 or vol <= 0 or days <= 0:
        return None
    sigma = vol / 100.0
    t = days / 252.0
    denom = sigma * math.sqrt(t)
    if denom <= 0:
        return None
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / denom
    d2 = d1 - denom
    prob_itm = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2)))
    if _normalize_option_type(option_type) == "PUT":
        prob_itm = 1.0 - prob_itm
    return max(0.0, min(1.0, prob_itm))


def calculate_probability_move(
    spot: float,
    pct_move: float,
    vol: float,
    days: float,
    option_type: Optional[str] = "CALL",
) -> Optional[float]:
    """Probabilidade neutra ao risco de mover ao menos pct_move (CALL: alta, PUT: baixa)."""
    if pct_move <= 0 or spot <= 0 or vol <= 0 or days <= 0:
        return None
    direction = "DOWN" if _normalize_option_type(option_type) == "PUT" else "UP"
    if direction == "DOWN":
        target = spot * (1.0 - pct_move / 100.0)
    else:
        target = spot * (1.0 + pct_move / 100.0)
    if target <= 0:
        return None
    sigma = vol / 100.0
    t = days / 252.0
    denom = sigma * math.sqrt(t)
    if denom <= 0:
        return None
    z = (math.log(target / spot) + 0.5 * sigma * sigma * t) / denom
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2)))
    prob = cdf if direction == "DOWN" else (1.0 - cdf)
    return max(0.0, min(1.0, prob))


def calculate_breakeven(
    spot: float,
    strike: float,
    price: float,
    option_type: Optional[str] = "CALL",
) -> Tuple[Optional[float], Optional[float]]:
    if spot <= 0 or strike <= 0 or price <= 0:
        return None, None
    if _normalize_option_type(option_type) == "PUT":
        be_price = strike - price
    else:
        be_price = strike + price
    dist_pct = ((be_price - spot) / spot) * 100.0
    return be_price, dist_pct


def classify_remote_bet(prob_itm_pct: Optional[float], extrinsic_pct_spot: Optional[float], days: Optional[float]) -> str:
    if prob_itm_pct is None:
        return ""
    if prob_itm_pct < 25.0:
        return "Loteria (<25% ITM)"
    if (
        25.0 <= prob_itm_pct <= 60.0
        and extrinsic_pct_spot is not None
        and extrinsic_pct_spot <= 10.0
        and days is not None
        and days >= 252.0
    ):
        return "Aposta remota racional"
    return ""


def determine_moneyness_status(ai_otm_label: str, dist_perc: Optional[float]) -> str:
    raw = ai_otm_label.upper()
    if "ITM" in raw:
        return "ITM"
    if dist_perc is None:
        return ""
    if "ATM" in raw and dist_perc <= 1.0:
        return "0-5% OTM (colada)"
    if dist_perc < 0:
        return "ITM"
    if dist_perc <= 5:
        return "0-5% OTM (colada)"
    if dist_perc <= 15:
        return "5-15% OTM (aposta)"
    if dist_perc <= 20:
        return "15-20% OTM"
    return "20%+ OTM (loteria)"


def spot_from_strike_dist(strike: float, dist: float) -> Optional[float]:
    denom = 1 + (dist / 100.0)
    if abs(denom) < 1e-6:
        return None
    return strike / denom


def calculate_price_distortion(price_buy: float, price_theoretical: float) -> Optional[float]:
    if price_theoretical <= 0:
        return None
    return ((price_buy - price_theoretical) / price_theoretical) * 100.0


def calculate_cost_pct(option_price: float, spot_price: float) -> Optional[float]:
    if spot_price <= 0:
        return None
    return (option_price / spot_price) * 100.0


def calculate_intrinsic_extrinsic(
    option_price: float,
    strike: float,
    spot_price: float,
    option_type: Optional[str] = "CALL",
) -> Tuple[Optional[float], Optional[float]]:
    if spot_price <= 0:
        return None, None
    if _normalize_option_type(option_type) == "PUT":
        intrinsic = max(strike - spot_price, 0.0)
    else:
        intrinsic = max(spot_price - strike, 0.0)
    extrinsic = max(option_price - intrinsic, 0.0)
    return intrinsic, extrinsic


def calculate_extrinsic_pct(extrinsic: float, spot_price: float) -> Optional[float]:
    if spot_price <= 0:
        return None
    return (extrinsic / spot_price) * 100.0
