"""
Data models for the economic data strategy.

Completely separate from the crypto pipeline models in core/models.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class EconEventType(Enum):
    CPI = "CPI"
    NFP = "NFP"
    FOMC = "FOMC"
    PPI = "PPI"


@dataclass(frozen=True)
class EconEvent:
    event_type: EconEventType
    scheduled_utc: datetime
    bls_series_id: str
    description: str

    def expected_bls_period(self) -> tuple[str, str]:
        """
        Return (year_str, period_str) for the BLS data this event will release.

        CPI and NFP are released in month M for the prior month M-1.
        E.g. 'NFP for April 2026' is released May 1 → expected period M04 in 2026.
        """
        m = self.scheduled_utc.month
        y = self.scheduled_utc.year
        data_month = m - 1 if m > 1 else 12
        data_year = y if m > 1 else y - 1
        return (str(data_year), f"M{data_month:02d}")


@dataclass(frozen=True)
class EconRelease:
    event: EconEvent
    actual: float
    prior: Optional[float]
    released_at: datetime
