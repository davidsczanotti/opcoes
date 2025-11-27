from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

DB_PATH = Path("data/opcoes_snapshots.db")


@dataclass
class FeeSettings:
    equity_fixed: float = 0.0
    equity_percent: float = 0.0  # em % sobre o valor da operação
    option_fixed: float = 0.0
    option_percent_notional: float = 0.0  # em % sobre (strike * 100 * contratos)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def get_fee_settings() -> FeeSettings:
    """Carrega configuração de taxas. Se não existir, retorna zeros."""

    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()

    raw: Dict[str, str] = {str(r["key"]): str(r["value"]) for r in rows}

    def _parse(name: str) -> float:
        text = raw.get(name, "").strip()
        if not text:
            return 0.0
        # aceita vírgula ou ponto
        text = text.replace("%", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return 0.0

    return FeeSettings(
        equity_fixed=_parse("fee_equity_fixed"),
        equity_percent=_parse("fee_equity_percent"),
        option_fixed=_parse("fee_option_fixed"),
        option_percent_notional=_parse("fee_option_percent_notional"),
    )


def update_fee_settings(
    *,
    equity_fixed: float,
    equity_percent: float,
    option_fixed: float,
    option_percent_notional: float,
) -> None:
    """Atualiza configuração de taxas (substitui os valores atuais)."""

    conn = _connect()
    try:
        params = {
            "fee_equity_fixed": equity_fixed,
            "fee_equity_percent": equity_percent,
            "fee_option_fixed": option_fixed,
            "fee_option_percent_notional": option_percent_notional,
        }
        for key, value in params.items():
            conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )
        conn.commit()
    finally:
        conn.close()


__all__ = ["FeeSettings", "get_fee_settings", "update_fee_settings"]

