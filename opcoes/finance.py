import sqlite3
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

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
    _ensure_table(conn, commit=True)
    return conn


def _resolve_conn(conn: Optional[sqlite3.Connection]) -> tuple[sqlite3.Connection, bool]:
    if conn is None:
        return _get_conn(), True
    _ensure_table(conn, commit=not conn.in_transaction)
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    return conn, False


def _ensure_table(conn: sqlite3.Connection, *, commit: bool) -> None:
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
    if commit:
        conn.commit()


def add_transaction(
    date: str,
    type: TransactionType,
    amount: float,
    description: str = None,
    position_id: int = None,
    is_simulated: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Registra uma transação financeira."""
    db, owns_conn = _resolve_conn(conn)
    try:
        cur = db.execute(
            """
            INSERT INTO ledger (date, type, amount, description, position_id, is_simulated)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (date, type.value, amount, description, position_id, 1 if is_simulated else 0),
        )
        if owns_conn:
            db.commit()
        return cur.lastrowid
    finally:
        if owns_conn:
            db.close()


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


def get_monthly_premiums(
    limit_months: int = 12,
    *,
    is_simulated: Optional[bool] = False,
    include_darf: bool = False,
) -> List[dict]:
    """Retorna soma de prêmios agrupados por mês (YYYY-MM).

    - is_simulated: False (padrão) -> somente real; True -> somente simulado; None -> ambos.
    - include_darf: soma também DARF (negativo) para exibir líquido (PREMIUM - DARF).
    """
    conn = _get_conn()
    try:
        where: list[str] = []
        params: list[object] = []
        if include_darf:
            # DARF de provisão é lançado com position_id; evita misturar com DARF "pago" (manual).
            where.append("(type = ? OR (type = ? AND position_id IS NOT NULL))")
            params.extend([TransactionType.PREMIUM.value, TransactionType.DARF.value])
        else:
            where.append("type = ?")
            params.append(TransactionType.PREMIUM.value)
        if is_simulated is not None:
            where.append("COALESCE(is_simulated, 0) = ?")
            params.append(1 if is_simulated else 0)

        query = f"""
            SELECT strftime('%Y-%m', date) as month, SUM(amount) as total
            FROM ledger
            WHERE {' AND '.join(where)}
            GROUP BY month
            ORDER BY month DESC
            LIMIT ?
        """
        rows = conn.execute(query, (*params, limit_months)).fetchall()
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


