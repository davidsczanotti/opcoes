from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Optional, Sequence

from .config import get_db_path
from .scraper.storage import CSV_FIELDS, CSV_WRITER_KWARGS, _ensure_parent, normalize_csv_row

def _ensure_snapshot_columns(conn: sqlite3.Connection) -> None:
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='option_snapshots' LIMIT 1"
        ).fetchone()
    except Exception:
        return
    if not exists:
        return

    existing = {
        row[1]
        for row in conn.execute('PRAGMA table_info("option_snapshots")').fetchall()
        if row and len(row) > 1
    }
    missing = [col for col in CSV_FIELDS if col not in existing]
    if not missing:
        return
    for col in missing:
        conn.execute(f'ALTER TABLE "option_snapshots" ADD COLUMN "{col}" TEXT')
    conn.commit()


def export_snapshot(
    *,
    output_csv: Path,
    snapshot_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Path:
    """Exporta um snapshot para CSV (sem deduplicar por ticker).

    - Se `snapshot_date` for None, usa a data mais recente disponível.
    - Usa sempre o schema atual de CSV_FIELDS, preenchendo campos ausentes como string vazia.
    """

    output_csv = Path(output_csv)
    _ensure_parent(output_csv)

    resolved_db_path = Path(db_path) if db_path is not None else get_db_path()
    conn = sqlite3.connect(resolved_db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_snapshot_columns(conn)
        if snapshot_date is None:
            row = conn.execute("SELECT MAX(snapshot_date) FROM option_snapshots").fetchone()
            if not row or not row[0]:
                raise RuntimeError("Nenhum snapshot encontrado em option_snapshots.")
            snapshot_date = str(row[0])

        # Prepara SELECT com colunas quotadas
        cols_clause = ", ".join(f'"{c}"' for c in CSV_FIELDS)
        query = f"""
            SELECT {cols_clause}
            FROM option_snapshots
            WHERE snapshot_date = ?
            ORDER BY underlying, ticker
        """
        rows = conn.execute(query, (snapshot_date,)).fetchall()
    finally:
        conn.close()

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, **CSV_WRITER_KWARGS)
        writer.writeheader()
        for r in rows:
            out_row = {col: (r[col] if col in r.keys() and r[col] is not None else "") for col in CSV_FIELDS}
            writer.writerow(normalize_csv_row(out_row))

    return output_csv


__all__ = ["export_snapshot"]
