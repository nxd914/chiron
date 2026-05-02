"""Data models for the weather latency arb strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class WeatherObservation:
    station_id: str
    city: str
    observed_at: datetime
    temperature_f: Optional[float]    # current temp in Fahrenheit
    precipitation_in: Optional[float] # hourly precipitation in inches (None if not measured)
    wind_speed_mph: Optional[float]
