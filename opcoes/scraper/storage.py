from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


CSV_DELIMITER = ";"
CSV_READER_KWARGS = {"delimiter": CSV_DELIMITER, "skipinitialspace": True}
CSV_WRITER_KWARGS = {"delimiter": CSV_DELIMITER, "lineterminator": "\n"}

CSV_FIELDS: List[str] = [
    "underlying",
    "ticker",
    "vencimento",
    "dias_uteis",
    "fm",
    "mod",
    "strike",
    "ai_otm",
    "dist_perc_strike",
    "ultimo",
    "var_perc",
    "data_hora",
    "num_neg",
    "vol_financeiro",
    "vol_impl_perc",
    "delta",
    "gamma",
    "theta_dolar",
    "theta_perc",
    "vega",
    "iq",
    "coberto",
    "travado",
    "descoberto",
    "titulares",
    "lancadores",
    # Medidas do subjacente
    "underlying_price",
    "underlying_price_date",
    "underlying_mm200",
    "underlying_return_3m",
    "trend_flag",
    "trend_reason",
    "custo_pct",
    "intrinsic_value",
    "extrinsic_value",
    "extrinsic_pct_spot",
    "breakeven_price",
    "breakeven_dist_pct",
    # Indicadores opcionais (preenchidos se arquivo de fundamentos for fornecido)
    "earnings_yield_ttm",
    "pe_ttm",
    # Derivados para checklist tático
    "Status_Moneyness",
    "%_Alta_p_2x",
    "Status_2x",
    "Status_Liquidez",
    "Status_Theta",
    "prob_itm_pct",
    "prob_itm_delta_pct",
    "prob_2x_pct",
    "Status_Remoto",
    "moneyness_score",
    "liquidez_score",
    "dobro_score",
    "theta_score",
    "score_total",
    "iv_rank_180d",
    "iv_score",
    "em_1sigma_pct",
    "relacao_em_2x",
    "em2x_score",
    "vol_fluxo_5d",
    "num_fluxo_5d",
    "best_bid",
    "best_ask",
    "spread_pct",
    "preco_teorico",
    "distorcao_preco_pct",
    "distorcao_flag",
    "illiquidez_flag",
]

CSV_FLOAT_FIELDS: Dict[str, int] = {
    "strike": 2,
    "dist_perc_strike": 2,
    "ultimo": 2,
    "var_perc": 2,
    "vol_financeiro": 2,
    "vol_impl_perc": 1,
    "delta": 4,
    "gamma": 4,
    "theta_dolar": 4,
    "theta_perc": 4,
    "vega": 4,
    "iq": 2,
    "underlying_price": 2,
    "underlying_mm200": 2,
    "underlying_return_3m": 2,
    "custo_pct": 2,
    "intrinsic_value": 2,
    "extrinsic_value": 2,
    "extrinsic_pct_spot": 2,
    "breakeven_price": 2,
    "breakeven_dist_pct": 2,
    "earnings_yield_ttm": 6,
    "pe_ttm": 6,
    "%_Alta_p_2x": 1,
    "prob_itm_pct": 1,
    "prob_itm_delta_pct": 1,
    "prob_2x_pct": 1,
    "moneyness_score": 2,
    "theta_score": 2,
    "iv_score": 2,
    "score_total": 2,
    "iv_rank_180d": 1,
    "em_1sigma_pct": 1,
    "relacao_em_2x": 2,
    "vol_fluxo_5d": 2,
    "num_fluxo_5d": 2,
    "best_bid": 2,
    "best_ask": 2,
    "spread_pct": 2,
    "preco_teorico": 2,
    "distorcao_preco_pct": 2,
}

