
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from core.kalshi_client import KalshiClient

# Load .env
repo_root = Path(__file__).resolve().parents[2]
load_dotenv(repo_root / ".env")


async def main():
    async with KalshiClient() as client:
        print("Fetching top events...")
        events = await client.get_events(limit=10)
        print(f"Fetched {len(events)} events.")
        for e in events:
            ticker = e.get("event_ticker")
            title = e.get("title")
            print(f"Event: {ticker} | {title}")
            
        print("\nFetching top markets via get_top_markets (using Events-first strategy)...")
        markets = await client.get_top_markets(limit=20)
        print(f"Fetched {len(markets)} parsed markets.")
        
        for m in markets:
            print(f"Ticker: {m.ticker} | Vol: {m.volume_24h:.2f} | Liq: {m.liquidity:.2f} | Prob: {m.implied_prob:.4f}")
            print(f"  Title: {m.title}")



if __name__ == "__main__":
    asyncio.run(main())
