"""
Learning Engine — Slow Loop (Weekly).

Runs once per week (Sunday evening). Calls Claude API (Sonnet) to:
1. Analyze the week's trading performance
2. Generate new strategy hypotheses
3. Recommend which strategies to promote, disable, or graveyard
4. Suggest parameter adjustments

Cost: ~$0.05-0.07 per run (Sonnet, <8K input + <3K output)
Monthly: ~$0.20-0.28 (4 runs)
"""

import json
import logging
import os
from datetime import datetime, timezone

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from config.costs import DECISION_MODEL_MAP, DecisionType
from engines.alerts import send_alert, AlertLevel
from memory.context_manager import ContextManager
from memory.strategy_journal import StrategyJournal
from engines.models import LearningContext, PortfolioSnapshot

logger = logging.getLogger(__name__)

# System prompt for weekly review — cached across calls (90% savings)
WEEKLY_REVIEW_SYSTEM = """You are Sentinel's Learning Engine — the analytical brain of an autonomous trading platform.

You are reviewing the past week's trading performance to make data-driven decisions about strategy management.

Your responsibilities:
1. ANALYZE: Identify what worked, what failed, and why
2. HYPOTHESIZE: Propose new strategy ideas based on observed patterns
3. RECOMMEND: Decide which strategies to promote, disable, or adjust
4. LEARN: Extract lessons that should inform future decisions

Rules:
- Be specific and quantitative. "Strategy X underperformed" is useless. "Strategy X had -2.3% return with 0.4 Sharpe vs target 1.0" is useful.
- Every hypothesis must include a clear test criteria (what would prove it right/wrong)
- Never recommend removing risk controls
- Bias toward caution: when uncertain, recommend paper testing over live activation
- Consider market regime: a strategy failing in high volatility isn't necessarily bad

Respond with valid JSON matching this schema:
{
  "analysis": "2-3 sentence summary of the week",
  "hypotheses": [
    {
      "text": "Strategy description",
      "rationale": "Why this might work",
      "target_regime": "trending_up|trending_down|ranging|high_volatility|any",
      "test_criteria": "How to evaluate success/failure"
    }
  ],
  "recommendations": [
    {
      "strategy_id": "name of existing strategy",
      "action": "promote|disable|graveyard|adjust_params",
      "reason": "Why this action",
      "params": {}
    }
  ],
  "lessons": ["Lesson 1", "Lesson 2"]
}"""


