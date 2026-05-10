import sys
import os

# Add the built module to the path if necessary, but we will run this from the venv
# with strategies/crypto added implicitly or explicitly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'strategies', 'crypto')))

try:
    import _cpp_lob
except ImportError as e:
    print(f"Error importing _cpp_lob: {e}")
    print("Please ensure you have built the C++ module:")
    print("  uv pip install -e \".[dev]\"")
    print("  cmake -S . -B build && cmake --build build")
    sys.exit(1)

def main():
    print("Starting Microsecond Event-Driven C++ Backtester...")
    
    # Initialize the Backtester
    backtester = _cpp_lob.Backtester()

    # Add dummy historical LOB snapshots
    # (timestamp_mu, bid_price, bid_volume, ask_price, ask_volume)
    print("Adding historical MARKET_DATA events to queue...")
    backtester.add_market_data(1000000, 100.0, 10.0, 101.0, 5.0)
    backtester.add_market_data(1002000, 100.5, 12.0, 101.0, 5.0)
    backtester.add_market_data(1005000, 101.0, 8.0, 102.0, 15.0)

    # Place some orders
    # (timestamp_mu, order_id, side (1=Buy, -1=Sell), quantity)
    print("Placing ORDER events...")
    backtester.place_order(1001000, 1, 1, 1.0)   # Buy order before the second tick
    backtester.place_order(1004000, 2, -1, 1.0)  # Sell order before the third tick

    # Run the backtest loop
    print("Running event loop (simulating latency & fills)...")
    backtester.run()

    # Retrieve and print trades
    print("\n--- Final Trade Ledger ---")
    trades = backtester.get_trades()
    for t in trades:
        side_str = "Buy" if t.side == 1 else "Sell"
        print(f"Time: {t.timestamp_mu}us | Order {t.order_id} | {side_str} {t.quantity} @ {t.price:.2f}")

    # Final PnL calculation
    pnl = backtester.get_pnl()
    print(f"\nFinal PnL (including Mark-to-Market): {pnl:.2f}")

if __name__ == '__main__':
    main()
