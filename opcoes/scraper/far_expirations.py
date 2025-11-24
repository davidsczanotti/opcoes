from __future__ import annotations

import ast
import re
import urllib.request
from typing import Dict, Optional

FAR_EXPIRATIONS_URL = "https://opcoes.net.br/opcoes/bovespa/vencimentos-longos"


def fetch_far_expiration_quotes(url: str = FAR_EXPIRATIONS_URL) -> Dict[str, dict]:
    """Busca tabela estática de vencimentos longos (inclui bid/ask quando expostos).

    Retorna um mapa ticker -> dados numéricos crus (float) para fusão posterior.
    """
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"falha ao baixar {url}: {exc}") from exc

    match = re.search(r"optionListDataArray=(\[[^\n]*?]);", html, re.DOTALL)
    if not match:
        match = re.search(r"optionListDataArray=(\[[\s\S]+?]);", html)
    if not match:
        return {}

    raw_array = match.group(1).replace("null", "None")
    try:
        data = ast.literal_eval(raw_array)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"falha ao interpretar optionListDataArray: {exc}") from exc

    quotes: Dict[str, dict] = {}
    for row in data:
        if not row or len(row) < 11:
            continue
        ticker = (row[0] or "").strip().upper()
        if not ticker:
            continue
        quotes[ticker] = {
            "tipo": row[1],
            "vencimento": row[2],
            "dias_uteis": row[3],
            "dias_corridos": row[4],
            "mod": row[5],
            "strike": row[6],
            "dist_pct": row[7],
            "vol_impl_bid": row[8],
            "best_bid": row[9],
            "best_ask": row[10],
            "vol_impl_ask": row[11],
            "vi": row[12],
            "ve": row[13],
            "ultimo": row[14],
            "data_ultima_neg": row[15] if len(row) > 15 else None,
            "dias_corr_ultima_neg": row[16] if len(row) > 16 else None,
            "num_neg": row[17] if len(row) > 17 else None,
            "vol_financeiro": row[18] if len(row) > 18 else None,
            "vencimento_iso": row[19] if len(row) > 19 else None,
            "underlying": row[20] if len(row) > 20 else None,
        }
    return quotes


__all__ = ["fetch_far_expiration_quotes"]
