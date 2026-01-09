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


def format_decimal(value: Optional[float], decimals: int = 2, signed: bool = False) -> str:
    """Formata float para string PT-BR (vírgula decimal)."""
    if value is None:
        return ""
    fmt = f"{{:.{decimals}f}}"
    txt = fmt.format(value).replace(".", ",")
    if signed and value > 0:
        txt = f"+{txt}"
    return txt


def parse_ptbr_number(value: object) -> Optional[float]:
    """Converte strings com vírgula/porcentagem em float."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:  # noqa: BLE001
            return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = (
        text.replace("\xa0", "")
        .replace("\u2212", "-")
        .replace("−", "-")
        .replace("%", "")
        .replace("+", "")
        .replace(" ", "")
    )
    if not cleaned or cleaned == "-":
        return None
    cleaned = cleaned.replace('"', "").replace("'", "")
    has_comma = "," in cleaned
    has_dot = "." in cleaned
    if has_comma and has_dot:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif has_comma:
        cleaned = cleaned.replace(",", ".")
    elif has_dot and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


__all__ = ["infer_option_type", "format_decimal", "parse_ptbr_number", "CALL_SERIES", "PUT_SERIES"]
