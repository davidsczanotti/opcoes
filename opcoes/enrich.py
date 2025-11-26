from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .scraper.fundamentals import load_earnings_yield_map
from .scraper.statusinvest import fetch_fundamentals_map
from .scraper.storage import CSV_FIELDS, CSV_WRITER_KWARGS, _csv_reader, _ensure_parent, normalize_csv_row


def _unique(seq: Iterable[str]) -> List[str]:
    return list(dict.fromkeys([s for s in seq if s]))


def _format_opt(v: Optional[float]) -> str:
    return f"{v:.6f}".replace(".", ",") if v is not None else ""


def enrich_csv(
    *,
    input_csv: Path,
    output_csv: Optional[Path] = None,
    use_status_invest: bool = False,
    fundamentals_csv: Optional[Path] = None,
    timeout: float = 15.0,
    throttle: float = 0.8,
    only_units: bool = False,
) -> Path:
    """Enriquece um CSV existente preenchendo earnings_yield_ttm e pe_ttm por underlying.

    - Quando `use_status_invest` é True, baixa P/L do Status Invest e deriva E/P.
    - Quando `fundamentals_csv` é informado, usa esse arquivo como fonte alternativa.
    - Escreve no `output_csv` (ou in-place se não informado), preservando as demais colunas.
    """

    input_csv = Path(input_csv)
    if output_csv is None:
        output_csv = input_csv
    output_csv = Path(output_csv)

    # Lê arquivo de entrada como dicts
    with input_csv.open("r", newline="", encoding="utf-8") as f:
        reader, _ = _csv_reader(f)
        rows: List[Dict[str, str]] = [normalize_csv_row(r) for r in reader]

    underlyings = _unique([str(r.get("underlying", "")).strip().upper() for r in rows])

    # Carrega mapa de fundamentos por opção escolhida
    fundamentals_map: Dict[str, Tuple[Optional[float], Optional[float]]]
    if use_status_invest:
        fundamentals_map = fetch_fundamentals_map(
            underlyings, timeout=timeout, throttle=throttle, only_units=only_units
        )
    elif fundamentals_csv:
        fundamentals_map = load_earnings_yield_map(Path(fundamentals_csv))
    else:
        fundamentals_map = {}

    # Prepara escrita segura
    _ensure_parent(output_csv)
    tmp_path = output_csv.with_suffix(output_csv.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, **CSV_WRITER_KWARGS)
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k, "") for k in CSV_FIELDS}
            sym = str(row.get("underlying", "")).strip().upper()
            ey, pe = fundamentals_map.get(sym, (None, None))
            out["earnings_yield_ttm"] = _format_opt(ey)
            out["pe_ttm"] = _format_opt(pe)
            writer.writerow(normalize_csv_row(out))

    os.replace(tmp_path, output_csv)
    return output_csv


__all__ = ["enrich_csv"]
