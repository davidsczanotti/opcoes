from __future__ import annotations

import re
from typing import Optional

# Séries tradicionais da B3: A–L = calls, M–X = puts.
CALL_SERIES = set("ABCDEFGHIJKL")
PUT_SERIES = set("MNOPQRSTUVWX")


def infer_option_type(ticker: str) -> Optional[str]:
    """Infere CALL/PUT a partir da letra da série no ticker."""

    if not ticker:
        return None
    text = str(ticker).strip().upper()
    if not text:
        return None

    # Procura a primeira letra imediatamente antes de um dígito (ex.: PETRA30, ABEVA105W1).
    match = re.search(r"([A-Z])\d", text)
    series_letter: Optional[str] = match.group(1) if match else None

    has_digits = any(ch.isdigit() for ch in text)
    # Fallback: assume que a 5ª letra (índice 4) é a série quando há dígitos no ticker.
    if series_letter is None and has_digits and len(text) >= 5 and text[4].isalpha():
        series_letter = text[4]

    if series_letter in CALL_SERIES:
        return "CALL"
    if series_letter in PUT_SERIES:
        return "PUT"
    return None


__all__ = ["infer_option_type", "CALL_SERIES", "PUT_SERIES"]
