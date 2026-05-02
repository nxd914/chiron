"""
Pipeline — the main user-facing API for Quant.

Orchestrates signal evaluation and Kelly-constrained risk sizing into a single
async call. The pipeline is fully deterministic — no LLM calls.

    pipeline = Pipeline(bankroll=100_000.0)
    result = await pipeline.evaluate("Will Fed cut rates?", odds=0.42, model_prob=0.58)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.kelly import MIN_EDGE, capped_kelly, position_size

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationResult:
    """Complete output of a pipeline evaluation."""
    market_question: str
    current_odds: float
    model_probability: float
    edge: float
    kelly_fraction: float
    position_size_usdc: float
    recommendation: str
    timestamp: datetime


class Pipeline:
    """
    End-to-end prediction market evaluation pipeline.

    Computes Kelly sizing and returns a structured recommendation.
    model_prob must be provided explicitly — the pipeline does not call
    any external LLM.

    Args:
        bankroll: Total bankroll in USDC for position sizing.
        kelly_cap: Maximum Kelly fraction (default 0.25).
        min_edge: Minimum edge to trade (default 0.04).
    """

    def __init__(
        self,
        bankroll: float = 500.0,
        kelly_cap: float = 0.25,
        min_edge: float = MIN_EDGE,
        enable_analysts: bool = False,  # no-op, kept for backward compat
    ) -> None:
        self._bankroll = bankroll
        self._kelly_cap = kelly_cap
        self._min_edge = min_edge

    async def evaluate(
        self,
        market_question: str,
        odds: float,
        model_prob: Optional[float] = None,
        context: str = "",
    ) -> EvaluationResult:
        """
        Evaluate a prediction market opportunity.

        Args:
            market_question: The market question
            odds: Current market implied probability (0-1).
            model_prob: Your probability estimate. If None, defaults to market odds
                        (i.e. no edge — use when scanning to detect trades that
                        require explicit model input).
            context: Unused — kept for interface compatibility.

        Returns:
            EvaluationResult with recommendation and sizing.
        """
        if model_prob is None:
            model_prob = odds  # no model → no edge

        edge = abs(model_prob - odds)
        kelly = capped_kelly(model_prob, odds)
        size = position_size(model_prob, odds, self._bankroll)

        recommendation = self._recommend(edge, kelly)

        return EvaluationResult(
            market_question=market_question,
            current_odds=odds,
            model_probability=model_prob,
            edge=edge,
            kelly_fraction=kelly,
            position_size_usdc=size,
            recommendation=recommendation,
            timestamp=datetime.now(tz=timezone.utc),
        )

    def evaluate_sync(
        self,
        market_question: str,
        odds: float,
        model_prob: Optional[float] = None,
        context: str = "",
    ) -> EvaluationResult:
        """Synchronous wrapper for evaluate()."""
        import asyncio
        return asyncio.run(self.evaluate(market_question, odds, model_prob, context))

    def _recommend(
        self,
        edge: float,
        kelly: float,
    ) -> str:
        if edge < self._min_edge:
            return "NO TRADE — edge below minimum threshold"

        if kelly <= 0:
            return "NO TRADE — negative Kelly (market correctly priced or overpriced)"

        strength = "STRONG" if edge > 0.08 else "MODERATE" if edge > 0.05 else "MARGINAL"
        return f"{strength} — edge={edge:.1%}, kelly={kelly:.1%}"
