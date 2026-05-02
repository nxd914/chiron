"""
Sports Feed Agent

Monitors ESPN scoreboards for finalized games and publishes GameResult
events to the queue. Polls rapidly during active game windows, idles otherwise.

Flow:
  1. Poll all configured leagues once per hour (idle mode)
  2. When any league has games in progress → switch to active mode (30s polls)
  3. For each newly completed game → publish GameResult to queue
  4. Track published game IDs to avoid duplicates
"""

from __future__ import annotations

import asyncio
import logging

from ..core.config import SportsConfig
from ..core.espn_client import fetch_scoreboard, has_active_games, parse_final_games
from ..core.models import GameResult

logger = logging.getLogger(__name__)


class SportsFeedAgent:
    def __init__(self, result_queue: asyncio.Queue[GameResult], config: SportsConfig) -> None:
        self._queue = result_queue
        self._config = config
        self._published: set[str] = set()  # game_ids already sent to queue

    async def run(self) -> None:
        logger.info("SportsFeedAgent started | leagues=%s", ",".join(self._config.leagues))
        while True:
            any_active = await self._poll_all_leagues()
            delay = self._config.poll_seconds if any_active else self._config.idle_seconds
            await asyncio.sleep(delay)

    async def _poll_all_leagues(self) -> bool:
        any_active = False
        for league in self._config.leagues:
            events = await fetch_scoreboard(league)
            if not events:
                continue

            if has_active_games(events):
                any_active = True
                logger.debug("SportsFeedAgent: active games in %s", league)

            for result in parse_final_games(events, league):
                if result.game_id in self._published:
                    continue
                self._published.add(result.game_id)
                logger.info(
                    "SportsFeedAgent: final — %s %d vs %s %d (%s) winner=%s",
                    result.home_team, result.home_score,
                    result.away_team, result.away_score,
                    result.sport.value.upper(),
                    result.winner or "TIE",
                )
                await self._queue.put(result)

        return any_active
