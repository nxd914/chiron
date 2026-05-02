"""
FRED (Federal Reserve Economic Data) API client.

API docs: https://fred.stlouisfed.org/docs/api/fred/
Free API key required: https://fred.stlouisfed.org/docs/api/api_key.html
Set FRED_API_KEY env var. Without it, FOMC data fetching is unavailable.

Series used:
  DFEDTARU — Federal Funds Target Rate Upper Bound
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
REQUEST_TIMEOUT = 10.0

FOMC_SERIES_ID = "DFEDTARU"


async def get_fomc_rate(api_key: Optional[str] = None) -> Optional[float]:
    """
    Return the current Fed Funds target rate upper bound, or None if unavailable.

    Fetches the latest observation of DFEDTARU from FRED.
    Returns the rate as a float (e.g., 5.25 for 5.25%).
    """
    if not api_key:
        logger.debug("FRED_API_KEY not set — cannot fetch FOMC rate")
        return None

    params = {
        "series_id": FOMC_SERIES_ID,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": "1",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                FRED_API_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.warning("FRED API HTTP %d for series %s", resp.status, FOMC_SERIES_ID)
                    return None
                body = await resp.json(content_type=None)
                observations = body.get("observations", [])
                if not observations:
                    return None
                value_str = observations[0].get("value", ".")
                if value_str == ".":
                    return None
                return float(value_str)
    except Exception as exc:
        logger.warning("FRED API fetch failed (series=%s): %s", FOMC_SERIES_ID, exc)
        return None
