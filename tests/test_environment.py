"""Unit tests for core.environment — the tri-state execution resolver."""

from __future__ import annotations

import pytest

from core.environment import (
    DEMO_REST_BASE,
    DEMO_WS_BASE,
    PROD_REST_BASE,
    PROD_WS_BASE,
    EnvironmentConfigError,
    ExecutionMode,
    resolve_environment,
)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "EXECUTION_MODE",
        "KALSHI_API_KEY",
        "KALSHI_PRIVATE_KEY_PATH",
        "KALSHI_API_KEY_DEMO",
        "KALSHI_PRIVATE_KEY_PATH_DEMO",
        "KALSHI_API_KEY_LIVE",
        "KALSHI_PRIVATE_KEY_PATH_LIVE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_paper_mode_resolves_to_demo_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_DEMO", "/tmp/kalshi_demo.pem")

    env = resolve_environment("paper")

    assert env.mode is ExecutionMode.PAPER
    assert env.rest_base_url == DEMO_REST_BASE
    assert env.ws_base_url == DEMO_WS_BASE
    assert env.api_key == "demo-key"
    assert env.is_demo is True


def test_live_mode_resolves_to_production_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY_LIVE", "live-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_LIVE", "/tmp/kalshi_prod.pem")

    env = resolve_environment("live")

    assert env.mode is ExecutionMode.LIVE
    assert env.rest_base_url == PROD_REST_BASE
    assert env.ws_base_url == PROD_WS_BASE
    assert env.api_key == "live-key"
    assert env.is_production is True


def test_local_sim_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    with pytest.raises(EnvironmentConfigError, match="Invalid"):
        resolve_environment("local_sim")


def test_paper_mode_falls_back_to_legacy_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY", "legacy-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/tmp/kalshi_demo.pem")

    env = resolve_environment("paper")

    assert env.api_key == "legacy-key"
    assert env.rest_base_url == DEMO_REST_BASE


def test_paper_mode_fails_when_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    with pytest.raises(EnvironmentConfigError, match="paper"):
        resolve_environment("paper")


def test_live_mode_fails_when_live_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    # Even with demo creds set, live must use *_LIVE explicitly
    monkeypatch.setenv("KALSHI_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_DEMO", "/tmp/kalshi_demo.pem")

    with pytest.raises(EnvironmentConfigError, match="live"):
        resolve_environment("live")


def test_live_mode_refuses_demo_pem_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY_LIVE", "live-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_LIVE", "/tmp/kalshi_demo.pem")

    with pytest.raises(EnvironmentConfigError, match="demo"):
        resolve_environment("live")


def test_paper_mode_refuses_prod_pem_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_DEMO", "/tmp/kalshi_prod.pem")

    with pytest.raises(EnvironmentConfigError, match="prod"):
        resolve_environment("paper")


def test_invalid_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    with pytest.raises(EnvironmentConfigError, match="Invalid"):
        resolve_environment("staging")


def test_default_mode_is_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_DEMO", "/tmp/kalshi_demo.pem")

    env = resolve_environment()

    assert env.mode is ExecutionMode.PAPER


def test_environment_is_immutable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("KALSHI_API_KEY_DEMO", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH_DEMO", "/tmp/kalshi_demo.pem")
    env = resolve_environment("paper")
    with pytest.raises((AttributeError, TypeError)):
        env.api_key = "tampered"  # type: ignore[misc]
