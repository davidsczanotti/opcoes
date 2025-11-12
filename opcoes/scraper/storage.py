from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Set


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
    # Indicadores opcionais (preenchidos se arquivo de fundamentos for fornecido)
    "earnings_yield_ttm",
    "pe_ttm",
]


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_existing_tickers(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    _ensure_csv_header(path)
    tickers: Set[str] = set()
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
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
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            ticker = str(row.get("ticker", "")).strip()
            if not ticker or ticker in existing:
                continue
            # keep only supported fields
            filtered = {k: row.get(k, "") for k in CSV_FIELDS}
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
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return
        rows = list(reader)
    # Já está no formato esperado?
    if [h.strip() for h in (reader.fieldnames or [])] == CSV_FIELDS:
        return

    def map_row(row: Dict[str, str]) -> List[str]:
        return [str(row.get(col, "")) for col in CSV_FIELDS]

    normalized: List[List[str]] = [map_row(r) for r in rows if any(str(v).strip() for v in r.values())]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        writer.writerows(normalized)


def _normalize_row(row: List[str]) -> List[str]:
    row = row[: len(CSV_FIELDS)]
    if len(row) < len(CSV_FIELDS):
        row = row + [""] * (len(CSV_FIELDS) - len(row))
    return row