def get_ledger_sums_by_position(
    *,
    types: Optional[List[TransactionType]] = None,
    is_simulated: Optional[bool] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[int, Dict[str, float]]:
    db, owns_conn = _resolve_conn(conn)
    try:
        where: list[str] = ["position_id IS NOT NULL"]
        params: list[object] = []
        if types:
            placeholders = ",".join("?" for _ in types)
            where.append(f"type IN ({placeholders})")
            params.extend([t.value if isinstance(t, TransactionType) else str(t) for t in types])
        if is_simulated is not None:
            where.append("COALESCE(is_simulated, 0) = ?")
            params.append(1 if is_simulated else 0)

        query = f"""
            SELECT position_id, type, SUM(amount) AS total
            FROM ledger
            WHERE {' AND '.join(where)}
            GROUP BY position_id, type
        """
        rows = db.execute(query, params).fetchall()
        result: Dict[int, Dict[str, float]] = {}
        for r in rows:
            pid = int(r["position_id"])
            if pid not in result:
                result[pid] = {}
            result[pid][str(r["type"])] = float(r["total"] or 0.0)
        return result
    finally:
        if owns_conn:
            db.close()


def recalc_position_premium_and_darf(
    *,
    position_id: int,
    trade_date: str,
    ticker: str,
    qty: int,
    premium_amount: float,
    trade_type: str,
    is_simulated: bool,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, float]:
    """Recalcula (upsert) prêmio e DARF vinculados a uma posição."""
    db, owns_conn = _resolve_conn(conn)
    try:
        cur = db.execute(
            """
            SELECT id, type
            FROM ledger
            WHERE position_id = ? AND type IN (?, ?)
            ORDER BY id ASC
            """,
            (int(position_id), TransactionType.PREMIUM.value, TransactionType.DARF.value),
        )
        rows = cur.fetchall()
        by_type: Dict[str, List[int]] = {TransactionType.PREMIUM.value: [], TransactionType.DARF.value: []}
        for r in rows:
            by_type[str(r["type"])].append(int(r["id"]))

        # PREMIUM
        if premium_amount > 0:
            if by_type[TransactionType.PREMIUM.value]:
                tx_id = by_type[TransactionType.PREMIUM.value][0]
                db.execute(
                    """
                    UPDATE ledger
                    SET date = ?, amount = ?, description = ?, is_simulated = ?
                    WHERE id = ?
                    """,
                    (
                        trade_date,
                        float(premium_amount),
                        f"Prêmio {ticker} ({int(qty)}x)",
                        1 if is_simulated else 0,
                        tx_id,
                    ),
                )
                extra_ids = by_type[TransactionType.PREMIUM.value][1:]
                if extra_ids:
                    db.execute(
                        f"DELETE FROM ledger WHERE id IN ({','.join('?' for _ in extra_ids)})",
                        extra_ids,
                    )
            else:
                add_transaction(
                    date=trade_date,
                    type=TransactionType.PREMIUM,
                    amount=float(premium_amount),
                    description=f"Prêmio {ticker} ({int(qty)}x)",
                    position_id=position_id,
                    is_simulated=is_simulated,
                    conn=db,
                )
        else:
            ids = by_type[TransactionType.PREMIUM.value]
            if ids:
                db.execute(
                    f"DELETE FROM ledger WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )

        # DARF
        aliquota_opts = 0.20 if "day" in (trade_type or "").lower() else 0.15
        base_ir = max(0.0, float(premium_amount))
        darf_amount = -round(base_ir * aliquota_opts, 2) if base_ir > 0 else 0.0

        if darf_amount != 0.0:
            if by_type[TransactionType.DARF.value]:
                tx_id = by_type[TransactionType.DARF.value][0]
                db.execute(
                    """
                    UPDATE ledger
                    SET date = ?, amount = ?, description = ?, is_simulated = ?
                    WHERE id = ?
                    """,
                    (
                        trade_date,
                        float(darf_amount),
                        f"Provisão DARF {ticker} ({int(aliquota_opts*100)}%)",
                        1 if is_simulated else 0,
                        tx_id,
                    ),
                )
                extra_ids = by_type[TransactionType.DARF.value][1:]
                if extra_ids:
                    db.execute(
                        f"DELETE FROM ledger WHERE id IN ({','.join('?' for _ in extra_ids)})",
                        extra_ids,
                    )
            else:
                add_transaction(
                    date=trade_date,
                    type=TransactionType.DARF,
                    amount=float(darf_amount),
                    description=f"Provisão DARF {ticker} ({int(aliquota_opts*100)}%)",
                    position_id=position_id,
                    is_simulated=is_simulated,
                    conn=db,
                )
        else:
            ids = by_type[TransactionType.DARF.value]
            if ids:
                db.execute(
                    f"DELETE FROM ledger WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )

        if owns_conn:
            db.commit()

        return {
            "premium": float(premium_amount),
            "darf": float(darf_amount),
        }
    finally:
        if owns_conn:
            db.close()


def get_premium_position_ids(position_ids: Optional[List[int]] = None) -> set[int]:
    if position_ids is not None and not position_ids:
        return set()
    conn = _get_conn()
    try:
        params: list[object] = [TransactionType.PREMIUM.value]
        query = "SELECT DISTINCT position_id FROM ledger WHERE type = ? AND position_id IS NOT NULL"
        if position_ids:
            placeholders = ",".join("?" for _ in position_ids)
            query += f" AND position_id IN ({placeholders})"
            params.extend([int(pid) for pid in position_ids])
        rows = conn.execute(query, params).fetchall()
        return {int(r["position_id"]) for r in rows if r["position_id"] is not None}
    finally:
        conn.close()


def has_position_premium(position_id: int) -> bool:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM ledger WHERE type = ? AND position_id = ? LIMIT 1",
            (TransactionType.PREMIUM.value, int(position_id)),
        ).fetchone()
        return row is not None
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
