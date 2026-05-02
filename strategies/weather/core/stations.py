"""
NWS station IDs for major US cities where Kalshi weather markets concentrate.

Station IDs: https://www.weather.gov/arh/stationlist
Each entry: city_name → NWS station ID (ICAO format)
"""

from __future__ import annotations

MONITORED_STATIONS: dict[str, str] = {
    "New York":    "KNYC",
    "Chicago":     "KORD",
    "Los Angeles": "KLAX",
    "Miami":       "KMIA",
    "Dallas":      "KDFW",
    "Boston":      "KBOS",
    "Atlanta":     "KATL",
    "Seattle":     "KSEA",
    "Denver":      "KDEN",
    "Phoenix":     "KPHX",
}

# Aliases used in Kalshi market titles — maps lowercase fragments to canonical city
CITY_ALIASES: dict[str, str] = {
    "new york": "New York",
    "nyc":      "New York",
    "chicago":  "Chicago",
    "los angeles": "Los Angeles",
    "la":       "Los Angeles",
    "miami":    "Miami",
    "dallas":   "Dallas",
    "boston":   "Boston",
    "atlanta":  "Atlanta",
    "seattle":  "Seattle",
    "denver":   "Denver",
    "phoenix":  "Phoenix",
}
