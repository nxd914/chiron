
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
        print("Counting all open markets...")
        count = 0
        cursor = None
        while True:
            params = {"limit": 200, "status": "open"}
            if cursor:
                params["cursor"] = cursor
            data = await client._get("/markets", params=params)
            batch = data.get("markets") or []
            count += len(batch)
            cursor = data.get("cursor")
            print(f"Aggregated {count} markets...", end="\r")
            if not cursor or not batch:
                break
        print(f"\nTotal open markets: {count}")

if __name__ == "__main__":
    asyncio.run(main())
