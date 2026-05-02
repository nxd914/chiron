"""Data models for the sports latency arb strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class Sport(Enum):
    NFL = "nfl"
    NBA = "nba"
    MLB = "mlb"
    NHL = "nhl"


@dataclass(frozen=True)
class GameResult:
    sport: Sport
    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    winner: str        # full team name of winner, "" for tie
    game_date: date    # local date of the game (for Kalshi market matching)
    finalized_at: datetime