CSV_INT_FIELDS: Set[str] = {
    "dias_uteis",
    "num_neg",
    "coberto",
    "travado",
    "descoberto",
    "titulares",
    "lancadores",
    "liquidez_score",
    "dobro_score",
    "em2x_score",
}


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _csv_reader(f) -> Tuple[csv.DictReader, str]:
    """Builds a DictReader using ';' as default delimiter and falls back to ',' for legacies."""

    reader = csv.DictReader(f, **CSV_READER_KWARGS)
    used_delimiter = CSV_DELIMITER
    if reader.fieldnames and len(reader.fieldnames) == 1 and "," in (reader.fieldnames[0] or ""):
        f.seek(0)
        reader = csv.DictReader(f, delimiter=",", skipinitialspace=True)
        used_delimiter = ","
    return reader, used_delimiter


def _parse_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_ptbr_number(value: object) -> Optional[float]:
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


def _format_decimal_ptbr(value: float, decimals: int) -> str:
    fmt = f"{value:.{decimals}f}"
    return fmt.replace(".", ",")


def normalize_csv_row(row: Dict[str, object]) -> Dict[str, str]:
    """Converte campos numéricos para números/strings padronizadas antes de gravar o CSV."""

    normalized: Dict[str, str] = {}
    for key in CSV_FIELDS:
        raw = row.get(key, "")
        if key in CSV_INT_FIELDS:
            parsed = _parse_int(raw)
            normalized[key] = str(parsed) if parsed is not None else ""
            continue
        if key in CSV_FLOAT_FIELDS:
            parsed = _parse_ptbr_number(raw)
            if parsed is None:
                normalized[key] = ""
            else:
                decimals = CSV_FLOAT_FIELDS[key]
                normalized[key] = _format_decimal_ptbr(parsed, decimals)
            continue
        normalized[key] = "" if raw is None else str(raw).strip()
    return normalized


def load_existing_tickers(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    _ensure_csv_header(path)
    tickers: Set[str] = set()
    with path.open("r", newline="", encoding="utf-8") as f:
        reader, _ = _csv_reader(f)
        for row in reader:
            t = (row.get("ticker") or "").strip()
            if t:
                tickers.add(t)
    return tickers


def append_rows_dedup(path: Path, rows: Iterable[Dict[str, object]], existing: Set[str]) -> int:
    """Append rows to CSV, skipping those with duplicate ticker.

    Returns number of rows written.
    """
    _ensure_parent(path)
    write_header = not path.exists()
    if path.exists():
        if path.stat().st_size == 0:
            write_header = True
        else:
            _ensure_csv_header(path)
    written = 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, **CSV_WRITER_KWARGS)
        if write_header:
            writer.writeheader()
        for row in rows:
            ticker = str(row.get("ticker", "")).strip()
            if not ticker or ticker in existing:
                continue
            filtered = normalize_csv_row(row)
            writer.writerow(filtered)
            existing.add(ticker)
            written += 1
    return written


def _ensure_csv_header(path: Path) -> None:
    """Garante cabeçalho padrão e mantém dados existentes.

    Caso o cabeçalho não corresponda a `CSV_FIELDS`, reescreve o arquivo
    mapeando colunas pelo nome (usando a primeira linha como header original)
    e preservando os valores conhecidos. Linhas vazias são descartadas.
    """

    if not path.exists():
        return
    with path.open("r", newline="", encoding="utf-8") as f:
        reader, used_delimiter = _csv_reader(f)
        if not reader.fieldnames:
            return
        rows = list(reader)
    # Já está no formato esperado?
    header_matches = [h.strip() for h in (reader.fieldnames or [])] == CSV_FIELDS
    needs_rewrite = (used_delimiter != CSV_DELIMITER) or not header_matches

    def map_row(row: Dict[str, str]) -> List[str]:
        normalized = normalize_csv_row(row)
        return [str(normalized.get(col, "")) for col in CSV_FIELDS]

    normalized: List[List[str]] = [map_row(r) for r in rows if any(str(v).strip() for v in r.values())]

    if not needs_rewrite:
        return

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, **CSV_WRITER_KWARGS)
        writer.writerow(CSV_FIELDS)
        writer.writerows(normalized)


def _normalize_row(row: List[str]) -> List[str]:
    row = row[: len(CSV_FIELDS)]
    if len(row) < len(CSV_FIELDS):
        row = row + [""] * (len(CSV_FIELDS) - len(row))
    return row
