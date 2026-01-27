from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

DB_PATH = Path("data/opcoes_snapshots.db")


@dataclass
class TaxSummary:
    year: int
    month: int
    swing_net: float
    daytrade_net: float
    swing_ir: float
    daytrade_ir: float
    swing_irrf: float
    daytrade_irrf: float


def compute_tax(month: int, year: int) -> TaxSummary:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cols = {row[1] for row in cur.execute('PRAGMA table_info("positions")').fetchall()}
        if "side" not in cols:
            cur.execute('ALTER TABLE positions ADD COLUMN "side" TEXT DEFAULT \'long\'')
            conn.commit()
    except sqlite3.Error:
        pass
    swing_gain = 0.0
    daytrade_gain = 0.0
    swing_irrf = 0.0
    daytrade_irrf = 0.0

    cur.execute(
        """
        SELECT trade_type, trade_date, qty, entry_price, fees, partial_date, partial_price, partial_qty,
               exit_date, exit_price, irrf, side
        FROM positions
        """
    )
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        trade_type = (row[0] or "swing").strip().lower()
        trade_year = int(str(row[1]).split("-")[0])
        # partial event
        partial_date = row[5]
        partial_price = row[6]
        partial_qty = row[7] or 0
        qty = row[2] or 0
        entry = float(row[3] or 0.0)
        fees = float(row[4] or 0.0)
        exit_date = row[8]
        exit_price = row[9]
        irrf_value = float(row[10] or 0.0)
        side_raw = row[11] if len(row) > 11 else None
        side = (side_raw or "long").strip().lower()
        direction = -1 if side in {"short", "vendida", "vendido", "v"} else 1

        def same_month(date_str: str) -> bool:
            if not date_str or len(date_str) < 7:
                return False
            parts = date_str.split("-")
            return int(parts[0]) == year and int(parts[1]) == month

        if partial_qty and partial_price is not None and same_month(str(partial_date)):
            gain = direction * (float(partial_price) - entry) * partial_qty
            if trade_type == "daytrade":
                daytrade_gain += gain
            else:
                swing_gain += gain

        open_qty = max(qty - (partial_qty or 0), 0)
        if exit_price is not None and same_month(str(exit_date)) and open_qty > 0:
            gain = direction * (float(exit_price) - entry) * open_qty - fees
            if trade_type == "daytrade":
                daytrade_gain += gain
            else:
                swing_gain += gain
            if irrf_value:
                if trade_type == "daytrade":
                    daytrade_irrf += irrf_value
                else:
                    swing_irrf += irrf_value

    swing_tax = 0.15 * swing_gain if swing_gain > 0 else 0.0
    daytrade_tax = 0.20 * daytrade_gain if daytrade_gain > 0 else 0.0

    return TaxSummary(
        year=year,
        month=month,
        swing_net=swing_gain,
        daytrade_net=daytrade_gain,
        swing_ir=swing_tax,
        daytrade_ir=daytrade_tax,
        swing_irrf=swing_irrf,
        daytrade_irrf=daytrade_irrf,
    )


__all__ = ["compute_tax", "TaxSummary"]
