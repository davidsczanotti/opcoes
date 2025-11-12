from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Optional, Tuple


def _parse_float(value: object) -> Optional[float]:
    """Converte string numérica em float, aceitando formatos pt-BR.

    - Mantém apenas dígitos, sinais e separadores [.,,]
    - Se existir vírgula e ponto, assume vírgula como decimal (pt-BR)
    - Retorna None em caso de falha
    """

    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:  # noqa: BLE001
            return None
    s = str(value).strip()
    if not s:
        return None
    # Remove símbolos e letras (R$, % etc.) preservando sinais e separadores
    s = re.sub(r"[^0-9,\.\-+eE]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        # Provável formato 1.234,56
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:  # noqa: BLE001
        return None


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def load_earnings_yield_map(path: Path) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """Carrega fundamentos de um CSV e devolve mapa: ticker -> (earnings_yield_ttm, pe_ttm).

    O arquivo pode conter qualquer um dos conjuntos abaixo:
    - ticker, earnings_yield_ttm
    - ticker, pe_ttm
    - ticker, lpa_ttm, preco
    - ticker, lucro_liquido_ttm, acoes_total, preco

    Colunas aceitas (sinônimos por normalização):
    - ticker: ticker, codigo, symbol
    - earnings_yield_ttm: earnings_yield_ttm, eyttm, ep, epttm
    - pe_ttm: pe_ttm, pl, plttm, pe
    - lpa_ttm: lpa_ttm, eps_ttm, lpa, eps
    - preco: preco, price, cotacao
    - lucro_liquido_ttm: lucro_liquido_ttm, lucrottm, lucrottm
    - acoes_total: acoes_total, numacoes, acoes, shares, shares_outstanding
    """

    path = Path(path)
    if not path.exists():
        return {}

    result: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Mapeamento flexível de colunas
            norm = { _norm_key(k): v for k, v in row.items() }

            def get(*names: str) -> Optional[str]:
                for n in names:
                    v = norm.get(_norm_key(n))
                    if v is not None and str(v).strip() != "":
                        return str(v)
                return None

            ticker = get("ticker", "codigo", "symbol")
            if not ticker:
                continue
            ticker = ticker.strip().upper()

            ey = _parse_float(get("earnings_yield_ttm", "eyttm", "ep", "epttm"))
            pe = _parse_float(get("pe_ttm", "pl", "plttm", "pe"))
            lpa = _parse_float(get("lpa_ttm", "eps_ttm", "lpa", "eps"))
            preco = _parse_float(get("preco", "price", "cotacao"))
            lucro_ttm = _parse_float(get("lucro_liquido_ttm", "lucrottm", "lucrottm", "lucrottm"))
            acoes = _parse_float(get("acoes_total", "numacoes", "acoes", "shares", "shares_outstanding"))

            # Derivações possíveis
            if ey is None and pe is not None and pe > 0:
                ey = 1.0 / pe
            if ey is None and lpa is not None and preco is not None and preco > 0:
                ey = lpa / preco
            if ey is None and lucro_ttm is not None and acoes and acoes > 0 and preco and preco > 0:
                lpa_calc = lucro_ttm / acoes
                ey = lpa_calc / preco
            if pe is None and ey is not None and ey > 0:
                pe = 1.0 / ey

            result[ticker] = (ey, pe)

    return result


__all__ = ["load_earnings_yield_map"]

