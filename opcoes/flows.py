from __future__ import annotations

from typing import Optional

from .db import db_transaction
from .finance import TransactionType, add_transaction
from .portfolio import add_position, close_position, get_position, update_position
from .utils import infer_option_type


class FlowError(RuntimeError):
    def __init__(self, message: str, *, underlying: Optional[str] = None) -> None:
        super().__init__(message)
        self.underlying = underlying


def assign_put(
    *,
    position_id: int,
    strike: float,
    qty: int,
    date: str,
) -> None:
    with db_transaction() as conn:
        pos = get_position(position_id, conn=conn)
        is_simulated = bool(pos["is_simulated"]) if pos else False

        close_position(
            position_id=position_id,
            exit_date=date,
            exit_price=0.0,
            exit_reason="Exercício",
            conn=conn,
        )

        cost = float(strike) * int(qty)
        add_transaction(
            date=date,
            type=TransactionType.ASSIGNMENT,
            amount=-cost,
            description=f"Exercício PUT {position_id} @ {strike}",
            position_id=position_id,
            is_simulated=is_simulated,
            conn=conn,
        )

        if pos:
            add_position(
                ticker=pos["underlying"],
                underlying=pos["underlying"],
                trade_date=date,
                qty=int(qty),
                entry_price=float(strike),
                fees=0.0,
                trade_type="stock",
                notes=f"Exercício da opção {pos['ticker']}",
                is_simulated=is_simulated,
                parent_position_id=position_id,
                strategy_tag="covered_call",
                conn=conn,
            )


def callaway(
    *,
    position_id: int,
    date: str,
) -> str:
    with db_transaction() as conn:
        call_pos = get_position(position_id, conn=conn)
        if not call_pos:
            raise FlowError("Posição não encontrada.")

        underlying = (call_pos.get("underlying") or "").strip().upper()
        is_simulated = bool(call_pos.get("is_simulated") or 0)

        if (call_pos.get("status") or "").strip().lower() != "open":
            raise FlowError("Posição não está aberta.", underlying=underlying)

        if infer_option_type(call_pos.get("ticker")) != "CALL":
            raise FlowError("Ticker não é CALL.", underlying=underlying)

        try:
            qty = int(call_pos.get("open_qty") or call_pos.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0

        strike = call_pos.get("strike")
        if qty <= 0 or strike is None:
            raise FlowError("Quantidade ou strike inválido.", underlying=underlying)

        lot_id = call_pos.get("parent_position_id")
        if lot_id is None:
            raise FlowError("Sem vínculo com lote.", underlying=underlying)

        lot_pos = get_position(int(lot_id), conn=conn)
        if not lot_pos:
            raise FlowError("Lote não encontrado.", underlying=underlying)

        if (lot_pos.get("status") or "").strip().lower() != "open":
            raise FlowError("Lote não está aberto.", underlying=underlying)

        if bool(lot_pos.get("is_simulated") or 0) != is_simulated:
            raise FlowError("Modo (real/simulado) divergente.", underlying=underlying)

        lot_ticker = (lot_pos.get("ticker") or "").strip().upper()
        if underlying and lot_ticker and lot_ticker != underlying:
            raise FlowError("Underlying divergente.", underlying=underlying)

        try:
            lot_open_qty = int(lot_pos.get("open_qty") or lot_pos.get("qty") or 0)
        except (TypeError, ValueError):
            lot_open_qty = 0

        if lot_open_qty < qty:
            raise FlowError("Quantidade do lote insuficiente.", underlying=underlying)

        try:
            strike_val = float(strike)
        except (TypeError, ValueError):
            raise FlowError("Strike inválido.", underlying=underlying)

        if lot_open_qty == qty:
            close_position(
                position_id=int(lot_id),
                exit_date=date,
                exit_price=strike_val,
                exit_reason="Exercício",
                conn=conn,
            )
        else:
            existing_partial_qty = int(lot_pos.get("partial_qty") or 0)
            existing_partial_price = lot_pos.get("partial_price")
            total_qty = int(lot_pos.get("qty") or 0)
            new_partial_qty = existing_partial_qty + qty
            if new_partial_qty > total_qty:
                raise FlowError("Quantidade parcial excede lote.", underlying=underlying)

            new_partial_price = strike_val
            if existing_partial_qty > 0 and existing_partial_price is not None:
                try:
                    new_partial_price = (
                        (float(existing_partial_price) * existing_partial_qty) + (strike_val * qty)
                    ) / new_partial_qty
                except Exception:
                    new_partial_price = strike_val

            update_position(
                position_id=int(lot_id),
                partial_qty=new_partial_qty,
                partial_price=float(new_partial_price),
                partial_date=date,
                exit_reason="Exercício",
                conn=conn,
            )
            if new_partial_qty == total_qty:
                close_position(
                    position_id=int(lot_id),
                    exit_date=date,
                    exit_price=strike_val,
                    exit_reason="Exercício",
                    conn=conn,
                )

        close_position(
            position_id=position_id,
            exit_date=date,
            exit_price=0.0,
            exit_reason="Exercício",
            conn=conn,
        )

        proceeds = strike_val * qty
        add_transaction(
            date=date,
            type=TransactionType.SELL,
            amount=proceeds,
            description=f"Venda (CALL exercida) {call_pos.get('ticker')} @ {strike_val:.2f}",
            position_id=position_id,
            is_simulated=is_simulated,
            conn=conn,
        )

    return underlying


__all__ = ["FlowError", "assign_put", "callaway"]
