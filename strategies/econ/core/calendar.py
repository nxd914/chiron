"""
2026 economic release schedule.

Verify dates annually against official sources:
  CPI:  https://www.bls.gov/schedule/news_release/cpi.htm
  NFP:  https://www.bls.gov/schedule/news_release/empsit.htm
  PPI:  https://www.bls.gov/schedule/news_release/ppi.htm
  FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

BLS releases at 8:30 AM ET (13:30 UTC).
FOMC decisions at 2:00 PM ET (19:00 UTC).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .bls_client import CPI_SERIES_ID, NFP_SERIES_ID, PPI_SERIES_ID
from .fred_client import FOMC_SERIES_ID
from .models import EconEvent, EconEventType

_BLS_H, _BLS_M = 13, 30   # 8:30 AM ET in UTC
_FOMC_H, _FOMC_M = 19, 0  # 2:00 PM ET in UTC


def _bls(y: int, mo: int, d: int) -> datetime:
    return datetime(y, mo, d, _BLS_H, _BLS_M, tzinfo=timezone.utc)


def _fomc(y: int, mo: int, d: int) -> datetime:
    return datetime(y, mo, d, _FOMC_H, _FOMC_M, tzinfo=timezone.utc)


CALENDAR_2026: list[EconEvent] = [
    # ── CPI (BLS, 8:30 AM ET) ─────────────────────────────────────────────
    EconEvent(EconEventType.CPI, _bls(2026, 5, 13),  CPI_SERIES_ID, "CPI for April 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 6, 11),  CPI_SERIES_ID, "CPI for May 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 7, 15),  CPI_SERIES_ID, "CPI for June 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 8, 12),  CPI_SERIES_ID, "CPI for July 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 9, 10),  CPI_SERIES_ID, "CPI for August 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 10, 14), CPI_SERIES_ID, "CPI for September 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 11, 13), CPI_SERIES_ID, "CPI for October 2026"),
    EconEvent(EconEventType.CPI, _bls(2026, 12, 10), CPI_SERIES_ID, "CPI for November 2026"),

    # ── NFP (BLS, 8:30 AM ET, first Friday of month) ──────────────────────
    EconEvent(EconEventType.NFP, _bls(2026, 5, 1),   NFP_SERIES_ID, "NFP for April 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 6, 5),   NFP_SERIES_ID, "NFP for May 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 7, 2),   NFP_SERIES_ID, "NFP for June 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 8, 7),   NFP_SERIES_ID, "NFP for July 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 9, 4),   NFP_SERIES_ID, "NFP for August 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 10, 2),  NFP_SERIES_ID, "NFP for September 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 11, 6),  NFP_SERIES_ID, "NFP for October 2026"),
    EconEvent(EconEventType.NFP, _bls(2026, 12, 4),  NFP_SERIES_ID, "NFP for November 2026"),

    # ── PPI Final Demand (BLS, 8:30 AM ET, ~mid-month) ────────────────────
    EconEvent(EconEventType.PPI, _bls(2026, 5, 15),  PPI_SERIES_ID, "PPI for April 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 6, 12),  PPI_SERIES_ID, "PPI for May 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 7, 15),  PPI_SERIES_ID, "PPI for June 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 8, 14),  PPI_SERIES_ID, "PPI for July 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 9, 11),  PPI_SERIES_ID, "PPI for August 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 10, 16), PPI_SERIES_ID, "PPI for September 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 11, 13), PPI_SERIES_ID, "PPI for October 2026"),
    EconEvent(EconEventType.PPI, _bls(2026, 12, 11), PPI_SERIES_ID, "PPI for November 2026"),

    # ── FOMC decisions (Fed, 2:00 PM ET) ──────────────────────────────────
    EconEvent(EconEventType.FOMC, _fomc(2026, 5, 7),   FOMC_SERIES_ID, "FOMC May 2026"),
    EconEvent(EconEventType.FOMC, _fomc(2026, 6, 18),  FOMC_SERIES_ID, "FOMC June 2026"),
    EconEvent(EconEventType.FOMC, _fomc(2026, 7, 30),  FOMC_SERIES_ID, "FOMC July 2026"),
    EconEvent(EconEventType.FOMC, _fomc(2026, 9, 17),  FOMC_SERIES_ID, "FOMC September 2026"),
    EconEvent(EconEventType.FOMC, _fomc(2026, 11, 5),  FOMC_SERIES_ID, "FOMC November 2026"),
    EconEvent(EconEventType.FOMC, _fomc(2026, 12, 17), FOMC_SERIES_ID, "FOMC December 2026"),
]


def upcoming_events(after: datetime | None = None) -> list[EconEvent]:
    """Return events scheduled after `after` (default: now), sorted ascending."""
    cutoff = after or datetime.now(tz=timezone.utc)
    return sorted(
        [e for e in CALENDAR_2026 if e.scheduled_utc > cutoff],
        key=lambda e: e.scheduled_utc,
    )
