from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yfinance as yf


RET_3M_DAYS = 63  # Aproxima 3 meses úteis
MM200_WINDOW = 200


@dataclass
class PriceIndicators:
    price: Optional[float]
    price_date: Optional[str]
    mm200: Optional[float]
    return_3m: Optional[float]
    trend_flag: Optional[int]
    trend_reason: str


def fetch_price_indicators(symbols: Sequence[str]) -> Dict[str, PriceIndicators]:
    unique = _unique_symbols(symbols)
    out: Dict[str, PriceIndicators] = {}
    for sym in unique:
        ticker = _to_yahoo_symbol(sym)
        if not ticker:
            continue
        try:
            info = _build_indicators(ticker)
        except Exception as exc:  # noqa: BLE001
            print(f"Aviso: falhou price fetch {sym}: {exc}")
            continue
        if info is None:
            continue
        out[sym] = info
    return out


def _build_indicators(ticker: str) -> Optional[PriceIndicators]:
    data = yf.Ticker(ticker)
    hist = data.history(period="1y", interval="1d")
    if hist is None or hist.empty:
        return None
    closes = hist["Close"].dropna()
    if closes.empty:
        return None
    last_close = float(closes.iloc[-1])
    last_date = hist.index[-1]
    if isinstance(last_date, dt.datetime):
        last_date_str = last_date.date().isoformat()
    else:
        last_date_str = dt.date.fromisoformat(str(last_date)).isoformat()

    mm200 = float(closes.tail(MM200_WINDOW).mean()) if len(closes) >= 5 else None
    ret3m = _compute_return(closes, RET_3M_DAYS)

    trend_flag, reason = _trend_status(last_close, mm200, ret3m)

    return PriceIndicators(
        price=last_close,
        price_date=last_date_str,
        mm200=mm200,
        return_3m=ret3m,
        trend_flag=trend_flag,
        trend_reason=reason,
    )


def _compute_return(series, lookback: int) -> Optional[float]:
    if len(series) <= lookback:
        return None
    past = float(series.iloc[-lookback - 1])
    if past == 0:
        return None
    latest = float(series.iloc[-1])
    return ((latest / past) - 1.0) * 100.0


def _trend_status(price: Optional[float], mm200: Optional[float], ret3m: Optional[float]) -> Tuple[Optional[int], str]:
    reasons: List[str] = []
    flag: Optional[int] = None
    if price is not None and mm200 is not None:
        if price >= mm200:
            flag = 1
            reasons.append(">=MM200")
        else:
            reasons.append("<MM200")
    if ret3m is not None:
        if ret3m >= 0:
            flag = 1
            reasons.append("ret3m>=0")
        else:
            reasons.append("ret3m<0")
    if flag is None:
        flag = 0 if reasons else None
    return flag, ",".join(reasons)


def _to_yahoo_symbol(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    s = symbol.strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    return f"{s}.SA"


def _unique_symbols(symbols: Iterable[str]) -> List[str]:
    return list(dict.fromkeys([s for s in (symbols or []) if s]))


__all__ = ["PriceIndicators", "fetch_price_indicators"]
