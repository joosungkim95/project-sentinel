"""
Learning Engine — Evaluates performance and refines strategies.

Two feedback loops:
- Fast Loop (daily): Pure math. Aggregates metrics, updates regimes.
- Slow Loop (weekly): Calls Claude API for strategic analysis.
"""

from engines.learning.fast_loop import FastLoop
from engines.learning.slow_loop import SlowLoop

__all__ = ["FastLoop", "SlowLoop"]
