"""
Execution environment resolver.

Tri-state EXECUTION_MODE controls everything downstream:

    local_sim   No network for orders. Synthesizes fills locally at market ask.
                Market data may still be fetched (read-only) but no POST.
    paper       Connected to Kalshi DEMO API. Real orders against demo books.
                Uses *_DEMO credentials.
    live        Connected to Kalshi PRODUCTION API. Real money.
                Uses *_LIVE credentials.

The resolver is the single source of truth — every other module receives the
resolved Environment object and never reads EXECUTION_MODE / KALSHI_API_KEY*
directly. This eliminates the class of bugs where one module thinks it's in
paper mode while another points at production.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROD_REST_BASE = "https://api.elections.kalshi.com/trade-api/v2"
PROD_WS_BASE = "wss://api.elections.kalshi.com/trade-api/ws/v2"
DEMO_REST_BASE = "https://demo-api.kalshi.co/trade-api/v2"
DEMO_WS_BASE = "wss://demo-api.kalshi.co/trade-api/ws/v2"


class ExecutionMode(str, Enum):
    LOCAL_SIM = "local_sim"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class Environment:
    """Immutable resolved execution environment."""
    mode: ExecutionMode
    rest_base_url: str
    ws_base_url: str
    api_key: str
    private_key_path: str
    place_real_orders: bool   # False for local_sim; True for paper/live

    @property
    def label(self) -> str:
        return self.mode.value.upper()

    @property
    def is_demo(self) -> bool:
        return self.mode is ExecutionMode.PAPER

    @property
    def is_production(self) -> bool:
        return self.mode is ExecutionMode.LIVE


class EnvironmentConfigError(RuntimeError):
    """Raised when the requested execution mode lacks required credentials."""


def _read_env(*names: str) -> str:
    """Return the first non-empty value among the given env var names."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _validate_credential_match(mode: ExecutionMode, private_key_path: str) -> None:
    """
    Belt-and-suspenders: refuse to launch if the loaded PEM filename obviously
    contradicts the requested mode (e.g. "demo" key in live mode).

    This is a string-only check — it cannot detect a mislabeled key, but it
    catches the common scp/copy-paste accident.
    """
    if not private_key_path:
        return
    name = Path(private_key_path).name.lower()
    if mode is ExecutionMode.LIVE and "demo" in name:
        raise EnvironmentConfigError(
            f"EXECUTION_MODE=live but private key path contains 'demo': "
            f"{private_key_path}. Refusing to start — verify .env."
        )
    if mode is ExecutionMode.PAPER and any(s in name for s in ("prod", "live")):
        raise EnvironmentConfigError(
            f"EXECUTION_MODE=paper but private key path looks like prod: "
            f"{private_key_path}. Refusing to start — verify .env."
        )


def resolve_environment(mode_override: Optional[str] = None) -> Environment:
    """
    Resolve EXECUTION_MODE into a fully-typed Environment.

    Reads from os.environ unless mode_override is given (useful for tests).
    Fails fast with EnvironmentConfigError if required credentials missing.
    """
    raw = (mode_override or os.environ.get("EXECUTION_MODE", "paper")).strip().lower()
    try:
        mode = ExecutionMode(raw)
    except ValueError:
        raise EnvironmentConfigError(
            f"Invalid EXECUTION_MODE={raw!r}. Expected one of: "
            f"{', '.join(m.value for m in ExecutionMode)}"
        )

    if mode is ExecutionMode.LOCAL_SIM:
        # No real orders, no creds required. Use prod REST for read-only market
        # discovery (simulated fills don't depend on a real order book).
        api_key = _read_env("KALSHI_API_KEY_DEMO", "KALSHI_API_KEY")
        pem_path = _read_env("KALSHI_PRIVATE_KEY_PATH_DEMO", "KALSHI_PRIVATE_KEY_PATH")
        return Environment(
            mode=mode,
            rest_base_url=PROD_REST_BASE,
            ws_base_url=PROD_WS_BASE,
            api_key=api_key,
            private_key_path=pem_path,
            place_real_orders=False,
        )

    if mode is ExecutionMode.PAPER:
        api_key = _read_env("KALSHI_API_KEY_DEMO")
        pem_path = _read_env("KALSHI_PRIVATE_KEY_PATH_DEMO")
        if not api_key or not pem_path:
            # Backward-compat: allow legacy KALSHI_API_KEY when *_DEMO unset.
            api_key = api_key or _read_env("KALSHI_API_KEY")
            pem_path = pem_path or _read_env("KALSHI_PRIVATE_KEY_PATH")
        if not api_key or not pem_path:
            raise EnvironmentConfigError(
                "EXECUTION_MODE=paper requires KALSHI_API_KEY_DEMO and "
                "KALSHI_PRIVATE_KEY_PATH_DEMO in .env (or legacy "
                "KALSHI_API_KEY / KALSHI_PRIVATE_KEY_PATH)."
            )
        _validate_credential_match(mode, pem_path)
        return Environment(
            mode=mode,
            rest_base_url=DEMO_REST_BASE,
            ws_base_url=DEMO_WS_BASE,
            api_key=api_key,
            private_key_path=pem_path,
            place_real_orders=True,
        )

    # LIVE
    api_key = _read_env("KALSHI_API_KEY_LIVE")
    pem_path = _read_env("KALSHI_PRIVATE_KEY_PATH_LIVE")
    if not api_key or not pem_path:
        raise EnvironmentConfigError(
            "EXECUTION_MODE=live requires KALSHI_API_KEY_LIVE and "
            "KALSHI_PRIVATE_KEY_PATH_LIVE in .env. Refusing to start with "
            "demo or unspecified credentials."
        )
    _validate_credential_match(mode, pem_path)
    return Environment(
        mode=mode,
        rest_base_url=PROD_REST_BASE,
        ws_base_url=PROD_WS_BASE,
        api_key=api_key,
        private_key_path=pem_path,
        place_real_orders=True,
    )


def log_environment_banner(env: Environment) -> None:
    """Emit a startup banner so logs make the resolved environment unambiguous."""
    logger.info(
        "[kinzie] Execution environment: mode=%s | REST=%s | WS=%s | "
        "place_real_orders=%s | key_id=%s | pem=%s",
        env.label,
        env.rest_base_url,
        env.ws_base_url,
        env.place_real_orders,
        (env.api_key[:8] + "…") if env.api_key else "(none)",
        env.private_key_path or "(none)",
    )
