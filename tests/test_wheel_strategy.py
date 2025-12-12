import os
import shutil
import sqlite3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from opcoes import portfolio, finance
from opcoes.strategies import cash_covered_put, covered_call
from opcoes.config import get_db_path

def setup_test_db():
    """Creates a temp copy of the real DB for testing."""
    real_db = Path("data/opcoes_snapshots.db")
    if not real_db.exists():
        print("Skipping test: Real DB not found at data/opcoes_snapshots.db")
        sys.exit(0)
    
    temp_db = Path("tests/temp_wheel_test.db")
    if temp_db.exists():
        os.remove(temp_db)
        
    shutil.copy(real_db, temp_db)
    os.environ["OPCOES_DB_PATH"] = str(temp_db)
    print(f"Test DB setup at {temp_db}")
    return temp_db

def find_test_assets():
    """Finds a valid Underlying and Option pair from the DB."""
    conn = sqlite3.connect(get_db_path())
    cursor = conn.cursor()
    
    # Find a liquid underlying
    cursor.execute("SELECT DISTINCT underlying FROM option_snapshots LIMIT 1")
    row = cursor.fetchone()
    if not row:
        print("No data in DB.")
        sys.exit(1)
    underlying = row[0]
    
    # Find a PUT option for this underlying
    cursor.execute(
        "SELECT ticker, strike FROM option_snapshots WHERE underlying = ? AND option_type = 'PUT' LIMIT 1", 
        (underlying,)
    )
    put_row = cursor.fetchone()
    
    # Find a CALL option for this underlying
    cursor.execute(
        "SELECT ticker, strike FROM option_snapshots WHERE underlying = ? AND option_type = 'CALL' LIMIT 1", 
        (underlying,)
    )
    call_row = cursor.fetchone()
    
    conn.close()
    
    if not put_row or not call_row:
        print(f"Could not find options for {underlying}")
        sys.exit(1)
        
    def to_float(val):
        if isinstance(val, str):
            return float(val.replace(',', '.'))
        return float(val)

    return underlying, put_row[0], to_float(put_row[1]), call_row[0], to_float(call_row[1])

def run_simulation():
    temp_db_path = setup_test_db()
    
    try:
        underlying, put_ticker, put_strike, call_ticker, call_strike = find_test_assets()
        print(f"\n--- Starting Wheel Strategy Simulation for {underlying} ---")
        print(f"Put: {put_ticker} (Strike {put_strike})")
        print(f"Call: {call_ticker} (Strike {call_strike})")
        
        # 1. SELL PUT (Simonulado)
        print("\n[Step 1] Selling Cash-Covered Put (Simulated)...")
        put_qty = 100
        put_price = 1.50
        put_id = portfolio.add_position(
            ticker=put_ticker,
            underlying=underlying,
            trade_date="2025-01-01",
            qty=put_qty,
            entry_price=put_price,
            trade_type="swing",
            is_simulated=True,
            strategy_tag="cash_put"
        )
        print(f"Position ID: {put_id} created.")
        
        # Verify in Context
        ctx_put = cash_covered_put.get_cash_covered_put_context({"underlying": underlying})
        sim_puts = ctx_put.get("puts_simulated", [])
        found_put = any(p["id"] == put_id for p in sim_puts)
        print(f"Found in Cash-Covered Put Dashboard? {found_put}")
        if not found_put:
            print("ERROR: Put not found in simulated list!")
            
        # 2. ASSIGNMENT (Exercise)
        print("\n[Step 2] Simulating Assignment (Put Exercised)...")
        # Close Put
        portfolio.close_position(
            position_id=put_id,
            exit_date="2025-01-20",
            exit_price=0.0, # Kept premium
            exit_reason="exercicio"
        )
        # Open Stock
        stock_id = portfolio.add_position(
            ticker=underlying,
            underlying=underlying,
            trade_date="2025-01-20",
            qty=put_qty,
            entry_price=put_strike,
            trade_type="stock",
            is_simulated=True,
            parent_position_id=put_id,
            notes="Exercised from Put"
        )
        print(f"Stock Position ID: {stock_id} created (Parent: {put_id}).")
        
        # Verify Stock in Portfolio
        pos = portfolio.get_position(stock_id)
        print(f"Stock Position: {pos['ticker']} | Qty: {pos['qty']} | Simulated: {pos['is_simulated']}")
        
        # 3. SELL COVERED CALL
        print("\n[Step 3] Selling Covered Call (Simulated)...")
        call_qty = 100
        call_price = 2.00
        call_id = portfolio.add_position(
            ticker=call_ticker,
            underlying=underlying,
            trade_date="2025-01-21",
            qty=call_qty,
            entry_price=call_price,
            trade_type="swing",
            is_simulated=True,
            parent_position_id=stock_id,
            strategy_tag="covered_call"
        )
        print(f"Call Position ID: {call_id} created (Parent Stock: {stock_id}).")
        
        # Verify in Covered Call Context
        ctx_call = covered_call.get_covered_call_context({"underlying": underlying})
        sim_calls = ctx_call.get("covered_sim", [])
        found_call = any(p["ticker"] == call_ticker for p in sim_calls) # id might not be in this specific view dict depending on implementation
        
        print(f"Found in Covered Call Dashboard? {found_call}")
        if found_call:
            # Check if it sees the stock coverage
            stock_sim = ctx_call.get("stock_sim", {})
            print(f"Stock Sim Stats: {stock_sim}")
            print(f"Covered: {stock_sim.get('shares_covered')} (Expected {call_qty})")
        else:
             print("ERROR: Call not found in simulated list!")

        print("\n--- Simulation Complete ---")
        
    finally:
        # Cleanup
        if temp_db_path.exists():
            os.remove(temp_db_path)
            print("\nTemp DB removed.")

if __name__ == "__main__":
    run_simulation()
