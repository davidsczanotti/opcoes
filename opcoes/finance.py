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
    SELL = "SELL"            # Venda direta de ativo
    DARF = "DARF"            # Provisão/pagamento de IR (DARF)
    DIVIDEND = "DIVIDEND"    # Dividendos recebidos


@dataclass
class Transaction:
    id: int
    date: str
    type: TransactionType
    amount: float
    description: Optional[str] = None
    position_id: Optional[int] = None  # Link opcional com uma posição específica
    is_simulated: bool = False


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
    # Garantir colunas extras em versões antigas do banco
    existing = {
        row[1]
        for row in conn.execute('PRAGMA table_info("ledger")').fetchall()
        if row and len(row) > 1
    }
    if "is_simulated" not in existing:
        conn.execute('ALTER TABLE ledger ADD COLUMN "is_simulated" INTEGER DEFAULT 0')
    conn.commit()


def add_transaction(
    date: str,
    type: TransactionType,
    amount: float,
    description: str = None,
    position_id: int = None,
    is_simulated: bool = False,
) -> int:
    """Registra uma transação financeira."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO ledger (date, type, amount, description, position_id, is_simulated)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (date, type.value, amount, description, position_id, 1 if is_simulated else 0),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_balance(mode: str = "all") -> float:
    """
    Retorna o saldo atual.
    mode: "all" (padrão), "real" (apenas não simuladas), "simulated" (apenas fictícias).
    """
    mode = (mode or "all").lower()
    conn = _get_conn()
    try:
        where = ""
        params: list[object] = []
        if mode == "real":
            where = "WHERE is_simulated = 0"
        elif mode == "simulated":
            where = "WHERE is_simulated = 1"
        row = conn.execute(f"SELECT SUM(amount) as total FROM ledger {where}", params).fetchone()
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
            WHERE type = ? AND COALESCE(is_simulated, 0) = 0
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
                is_simulated=bool(r["is_simulated"] or 0) if "is_simulated" in r.keys() else False,
            )
            for r in rows
        ]
    finally:
        conn.close()


def update_transaction(
    tx_id: int,
    *,
    date: Optional[str] = None,
    type: Optional[TransactionType] = None,
    amount: Optional[float] = None,
    description: Optional[str] = None,
    is_simulated: Optional[bool] = None,
) -> None:
    """Atualiza campos básicos de uma transação existente."""
    fields = []
    params: list[object] = []
    if date is not None:
        fields.append("date = ?")
        params.append(date)
    if type is not None:
        if isinstance(type, TransactionType):
            type_val = type.value
        else:
            type_val = str(type)
        fields.append("type = ?")
        params.append(type_val)
    if amount is not None:
        fields.append("amount = ?")
        params.append(float(amount))
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if is_simulated is not None:
        fields.append("is_simulated = ?")
        params.append(1 if is_simulated else 0)
    if not fields:
        return

    params.append(int(tx_id))
    conn = _get_conn()
    try:
        cur = conn.execute(
            f"UPDATE ledger SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"Transação {tx_id} não encontrada.")
    finally:
        conn.close()


def delete_transaction(tx_id: int) -> None:
    """Remove uma transação do ledger."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM ledger WHERE id = ?", (int(tx_id),))
        conn.commit()
    finally:
        conn.close()
