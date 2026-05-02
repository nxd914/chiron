"""
BLS Public Data API client.

API docs: https://www.bls.gov/developers/api_python.htm
No key required for basic access (25 queries/10s, limited history).
Set BLS_API_KEY env var (free registration) for higher limits.

Series used:
  CUUR0000SA0   — CPI-U all items, not seasonally adjusted (official BLS headline)
  CES0000000001 — Total nonfarm employment, SA, in thousands (level)
  WPUFD49104    — PPI Final Demand, not seasonally adjusted (index level)
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
REQUEST_TIMEOUT = 10.0

CPI_SERIES_ID = "CUUR0000SA0"
NFP_SERIES_ID = "CES0000000001"
PPI_SERIES_ID = "WPUFD49104"


async def fetch_series(
    series_id: str,
    start_year: str,
    end_year: str,
    api_key: Optional[str] = None,
) -> Optional[list[dict]]:
    """
    Fetch BLS time series observations.
    Returns list of {year, period, value} dicts sorted newest-first, or None on error.
    """
    payload: dict = {
        "seriesid": [series_id],
        "startyear": start_year,
        "endyear": end_year,
    }
    if api_key:
        payload["registrationkey"] = api_key

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                BLS_API_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.warning("BLS API HTTP %d for series %s", resp.status, series_id)
                    return None
                body = await resp.json(content_type=None)
                if body.get("status") != "REQUEST_SUCCEEDED":
                    logger.warning("BLS API error: %s", body.get("message", body.get("status")))
                    return None
                series_list = body.get("Results", {}).get("series", [])
                if not series_list:
                    return None
                data = series_list[0].get("data", [])
                return sorted(data, key=lambda x: (x["year"], x["period"]), reverse=True)
    except Exception as exc:
        logger.warning("BLS API fetch failed (series=%s): %s", series_id, exc)
        return None


async def get_cpi_yoy(
    expected_year: str,
    expected_period: str,
    api_key: Optional[str] = None,
) -> Optional[float]:
    """
    Return CPI YoY % for the expected period, or None if not yet released.

    YoY = (index_current / index_same_month_prior_year - 1) × 100
    """
    data = await fetch_series(CPI_SERIES_ID, str(int(expected_year) - 1), expected_year, api_key)
    if not data:
        return None

    current = next((d for d in data if d["year"] == expected_year and d["period"] == expected_period), None)
    if current is None:
        return None  # Not yet published

    prior_year = str(int(expected_year) - 1)
    prior = next((d for d in data if d["year"] == prior_year and d["period"] == expected_period), None)
    if prior is None:
        return None

    current_val = float(current["value"])
    prior_val = float(prior["value"])
    if prior_val == 0:
        return None

    return round((current_val / prior_val - 1.0) * 100.0, 2)


async def get_nfp_change(
    expected_year: str,
    expected_period: str,
    api_key: Optional[str] = None,
) -> Optional[float]:
    """
    Return monthly NFP change (thousands of jobs) for the expected period,
    or None if not yet released.

    NFP change = level[expected_period] - level[prior_month] (in thousands)
    """
    prior_month_num = int(expected_period[1:]) - 1  # "M04" → 3
    prior_year = expected_year
    if prior_month_num == 0:
        prior_month_num = 12
        prior_year = str(int(expected_year) - 1)
    prior_period = f"M{prior_month_num:02d}"

    data = await fetch_series(NFP_SERIES_ID, prior_year, expected_year, api_key)
    if not data:
        return None

    current = next((d for d in data if d["year"] == expected_year and d["period"] == expected_period), None)
    if current is None:
        return None

    prior = next((d for d in data if d["year"] == prior_year and d["period"] == prior_period), None)
    if prior is None:
        return None

    return round(float(current["value"]) - float(prior["value"]), 1)


async def get_ppi_mom(
    expected_year: str,
    expected_period: str,
    api_key: Optional[str] = None,
) -> Optional[float]:
    """
    Return PPI Final Demand month-over-month % change for the expected period,
    or None if not yet released.

    MoM = (index_current / index_prior_month - 1) × 100
    """
    prior_month_num = int(expected_period[1:]) - 1  # "M04" → 3
    prior_year = expected_year
    if prior_month_num == 0:
        prior_month_num = 12
        prior_year = str(int(expected_year) - 1)
    prior_period = f"M{prior_month_num:02d}"

    data = await fetch_series(PPI_SERIES_ID, prior_year, expected_year, api_key)
    if not data:
        return None

    current = next((d for d in data if d["year"] == expected_year and d["period"] == expected_period), None)
    if current is None:
        return None

    prior = next((d for d in data if d["year"] == prior_year and d["period"] == prior_period), None)
    if prior is None:
        return None

    prior_val = float(prior["value"])
    if prior_val == 0:
        return None

    return round((float(current["value"]) / prior_val - 1.0) * 100.0, 1)
