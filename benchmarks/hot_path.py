"""
Hot-path benchmark for chiron's CPU-bound components.

Measures per-call latency for:
  - RollingWindow.push() under realistic tick rates
  - spot_to_implied_prob() and bracket_prob()
  - capped_kelly()
  - Full feature computation pipeline

Run from repo root:
    python3 -m benchmarks.hot_path

Interpretation:
  RollingWindow.push  < 5 µs  → Python is fine, no C extension needed
  RollingWindow.push  > 50 µs → Consider PyO3/pybind11 Welford extension
  N(d2) pricing       < 2 µs  → Already fast (math.erfc is a C call)
  Full feature cycle  < 20 µs → Plenty of headroom for 500 ticks/sec

Context: at 500 ticks/sec per symbol × 2 symbols, feature computation budget
is 1ms total per second. If push() > 1µs, you're at 50% CPU on features alone
before any I/O (REST, WS). Profile before assuming C++ is needed.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

TICK_COUNT = 100_000
WARMUP = 1_000


def _bench(label: str, fn, n: int = TICK_COUNT) -> None:
    # Warmup
    for _ in range(WARMUP):
        fn()
    # Timed run
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    elapsed = time.perf_counter() - t0
    per_call_us = (elapsed / n) * 1e6
    total_ms = elapsed * 1e3
    print(f"  {label:<45} {per_call_us:7.3f} µs/call   ({total_ms:.1f} ms total, n={n:,})")


def bench_rolling_window() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from core.features import RollingWindow

    window = RollingWindow(max_age_seconds=60.0)
    ts = time.monotonic()
    price = 70_000.0

    def push_tick():
        nonlocal ts, price
        ts += 0.002  # 500 Hz tick rate
        price += price * 0.0001 * (1 if ts % 2 < 1 else -1)
        window.push(price, ts)

    def read_variance():
        return window.variance

    def read_vol():
        return window.realized_vol()

    # Pre-fill the window with realistic data before benchmarking reads
    for _ in range(5_000):
        push_tick()

    print("\n── RollingWindow ──────────────────────────────────────────────")
    _bench("push() @ 500 Hz (60s window, ~30k entries)", push_tick)
    _bench("variance (read, warmed)", read_variance)
    _bench("realized_vol() (read, warmed)", read_vol)

    # Benchmark the dirty recompute path (happens when window expires old entries)
    window2 = RollingWindow(max_age_seconds=1.0)  # 1s window — expires fast
    ts2 = time.monotonic()

    def push_with_expiry():
        nonlocal ts2
        ts2 += 0.01  # 100 Hz — window expires every 10 pushes
        window2.push(70_000.0, ts2)
        _ = window2.variance  # trigger recompute on dirty

    print()
    _bench("push() + variance read w/ frequent expiry (dirty recompute)", push_with_expiry, n=10_000)


def bench_pricing() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from core.pricing import spot_to_implied_prob, bracket_prob

    spot = 70_000.0
    strike = 71_000.0
    vol = 0.45
    hours = 2.0

    print("\n── Pricing (Black-Scholes N(d2)) ─────────────────────────────")
    _bench("spot_to_implied_prob(spot, strike, 2h, vol=0.45)",
           lambda: spot_to_implied_prob(spot, strike, hours, vol))
    _bench("bracket_prob(spot, floor, cap, 2h, vol=0.45)",
           lambda: bracket_prob(spot, 69_000.0, 71_000.0, hours, vol))
    _bench("spot_to_implied_prob (near-expiry, t=5min)",
           lambda: spot_to_implied_prob(spot, strike, 5 / 60, vol))


def bench_kelly() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from core.kelly import capped_kelly, position_size

    print("\n── Kelly Sizing ───────────────────────────────────────────────")
    _bench("capped_kelly(model_prob=0.65, market_price=0.50)",
           lambda: capped_kelly(0.65, 0.50))
    _bench("position_size(model_prob=0.65, price=0.50, bankroll=100_000)",
           lambda: position_size(0.65, 0.50, 100_000.0))


def bench_full_pipeline() -> None:
    """End-to-end: push tick → compute features → price one market."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from datetime import datetime, timezone
    from core.features import RollingWindow, compute_features
    from core.models import Tick
    from core.pricing import spot_to_implied_prob
    from core.kelly import capped_kelly

    short_win = RollingWindow(max_age_seconds=60.0)
    long_win = RollingWindow(max_age_seconds=900.0)

    ts_base = time.monotonic()
    price = 70_000.0
    counter = [0]

    def full_cycle():
        counter[0] += 1
        ts = ts_base + counter[0] * 0.002
        nonlocal price
        price *= 1 + 0.00005 * math.sin(counter[0] * 0.1)

        tick = Tick(
            exchange="binance",
            symbol="BTCUSDT",
            price=price,
            timestamp=datetime.now(tz=timezone.utc),
        )
        short_win.push(price, ts)
        long_win.push(price, ts)
        fv = compute_features(short_win, tick, long_window=long_win)
        if fv is not None:
            prob = spot_to_implied_prob(price, 71_000.0, 2.0, fv.realized_vol_long or 0.45)
            _ = capped_kelly(prob, 0.50)

    # Warmup
    for _ in range(2_000):
        full_cycle()
    counter[0] = 0

    print("\n── Full Pipeline (tick → features → N(d2) → Kelly) ───────────")
    _bench("push + compute_features + N(d2) + Kelly (one market)", full_cycle)


def main() -> None:
    print("chiron hot-path benchmark")
    print(f"{'─' * 65}")
    print("n=100,000 calls per bench (except where noted)\n")

    bench_rolling_window()
    bench_pricing()
    bench_kelly()
    bench_full_pipeline()

    print(f"\n{'─' * 65}")
    print("Decision guide:")
    print("  RollingWindow.push < 5µs  → Python is sufficient")
    print("  RollingWindow.push > 50µs → Write PyO3 Welford extension")
    print("  Full pipeline     < 20µs  → Headroom for 500 ticks/sec × 2 symbols")


if __name__ == "__main__":
    main()
