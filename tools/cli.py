"""
CLI entry point for Quant.

Usage:
    quant scan                                 # top active Kalshi markets by volume
    quant evaluate "Will Fed cut rates?"       # evaluate with live Kalshi odds
    quant evaluate "..." --odds 0.42 --model-prob 0.60  # provide your own probability
    quant demo                                 # full pipeline on the top market
    quant paper                                # start autonomous paper trading loop
    quant paper --bankroll 1000 --cycle 120    # custom bankroll + faster cycles
    quant history                              # show recent paper trades
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from latency.tools.pipeline import Pipeline


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="quant",
        description="Multi-agent prediction market trading framework (Kalshi)",
    )
    subparsers = parser.add_subparsers(dest="command")

    eval_parser = subparsers.add_parser("evaluate", help="Evaluate a market opportunity")
    eval_parser.add_argument("question", help="Market question (partial match against live Kalshi markets)")
    eval_parser.add_argument("--odds", type=float, default=None, help="Override live odds (0-1)")
    eval_parser.add_argument("--model-prob", type=float, default=None, help="Your model probability")
    eval_parser.add_argument("--bankroll", type=float, default=100_000.0, help="Bankroll in USDC")

    scan_parser = subparsers.add_parser("scan", help="Scan top Kalshi markets by 24h volume")
    scan_parser.add_argument("--limit", type=int, default=10, help="Number of markets to show")
    scan_parser.add_argument("--min-volume", type=float, default=1000, help="Min 24h volume filter")

    subparsers.add_parser("demo", help="Run a full pipeline on the top live Kalshi market")

    paper_parser = subparsers.add_parser("paper", help="Start autonomous paper trading loop")
    paper_parser.add_argument("--bankroll", type=float, default=100_000.0, help="Starting bankroll in USDC")
    paper_parser.add_argument("--cycle", type=int, default=300, help="Seconds between scan cycles")
    paper_parser.add_argument("--scan-limit", type=int, default=30, help="Markets to scan per cycle")
    paper_parser.add_argument("--min-edge", type=float, default=0.04, help="Minimum edge to trade")
    paper_parser.add_argument("--once", action="store_true", help="Run a single cycle then exit")

    subparsers.add_parser("history", help="Show recent paper trade history")

    args = parser.parse_args()

    if args.command == "evaluate":
        _cmd_evaluate(args)
    elif args.command == "scan":
        _cmd_scan(args)
    elif args.command == "demo":
        _cmd_demo()
    elif args.command == "paper":
        _cmd_paper(args)
    elif args.command == "history":
        _cmd_history()
    else:
        parser.print_help()


def _cmd_evaluate(args: argparse.Namespace) -> None:
    matched = asyncio.run(_find_market_async(args.question))
    if matched is None and args.odds is None:
        print(f"  No live Kalshi market matched: {args.question!r}")
        print("  Try 'quant scan' to see top markets, or pass --odds explicitly.")
        sys.exit(1)

    question = matched["title"] if matched else args.question
    live_odds = args.odds if args.odds is not None else matched["implied_prob"] if matched else None

    if live_odds is None:
        print(f"  Could not resolve live odds for: {question!r}")
        print("  Pass --odds explicitly.")
        sys.exit(1)

    pipeline = Pipeline(bankroll=args.bankroll)
    result = pipeline.evaluate_sync(
        market_question=question,
        odds=live_odds,
        model_prob=args.model_prob,
    )
    _print_result(result, live_note=args.odds is None)


def _cmd_scan(args: argparse.Namespace) -> None:
    print(f"Scanning top Kalshi markets by 24h volume (min ${args.min_volume:,.0f})...\n")
    markets = asyncio.run(_fetch_top_markets_async(args.limit, args.min_volume))

    if not markets:
        print("  No markets returned. Check KALSHI_API_KEY is set (auth improves market access).")
        return

    print(f"  {'#':>2}  {'24h Vol':>12}  {'Liquidity':>11}  {'YES mid':>8}  {'Spread':>7}  Market")
    print("  " + "─" * 80)
    for i, m in enumerate(markets, 1):
        print(
            f"  {i:>2}. ${m.volume_24h:>11,.0f}  "
            f"${m.liquidity:>10,.0f}  "
            f"{m.implied_prob:>8.3f}  "
            f"{m.spread_pct:>7.1%}  "
            f"{m.title[:55]}"
        )

    print(f"\n  {len(markets)} markets shown.")
    print("  Run 'quant evaluate \"<question>\"' to analyze one.")


def _cmd_demo() -> None:
    print("Quant Demo — Live Pipeline on Top Kalshi Market\n")
    markets = asyncio.run(_fetch_top_markets_async(25, min_volume=1000))
    if not markets:
        print("  Could not fetch markets. Check connection and KALSHI_API_KEY.")
        sys.exit(1)

    # Pick first market with tradeable pricing (not near 0 or 1)
    top = next(
        (m for m in markets if 0.10 <= m.implied_prob <= 0.90 and m.spread_pct < 0.10),
        None,
    )
    if top is None:
        top = markets[0]

    print(f"  Ticker:   {top.ticker}")
    print(f"  Market:   {top.title}")
    print(f"  Volume:   ${top.volume_24h:,.0f} (24h)")
    print(f"  YES mid:  {top.implied_prob:.3f}   spread={top.spread_pct:.1%}")
    print()

    print("  (Provide --model-prob to explicitly set your probability estimate.)\n")

    pipeline = Pipeline(bankroll=100_000.0)
    result = pipeline.evaluate_sync(
        market_question=top.title,
        odds=top.implied_prob,
        model_prob=None,
    )
    _print_result(result, live_note=True)


def _cmd_paper(args: argparse.Namespace) -> None:
    from latency.tools.paper import PaperTrader

    trader = PaperTrader(
        bankroll=args.bankroll,
        cycle_seconds=args.cycle,
        scan_limit=args.scan_limit,
        min_edge=args.min_edge,
    )

    if args.once:
        asyncio.run(trader.run_once())
        trader.print_history(10)
    else:
        try:
            asyncio.run(trader.run())
        except KeyboardInterrupt:
            print("\n\n  Paper trading stopped.")
            trader.print_history(10)


def _cmd_history() -> None:
    from latency.tools.paper import PaperTrader

    trader = PaperTrader(bankroll=0)
    trader.print_history(30)


def _print_result(result, *, live_note: bool) -> None:
    print("=" * 60)
    print(f"  Market:      {result.market_question}")
    odds_tag = "  (live)" if live_note else ""
    print(f"  Odds:        {result.current_odds:.1%}{odds_tag}")
    prob_tag = "" if result.model_probability != result.current_odds else "  (= live odds, no model)"
    print(f"  Model Prob:  {result.model_probability:.1%}{prob_tag}")
    print(f"  Edge:        {result.edge:.1%}")
    print(f"  Kelly:       {result.kelly_fraction:.1%}")
    print(f"  Size:        ${result.position_size_usdc:,.2f}")
    print(f"  Signal:      {result.recommendation}")
    print("=" * 60)


# ------------------------------------------------------------------
# Async helpers — share KalshiClient session
# ------------------------------------------------------------------

async def _fetch_top_markets_async(limit: int, min_volume: float = 1000.0):
    from latency.core.kalshi_client import KalshiClient
    async with KalshiClient() as client:
        return await client.get_top_markets(limit=limit, min_volume_24h=min_volume)


async def _find_market_async(query: str):
    """Find best-matching live Kalshi market for a query string."""
    markets = await _fetch_top_markets_async(50)
    if not markets:
        return None

    q = query.lower().strip()
    scored = []
    for m in markets:
        text = m.title.lower()
        # Exact substring match first
        if q in text:
            scored.append((100, m.volume_24h, m))
            continue
        # Partial word match
        terms = [t for t in q.split() if len(t) >= 3]
        hits = sum(1 for t in terms if t in text)
        if hits:
            scored.append((hits, m.volume_24h, m))

    if not scored:
        return None
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return scored[0][2]


if __name__ == "__main__":
    main()
