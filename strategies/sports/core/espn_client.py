"""
ESPN public scoreboard API client.

No API key required. Returns live and final game data for major US sports leagues.
Endpoint: https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import aiohttp

from .models import GameResult, Sport

logger = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
REQUEST_TIMEOUT = 10.0

_LEAGUE_PATHS: dict[str, tuple[str, Sport]] = {
    "NFL": ("football/nfl", Sport.NFL),
    "NBA": ("basketball/nba", Sport.NBA),
    "MLB": ("baseball/mlb", Sport.MLB),
    "NHL": ("hockey/nhl", Sport.NHL),
}


async def fetch_scoreboard(league: str) -> list[dict]:
    """
    Return raw ESPN event dicts for the given league.
    Returns [] on error or if the league is unknown.
    """
    entry = _LEAGUE_PATHS.get(league.upper())
    if entry is None:
        logger.warning("espn_client: unknown league %s", league)
        return []

    path, _ = entry
    url = f"{ESPN_BASE}/{path}/scoreboard"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.warning("ESPN API HTTP %d for %s", resp.status, league)
                    return []
                body = await resp.json(content_type=None)
                return body.get("events", [])
    except Exception as exc:
        logger.warning("ESPN fetch failed (%s): %s", league, exc)
        return []


def parse_final_games(events: list[dict], league: str) -> list[GameResult]:
    """
    Extract completed games from raw ESPN event list.
    Returns only events where status.type.completed == True.
    """
    entry = _LEAGUE_PATHS.get(league.upper())
    if entry is None:
        return []
    _, sport = entry

    results = []
    for event in events:
        status = event.get("status", {}).get("type", {})
        if not status.get("completed", False):
            continue

        result = _parse_event(event, sport)
        if result is not None:
            results.append(result)

    return results


def has_active_games(events: list[dict]) -> bool:
    """Return True if any event is currently in progress (not pre, not final)."""
    for event in events:
        status = event.get("status", {}).get("type", {})
        name = status.get("name", "")
        if name not in ("STATUS_SCHEDULED", "STATUS_FINAL", "STATUS_POSTPONED", "STATUS_CANCELLED"):
            if not status.get("completed", False):
                return True
    return False


def _parse_event(event: dict, sport: Sport) -> Optional[GameResult]:
    competitions = event.get("competitions", [])
    if not competitions:
        return None

    competitors = competitions[0].get("competitors", [])
    if len(competitors) < 2:
        return None

    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None

    try:
        home_score = int(home.get("score", 0))
        away_score = int(away.get("score", 0))
    except (ValueError, TypeError):
        return None

    home_name = home.get("team", {}).get("displayName", "")
    away_name = away.get("team", {}).get("displayName", "")
    if not home_name or not away_name:
        return None

    if home_score > away_score:
        winner = home_name
    elif away_score > home_score:
        winner = away_name
    else:
        winner = ""  # tie

    # Parse game date from event date string
    date_str = event.get("date", "")
    try:
        game_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        game_date = game_dt.date()
    except (ValueError, AttributeError):
        game_date = date.today()

    return GameResult(
        sport=sport,
        game_id=str(event.get("id", "")),
        home_team=home_name,
        away_team=away_name,
        home_score=home_score,
        away_score=away_score,
        winner=winner,
        game_date=game_date,
        finalized_at=datetime.now(tz=timezone.utc),
    )
