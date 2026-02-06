from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict

from .config import get_db_path


@dataclass
class FeeSettings:
    equity_fixed: float = 0.0
    equity_percent: float = 0.0  # em % sobre o valor da operação
    option_fixed: float = 0.0
    option_percent_notional: float = 0.0  # em % sobre (strike * 100 * contratos)


@dataclass
class StrategySettings:
    min_score: int = 8
    limit_opportunities: int = 30
    recurring_days: int = 30


@dataclass
class CashCoveredPutSettings:
    underlying: str = "PETR4"
    min_yield_pct: float = 1.0
    min_buffer_pct: float = 5.0
    min_days: int = 7
    max_days: int = 120
    contract_size: int = 100
    limit: int = 50
    cash_mode: str = "real"
    buyback_target_pct: float = 50.0


@dataclass
class CoveredCallSettings:
    underlying: str = "CMIG4"
    min_extrinsic: float = 2.0
    min_days: int = 30
    max_days: int = 200
    min_dist_strike: float = 1.0
    buyback_target_pct: float = 50.0


@dataclass
class FundamentusSettings:
    target_yield_pct: float = 8.0
    put_distance_limit_pct: float = 15.0
    put_min_premium_pct: float = 0.5
    put_target_monthly_yield_pct: float = 1.0
    put_min_score: float = 4.0


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
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


def get_strategy_settings() -> StrategySettings:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()

    raw: Dict[str, str] = {str(r["key"]): str(r["value"]) for r in rows}

    def _parse_int(name: str, default: int) -> int:
        text = raw.get(name, "").strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            return default

    return StrategySettings(
        min_score=_parse_int("strat_min_score", 8),
        limit_opportunities=_parse_int("strat_limit_opportunities", 30),
        recurring_days=_parse_int("strat_recurring_days", 30),
    )


def get_cash_put_settings() -> CashCoveredPutSettings:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()

    raw: Dict[str, str] = {str(r["key"]): str(r["value"]) for r in rows}

    def _parse_float(name: str, default: float) -> float:
        text = raw.get(name, "").strip()
        if not text:
            return default
        text = text.replace("%", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return default

    def _parse_int(name: str, default: int) -> int:
        text = raw.get(name, "").strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            return default

    underlying = raw.get("cash_put_underlying", "").strip().upper() or "PETR4"
    cash_mode = raw.get("cash_put_cash_mode", "real").strip().lower() or "real"

    return CashCoveredPutSettings(
        underlying=underlying,
        min_yield_pct=_parse_float("cash_put_min_yield_pct", 1.0),
        min_buffer_pct=_parse_float("cash_put_min_buffer_pct", 5.0),
        min_days=_parse_int("cash_put_min_days", 7),
        max_days=_parse_int("cash_put_max_days", 120),
        contract_size=_parse_int("cash_put_contract_size", 100),
        limit=_parse_int("cash_put_limit", 50),
        cash_mode=cash_mode,
        buyback_target_pct=_parse_float("cash_put_buyback_target_pct", 50.0),
    )


def get_covered_call_settings() -> CoveredCallSettings:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()

    raw: Dict[str, str] = {str(r["key"]): str(r["value"]) for r in rows}

    def _parse_float(name: str, default: float) -> float:
        text = raw.get(name, "").strip()
        if not text:
            return default
        text = text.replace("%", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return default

    def _parse_int(name: str, default: int) -> int:
        text = raw.get(name, "").strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            return default

    underlying = raw.get("ccall_underlying", "").strip().upper() or "CMIG4"

    return CoveredCallSettings(
        underlying=underlying,
        min_extrinsic=_parse_float("ccall_min_extrinsic", 2.0),
        min_days=_parse_int("ccall_min_days", 30),
        max_days=_parse_int("ccall_max_days", 200),
        min_dist_strike=_parse_float("ccall_min_dist_strike", 1.0),
        buyback_target_pct=_parse_float("ccall_buyback_target_pct", 50.0),
    )


def get_fundamentus_settings() -> FundamentusSettings:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()

    raw: Dict[str, str] = {str(r["key"]): str(r["value"]) for r in rows}

    def _parse_float(name: str, default: float) -> float:
        text = raw.get(name, "").strip()
        if not text:
            return default
        text = text.replace("%", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return default

    return FundamentusSettings(
        target_yield_pct=_parse_float("fund_target_yield_pct", 8.0),
        put_distance_limit_pct=_parse_float("fund_put_distance_limit_pct", 15.0),
        put_min_premium_pct=_parse_float("fund_put_min_premium_pct", 0.5),
        put_target_monthly_yield_pct=_parse_float("fund_put_target_monthly_yield_pct", 1.0),
        put_min_score=_parse_float("fund_put_min_score", 4.0),
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


def update_strategy_settings(
    *,
    min_score: int,
    limit_opportunities: int,
    recurring_days: int,
) -> None:
    conn = _connect()
    try:
        params = {
            "strat_min_score": min_score,
            "strat_limit_opportunities": limit_opportunities,
            "strat_recurring_days": recurring_days,
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


def update_cash_put_settings(
    *,
    underlying: str,
    min_yield_pct: float,
    min_buffer_pct: float,
    min_days: int,
    max_days: int,
    contract_size: int,
    limit: int,
    cash_mode: str,
    buyback_target_pct: float,
) -> None:
    conn = _connect()
    try:
        params = {
            "cash_put_underlying": (underlying or "").strip().upper() or "PETR4",
            "cash_put_min_yield_pct": float(min_yield_pct),
            "cash_put_min_buffer_pct": float(min_buffer_pct),
            "cash_put_min_days": int(min_days),
            "cash_put_max_days": int(max_days),
            "cash_put_contract_size": int(contract_size),
            "cash_put_limit": int(limit),
            "cash_put_cash_mode": (cash_mode or "real").strip().lower() or "real",
            "cash_put_buyback_target_pct": float(buyback_target_pct),
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


def update_covered_call_settings(
    *,
    underlying: str,
    min_extrinsic: float,
    min_days: int,
    max_days: int,
    min_dist_strike: float,
    buyback_target_pct: float,
) -> None:
    conn = _connect()
    try:
        params = {
            "ccall_underlying": (underlying or "").strip().upper() or "CMIG4",
            "ccall_min_extrinsic": float(min_extrinsic),
            "ccall_min_days": int(min_days),
            "ccall_max_days": int(max_days),
            "ccall_min_dist_strike": float(min_dist_strike),
            "ccall_buyback_target_pct": float(buyback_target_pct),
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


def update_fundamentus_settings(
    *,
    target_yield_pct: float,
    put_distance_limit_pct: float,
    put_min_premium_pct: float,
    put_target_monthly_yield_pct: float,
    put_min_score: float,
) -> None:
    conn = _connect()
    try:
        params = {
            "fund_target_yield_pct": float(target_yield_pct),
            "fund_put_distance_limit_pct": float(put_distance_limit_pct),
            "fund_put_min_premium_pct": float(put_min_premium_pct),
            "fund_put_target_monthly_yield_pct": float(put_target_monthly_yield_pct),
            "fund_put_min_score": float(put_min_score),
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


__all__ = [
    "FeeSettings",
    "get_fee_settings",
    "update_fee_settings",
    "StrategySettings",
    "get_strategy_settings",
    "update_strategy_settings",
    "CashCoveredPutSettings",
    "get_cash_put_settings",
    "update_cash_put_settings",
    "CoveredCallSettings",
    "get_covered_call_settings",
    "update_covered_call_settings",
    "FundamentusSettings",
    "get_fundamentus_settings",
    "update_fundamentus_settings",
]
