import sqlite3
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from .config import get_db_path


class TransactionType(str, Enum):
    DEPOSIT = "DEPOSIT"      # Aporte novo
    WITHDRAWAL = "WITHDRAW"  # Retirada
    PREMIUM = "PREMIUM"      # Prêmio recebido de venda de opção
    ASSIGNMENT = "ASSIGN"    # Custo de exercício (compra da ação)
    BUY = "BUY"              # Compra direta de ativo
    DIVIDEND = "DIVIDEND"    # Dividendos recebidos


@dataclass
class Transaction:
    id: int
    date: str
    type: TransactionType
    amount: float
    description: Optional[str] = None
    position_id: Optional[int] = None  # Link opcional com uma posição específica


def _get_conn() -> sqlite3.Connection:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            position_id INTEGER
        )
        """
    )
    conn.commit()


def add_transaction(
    date: str,
    type: TransactionType,
    amount: float,
    description: str = None,
    position_id: int = None
) -> int:
    """Registra uma transação financeira."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO ledger (date, type, amount, description, position_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (date, type.value, amount, description, position_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_balance() -> float:
    """Retorna o saldo total atual (soma de todas as transações)."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT SUM(amount) as total FROM ledger").fetchone()
        return row["total"] if row and row["total"] is not None else 0.0
    finally:
        conn.close()


def get_monthly_premiums(limit_months: int = 12) -> List[dict]:
    """Retorna soma de prêmios agrupados por mês (YYYY-MM)."""
    conn = _get_conn()
    try:
        # Filtra apenas PREMIUM (vendas de opções)
        query = """
            SELECT strftime('%Y-%m', date) as month, SUM(amount) as total
            FROM ledger
            WHERE type = ?
            GROUP BY month
            ORDER BY month DESC
            LIMIT ?
        """
        rows = conn.execute(query, (TransactionType.PREMIUM.value, limit_months)).fetchall()
        # Inverte para ordem cronológica (gráfico)
        results = [{"month": r["month"], "total": r["total"]} for r in rows]
        return results[::-1] 
    finally:
        conn.close()


def get_transactions(limit: int = 50) -> List[Transaction]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ledger ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Transaction(
                id=r["id"],
                date=r["date"],
                type=TransactionType(r["type"]),
                amount=r["amount"],
                description=r["description"],
                position_id=r["position_id"],
            )
            for r in rows
        ]
    finally:
        conn.close()
