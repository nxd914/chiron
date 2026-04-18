
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from latency.core.kalshi_client import KalshiClient

# Load .env
repo_root = Path(__file__).resolve().parents[2]
load_dotenv(repo_root / ".env")

async def main():
    async with KalshiClient() as client:
        print("Fetching first few raw markets to inspect keys...")
        params = {"limit": 5, "status": "open"}
        data = await client._get("/markets", params=params)
        markets = data.get("markets") or []
        for i, m in enumerate(markets):
            print(f"\n--- Market {i} Keys ---")
            print(sorted(m.keys()))
            print(f"Ticker: {m.get('ticker')}")
            print(f"Volume 24h: {m.get('volume_24h')}")
            print(f"Liquidity: {m.get('liquidity')}")
            # print first 50 chars of title
            print(f"Title: {m.get('title')[:50]}...")

if __name__ == "__main__":
    asyncio.run(main())