class SlowLoop:
    """
    Weekly learning loop — uses Claude API for strategic analysis.

    Assembles a LearningContext, sends it to Sonnet for analysis,
    and acts on the recommendations (recording hypotheses, adjusting
    strategy status).
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.context_manager = ContextManager(db_session)
        self.strategy_journal = StrategyJournal(db_session)
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Lazy-init the Anthropic client."""
        if self._client is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set — cannot run slow loop"
                )
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._client

    async def run(
        self, current_portfolio: PortfolioSnapshot, period_days: int = 7
    ) -> dict:
        """
        Execute the full weekly slow loop.

        Args:
            current_portfolio: Current portfolio state.
            period_days: How many days to review (default: 7).

        Returns:
            Parsed response from Claude with analysis, hypotheses,
            recommendations, and lessons.
        """
        logger.info("Slow loop starting — weekly review (%d days)", period_days)

        # 1. Build context
        context = await self.context_manager.build_learning_context(
            current_portfolio, period_days=period_days
        )

        # 2. Format prompt
        prompt = self._build_prompt(context)

        # 3. Call Claude API
        config = DECISION_MODEL_MAP[DecisionType.WEEKLY_REVIEW]
        response = await self._call_claude(
            prompt=prompt,
            model=config["model"].value,
            max_tokens=config["max_output_tokens"],
        )

        # 4. Parse response
        parsed = self._parse_response(response)

        # 5. Act on results
        await self._record_hypotheses(parsed.get("hypotheses", []))
        await self._apply_recommendations(parsed.get("recommendations", []))

        # 6. Send summary alert
        await self._send_weekly_alert(parsed, context)

        await self.db.commit()

        logger.info(
            "Slow loop complete: %d hypotheses, %d recommendations",
            len(parsed.get("hypotheses", [])),
            len(parsed.get("recommendations", [])),
        )
        return parsed

    def _build_prompt(self, context: LearningContext) -> str:
        """
        Build the user prompt from the LearningContext.

        Keeps it under the 8K input token budget.
        """
        prompt_text = self.context_manager.to_prompt_text(
            context, max_tokens=6000
        )

        return f"""## Weekly Performance Review — {context.period_days} Day Period

### Portfolio
{prompt_text}

### Task
Analyze this week's performance and provide:
1. A brief analysis of what happened
2. 1-3 new strategy hypotheses to test (if any patterns suggest opportunities)
3. Recommendations for existing strategies (promote, disable, adjust)
4. Key lessons learned

Respond with JSON only. No markdown, no explanation outside the JSON."""

    async def _call_claude(
        self, prompt: str, model: str, max_tokens: int
    ) -> str:
        """
        Call the Claude API with cost controls.

        Uses prompt caching for the system prompt to save ~90% on
        repeated calls.

        Args:
            prompt: The user message content.
            model: Claude model ID.
            max_tokens: Maximum output tokens.

        Returns:
            The text content of Claude's response.
        """
        logger.info("Calling Claude API: model=%s, max_tokens=%d", model, max_tokens)

        try:
            message = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": WEEKLY_REVIEW_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )

            # Log usage for cost tracking
            usage = message.usage
            logger.info(
                "Claude API response: input=%d tokens, output=%d tokens, "
                "cache_read=%d, cache_creation=%d",
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_read_input_tokens", 0),
                getattr(usage, "cache_creation_input_tokens", 0),
            )

            return message.content[0].text

        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            raise

    def _parse_response(self, response_text: str) -> dict:
        """
        Parse Claude's JSON response.

        Handles common formatting issues (markdown code blocks, etc.)
        and validates the expected structure.
        """
        text = response_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Claude response as JSON: %s", e)
            logger.debug("Raw response: %s", response_text[:500])
            return {
                "analysis": "Failed to parse response",
                "hypotheses": [],
                "recommendations": [],
                "lessons": [],
                "parse_error": str(e),
            }

        # Validate expected keys
        expected_keys = {"analysis", "hypotheses", "recommendations", "lessons"}
        missing = expected_keys - set(parsed.keys())
        if missing:
            logger.warning("Response missing keys: %s", missing)

        return parsed

    async def _record_hypotheses(self, hypotheses: list[dict]) -> None:
        """Record new strategy hypotheses from Claude's suggestions."""
        for h in hypotheses:
            text = h.get("text", "")
            if not text:
                continue

            rationale = h.get("rationale", "")
            criteria = h.get("test_criteria", "")
            regime = h.get("target_regime")

            full_text = f"{text}\n\nRationale: {rationale}\nTest criteria: {criteria}"

            await self.strategy_journal.propose_hypothesis(
                hypothesis_text=full_text,
                source="claude_weekly_review",
                market_regime=regime,
            )
            logger.info("Recorded hypothesis: %s", text[:80])

    async def _apply_recommendations(self, recommendations: list[dict]) -> None:
        """
        Apply strategy recommendations.

        Note: This only records recommendations in the journal.
        Actual strategy activation/deactivation requires human approval
        or a separate promotion pipeline. We never auto-disable without
        the ability to review.
        """
        for rec in recommendations:
            strategy_id = rec.get("strategy_id", "")
            action = rec.get("action", "")
            reason = rec.get("reason", "")

            if not strategy_id or not action:
                continue

            # Log the recommendation — actual execution is manual for safety
            logger.info(
                "Recommendation: %s → %s (reason: %s)",
                strategy_id, action, reason,
            )

            # Record as a hypothesis with the recommendation context
            await self.strategy_journal.propose_hypothesis(
                hypothesis_text=(
                    f"[RECOMMENDATION] {action.upper()} strategy '{strategy_id}': "
                    f"{reason}"
                ),
                source="claude_weekly_review",
                market_regime=None,
            )

    async def _send_weekly_alert(
        self, parsed: dict, context: LearningContext
    ) -> None:
        """Send weekly review summary to Discord."""
        analysis = parsed.get("analysis", "No analysis available")
        n_hypotheses = len(parsed.get("hypotheses", []))
        n_recs = len(parsed.get("recommendations", []))
        lessons = parsed.get("lessons", [])

        lesson_text = "\n".join(f"• {l}" for l in lessons[:3]) if lessons else "None"

        await send_alert(
            title=f"Weekly Review — {context.period_days}d",
            message=(
                f"**Analysis:** {analysis}\n\n"
                f"**P&L:** ${context.total_pnl_period:,.2f}\n"
                f"**Strategies reviewed:** {len(context.strategy_performances)}\n\n"
                f"**New hypotheses:** {n_hypotheses}\n"
                f"**Recommendations:** {n_recs}\n\n"
                f"**Lessons:**\n{lesson_text}"
            ),
            level=AlertLevel.INFO,
            fields={
                "Period": f"{context.period_days} days",
                "Total P&L": f"${context.total_pnl_period:,.2f}",
                "Hypotheses": str(n_hypotheses),
                "Recommendations": str(n_recs),
            },
        )
