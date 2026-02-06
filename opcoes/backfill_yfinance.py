from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional

import yfinance as yf

from .config import get_db_path


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _list_underlyings(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("SELECT DISTINCT underlying FROM option_snapshots").fetchall()
    return [str(r[0]).strip().upper() for r in rows if r and r[0]]


def _insert_price(
    conn: sqlite3.Connection,
    *,
    underlying: str,
    date_iso: str,
    price: float,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO underlying_snapshots (snapshot_date, underlying, price, price_date)
        VALUES (?, ?, ?, ?)
        """,
        (date_iso, underlying, price, date_iso),
    )


def _symbol_to_yf(sym: str) -> str:
    # B3 tickers no Yahoo Finance normalmente são <TICKER>.SA
    return f"{sym}.SA"


def _backfill_prices(
    conn: sqlite3.Connection,
    *,
    underlyings: Iterable[str],
    days: int,
) -> None:
    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    for sym in sorted({u for u in underlyings if u}):
        yf_sym = _symbol_to_yf(sym)
        print(f"Baixando {yf_sym} (desde {start.isoformat()})...")
        hist = yf.download(
            yf_sym,
            start=start.isoformat(),
            end=today.isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=False,  # evita warning da mudança de default
        )
        if hist.empty:
            print(f"  - sem dados para {yf_sym}")
            continue
        for idx, row in hist.iterrows():
            val = row.get("Close")
            try:
                if hasattr(val, "item"):
                    price = float(val.item())
                else:
                    price = float(val)
            except Exception:
                continue
            if price is None or price <= 0:
                continue
            date_iso = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
            _insert_price(conn, underlying=sym, date_iso=date_iso, price=price)
        conn.commit()
        print(f"  - OK ({len(hist)} cotações)")


def backfill_prices(
    *,
    db_path: Optional[Path] = None,
    days: int = 90,
    underlyings: Optional[Iterable[str]] = None,
) -> None:
    """Preenche histórico de preços diários para underlyings usando yfinance.

    - `underlyings`: se None, usa todos presentes em option_snapshots.
    - `days`: quantos dias para trás baixar (default: 90).
    """
    resolved_db_path = Path(db_path) if db_path is not None else get_db_path()
    conn = _connect(resolved_db_path)
    try:
        if underlyings is None:
            symbols = _list_underlyings(conn)
        else:
            symbols = [u.strip().upper() for u in underlyings if u and u.strip()]
        if not symbols:
            print("Nenhum underlying encontrado para backfill.")
            return
        _backfill_prices(conn, underlyings=symbols, days=max(days, 1))
    finally:
        conn.close()


def main() -> None:
    default_db = get_db_path()
    parser = argparse.ArgumentParser(description="Backfill de preços diários via yfinance para underlying_snapshots.")
    parser.add_argument(
        "--db",
        type=Path,
        default=default_db,
        help=f"Caminho do opcoes_snapshots.db (default: {default_db})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Quantos dias de histórico baixar (default: 90)",
    )
    parser.add_argument(
        "--underlying",
        action="append",
        help="Underlying específico (pode repetir). Se não informar, usa todos do banco.",
    )
    args = parser.parse_args()

    underlyings: Optional[Iterable[str]] = None
    if args.underlying:
        underlyings = {u.strip().upper() for u in args.underlying if u and u.strip()}

    backfill_prices(db_path=args.db, days=max(args.days, 1), underlyings=underlyings)


if __name__ == "__main__":
    main()
