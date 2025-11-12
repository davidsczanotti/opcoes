from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Dict, Iterable, Optional, Sequence, Tuple


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _http_get(url: str, timeout: float) -> Optional[str]:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def _parse_float(s: str) -> Optional[float]:
    s = s.strip()
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _extract_pl_from_window_nuxt(html: str, symbol: str) -> Optional[float]:
    m = re.search(r"window.__NUXT__\s*=\s*(\{.*?\})\s*;", html, flags=re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    target = symbol.upper()

    def iter_dicts(obj: object) -> Iterable[dict]:
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from iter_dicts(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from iter_dicts(item)

    for node in iter_dicts(data):
        if "pl" not in node:
            continue
        codes = [str(node.get(key, "")).upper() for key in ("code", "ticker", "symbol", "tickerCode")]
        if target in codes:
            val = _parse_float(node.get("pl"))
            if val is not None:
                return val
    return None


def _extract_pe_from_html(html: str, symbol: str) -> Optional[float]:
    """Extrai P/L tentando múltiplas estratégias, priorizando dados do símbolo."""

    # 1) JSON Nuxt com associação direta ao ticker
    val = _extract_pl_from_window_nuxt(html, symbol)
    if val is not None:
        return val

    target = re.escape(symbol.upper())
    patterns = [
        rf'"ticker"\s*:\s*"{target}"[^{{}}]{{0,2000}}?"pl"\s*:\s*([0-9\.,\-]+)',
        rf'"code"\s*:\s*"{target}"[^{{}}]{{0,2000}}?"pl"\s*:\s*([0-9\.,\-]+)',
        rf'"pl"\s*:\s*([0-9\.,\-]+)[^{{}}]{{0,2000}}?"ticker"\s*:\s*"{target}"',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            val = _parse_float(m.group(1))
            if val is not None:
                return val

    # 2) fallback rótulo textual
    for match in re.finditer(r"P\s*/\s*L", html, flags=re.IGNORECASE):
        start = match.end()
        window = html[start : start + 200]
        m2 = re.search(r"([-+]?\d{1,3}(?:[\.,]\d{3})*(?:[\.,]\d+)?|\d+(?:[\.,]\d+)?)", window)
        if m2:
            val = _parse_float(m2.group(1))
            if val is not None and val != 3.0:
                return val
    return None


def _detect_kind(html: str) -> str:
    """Classifica página do ativo de forma heurística.

    Retorna um de: 'unit', 'acao', 'etf', 'indice', 'bdr', 'unknown'.
    """
    h = html.lower()
    # pistas de ETF/índice
    if "etf" in h or "fundo de índice" in h or "fundo de indice" in h:
        return "etf"
    if "indice b3" in h or "índice b3" in h or "indice" in h and "p/l" in h and "ibov" in h:
        return "indice"
    # pistas de BDR
    if "bdr" in h:
        return "bdr"
    # pistas de Unit (além do sufixo 11, o html costuma ter 'Unit')
    if "unit" in h or "unitária" in h or "unitaria" in h:
        return "unit"
    # fallback: considerar ação
    return "acao"


def fetch_fundamentals_map(
    symbols: Sequence[str], *, timeout: float = 15.0, throttle: float = 0.8, only_units: bool = False
) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """Obtém (earnings_yield_ttm, pe_ttm) por ticker no Status Invest.

    Implementação best-effort via scraping HTML. Caso não consiga extrair,
    retorna None para os campos.
    """

    base_paths = [
        "https://statusinvest.com.br/acoes/{}",
        "https://statusinvest.com.br/acao/{}",
    ]

    out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    for sym in symbols:
        sym_u = sym.strip().upper()
        html = None
        for pattern in base_paths:
            url = pattern.format(sym_u)
            html = _http_get(url, timeout)
            if html:
                break
        if not html:
            out[sym_u] = (None, None)
            time.sleep(max(throttle, 0.2))
            continue

        kind = _detect_kind(html)
        if only_units and kind != "unit":
            # Ignora tudo que não for Unit
            out[sym_u] = (None, None)
            time.sleep(max(throttle, 0.2))
            continue
        if kind in {"etf", "indice"}:
            # Explicitamente ignorar ETFs/índices
            out[sym_u] = (None, None)
            time.sleep(max(throttle, 0.2))
            continue

        pe = _extract_pe_from_html(html, sym_u)
        ey = (1.0 / pe) if (pe and pe > 0) else None
        out[sym_u] = (ey, pe)
        time.sleep(max(throttle, 0.2))

    return out


__all__ = ["fetch_fundamentals_map"]
