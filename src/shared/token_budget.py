"""Token budget management for the multi-agent meeting system.

Provides deterministic local token estimation, baseline measurement,
and savings analysis. Targets 40-50% token reduction through
separation of raw storage from exposed loop context summaries.

Token counting uses a deterministic character-based estimate
(characters / characters_per_token) rather than an actual tokenizer
to ensure reproducibility and zero external dependencies.
"""

from dataclasses import dataclass, field

from .config import TokenBudgetConfig, default_config
from .context_compression import (
    CompressedLoopContext,
    MeetingTurnSummary,
    build_compressed_loop_context,
)
from .utilities import resolve_token_value


def estimate_token_count(
    text: str, chars_per_token: float | None = None
) -> int:
    """Estimate token count from text using character ratio.

    Uses a deterministic character-based estimate: token_count ≈ len(text) / ratio.
    This is a local estimate for budget management, not a production tokenizer.

    Args:
        text: The text to estimate tokens for.
        chars_per_token: Characters per token ratio. Uses config default if None.

    Returns:
        Estimated token count (rounded to nearest integer).
    """
    if chars_per_token is None:
        chars_per_token = default_config.token_budget.characters_per_token
    if chars_per_token <= 0:
        return 0
    return round(len(text) / chars_per_token)


@dataclass(frozen=True)
class TurnTokenEstimate:
    """Per-turn token estimate."""

    round: int
    role: str
    kind: str
    raw_full_text_tokens: int
    exposed_summary_tokens: int


@dataclass(frozen=True)
class TokenReductionThreshold:
    """Token reduction threshold for target validation."""

    reduction_percent: int
    max_allowed_tokens: int
    minimum_saved_tokens: int


@dataclass(frozen=True)
class TokenBaselineMeasurement:
    """Complete token baseline measurement for a meeting workflow."""

    method: str = "deterministic-local-estimate-v1"
    turn_count: int = 0
    raw_full_text_tokens: int = 0
    exposed_loop_context_tokens: int = 0
    compressed_loop_context_tokens: int = 0
    exposed_reduction_percent: float = 0.0
    compressed_reduction_percent: float = 0.0
    target_reduction_thresholds: list[TokenReductionThreshold] = field(
        default_factory=list
    )
    per_turn: list[TurnTokenEstimate] = field(default_factory=list)


@dataclass(frozen=True)
class TokenBaselineInput:
    """Input for token baseline measurement."""

    turns: list[TurnTokenEstimate] = field(default_factory=list)
    compressed_context: str = ""


@dataclass(frozen=True)
class TokenSavingsEstimate:
    """Estimated token savings from compression."""

    method: str = "deterministic-local-estimate-v1"
    baseline_tokens: int = 0
    proposed_compressed_tokens: int = 0
    saved_tokens: int = 0
    savings_percent: float = 0.0
    meets_forty_percent_target: bool = False


@dataclass(frozen=True)
class TokenAccountingResult:
    """Result of token reduction accounting."""

    method: str = "deterministic-local-estimate-v1"
    baseline_tokens: int = 0
    optimized_tokens: int = 0
    absolute_reduction_tokens: int = 0
    percent_savings: float = 0.0
    meets_forty_percent_target: bool = False
    meets_fifty_percent_target: bool = False


@dataclass(frozen=True)
class TokenReductionTargetRange:
    """Target range for token reduction savings."""

    minimum_percent_savings: int = 40
    maximum_percent_savings: int = 50


@dataclass(frozen=True)
class TokenReductionSavingsMeasurement:
    """Measurement of token reduction against target range."""

    method: str = "deterministic-local-estimate-v1"
    baseline_tokens: int = 0
    reduced_tokens: int = 0
    saved_tokens: int = 0
    savings_percent: float = 0.0
    target_range: TokenReductionTargetRange = field(
        default_factory=TokenReductionTargetRange
    )
    meets_minimum_target: bool = False
    within_target_range: bool = False
    exceeds_target_range: bool = False


def reduction_percent(baseline: int, optimized: int) -> float:
    """Calculate the percentage reduction from baseline to optimized.

    Args:
        baseline: The baseline token/character count.
        optimized: The optimized token/character count.

    Returns:
        Reduction percentage as a float (0-100), or 0.0 if baseline is 0.
    """
    if baseline <= 0:
        return 0.0
    return round(((baseline - optimized) / baseline) * 100, 1)


def _build_turn_estimates(
    turns: list[dict[str, object]], chars_per_token: float
) -> list[TurnTokenEstimate]:
    """Build per-turn token estimates from turn data dicts."""
    estimates: list[TurnTokenEstimate] = []
    for turn in turns:
        content = str(turn.get("content", ""))
        visible_summary = str(turn.get("visible_summary", ""))
        round_raw = turn.get("round", 0)
        round_val = int(round_raw) if isinstance(round_raw, (int, float)) else 0
        estimates.append(
            TurnTokenEstimate(
                round=round_val,
                role=str(turn.get("role", "")),
                kind=str(turn.get("kind", "")),
                raw_full_text_tokens=estimate_token_count(
                    content, chars_per_token
                ),
                exposed_summary_tokens=estimate_token_count(
                    visible_summary, chars_per_token
                ),
            )
        )
    return estimates


def _build_thresholds(raw_tokens: int) -> list[TokenReductionThreshold]:
    """Build token reduction thresholds for target validation."""
    return [
        TokenReductionThreshold(
            reduction_percent=40,
            max_allowed_tokens=max(0, raw_tokens - int(raw_tokens * 0.4)),
            minimum_saved_tokens=int(raw_tokens * 0.4),
        ),
        TokenReductionThreshold(
            reduction_percent=45,
            max_allowed_tokens=max(0, raw_tokens - int(raw_tokens * 0.45)),
            minimum_saved_tokens=int(raw_tokens * 0.45),
        ),
        TokenReductionThreshold(
            reduction_percent=50,
            max_allowed_tokens=max(0, raw_tokens - int(raw_tokens * 0.5)),
            minimum_saved_tokens=int(raw_tokens * 0.5),
        ),
    ]


def measure_token_baseline(
    turns: list[dict[str, object]],
    compressed_context: str | None = None,
    meeting_turn_summaries: list[MeetingTurnSummary] | None = None,
    user_request_summary: str = "",
    config: TokenBudgetConfig | None = None,
) -> TokenBaselineMeasurement:
    """Measure token baseline from representative meeting turns.

    Computes raw full-text token count, exposed summary token count,
    and compressed loop context token count along with reduction percentages.

    Args:
        turns: List of turn dicts with 'content', 'visible_summary', 'round',
               'role', 'kind' keys.
        compressed_context: Pre-computed compressed context string.
                            Auto-generated from meeting_turn_summaries if None.
        meeting_turn_summaries: Meeting turn summaries for auto-generating
                                compressed context when compressed_context is None.
        user_request_summary: User request summary for auto-generation.
        config: Token budget configuration. Uses defaults if None.

    Returns:
        A TokenBaselineMeasurement with full token analysis.
    """
    cfg = config if config is not None else default_config.token_budget

    per_turn = _build_turn_estimates(turns, cfg.characters_per_token)
    raw_tokens = sum(t.raw_full_text_tokens for t in per_turn)
    exposed_tokens = sum(t.exposed_summary_tokens for t in per_turn)

    if compressed_context is not None:
        compressed_tokens = estimate_token_count(
            compressed_context, cfg.characters_per_token
        )
    elif meeting_turn_summaries is not None and user_request_summary:
        artifact = build_compressed_loop_context(
            CompressedLoopContext(
                user_request_summary=user_request_summary,
                meeting_turns=meeting_turn_summaries,
            )
        )
        compressed_tokens = estimate_token_count(
            artifact.content, cfg.characters_per_token
        )
    else:
        compressed_tokens = 0

    return TokenBaselineMeasurement(
        method=cfg.token_estimation_method,
        turn_count=len(turns),
        raw_full_text_tokens=raw_tokens,
        exposed_loop_context_tokens=exposed_tokens,
        compressed_loop_context_tokens=compressed_tokens,
        exposed_reduction_percent=reduction_percent(raw_tokens, exposed_tokens),
        compressed_reduction_percent=reduction_percent(
            raw_tokens, compressed_tokens
        ),
        target_reduction_thresholds=_build_thresholds(raw_tokens),
        per_turn=per_turn,
    )


def account_token_reduction(
    baseline: str | list[str],
    optimized: str | list[str],
) -> TokenAccountingResult:
    """Account for token reduction between baseline and optimized contexts.

    Args:
        baseline: Baseline context as a string or list of strings.
        optimized: Optimized/compressed context as a string or list of strings.

    Returns:
        TokenAccountingResult with savings analysis.

    Raises:
        TypeError: If inputs are not string or list of strings.
    """
    baseline_tokens = resolve_token_value(baseline, "baseline")
    optimized_tokens = resolve_token_value(optimized, "optimized")
    saved = baseline_tokens - optimized_tokens
    pct = reduction_percent(baseline_tokens, optimized_tokens)

    return TokenAccountingResult(
        method=default_config.token_budget.token_estimation_method,
        baseline_tokens=baseline_tokens,
        optimized_tokens=optimized_tokens,
        absolute_reduction_tokens=saved,
        percent_savings=pct,
        meets_forty_percent_target=pct >= 40,
        meets_fifty_percent_target=pct >= 50,
    )


def measure_token_reduction_savings(
    baseline: str | list[str],
    reduced: str | list[str],
    minimum_percent: int = 40,
    maximum_percent: int = 50,
) -> TokenReductionSavingsMeasurement:
    """Measure token reduction savings against a target range.

    Args:
        baseline: Baseline context as a string or list of strings.
        reduced: Reduced/compressed context as a string or list of strings.
        minimum_percent: Minimum target savings percentage (default: 40).
        maximum_percent: Maximum target savings percentage (default: 50).

    Returns:
        TokenReductionSavingsMeasurement with target range analysis.

    Raises:
        ValueError: If minimum_percent >= maximum_percent or values are negative.
    """
    if minimum_percent >= maximum_percent:
        raise ValueError(
            f"minimum_percent ({minimum_percent}) must be less than "
            f"maximum_percent ({maximum_percent})"
        )
    if minimum_percent < 0 or maximum_percent > 100:
        raise ValueError(
            f"Percentages must be between 0 and 100, got "
            f"min={minimum_percent}, max={maximum_percent}"
        )

    target_range = TokenReductionTargetRange(
        minimum_percent_savings=minimum_percent,
        maximum_percent_savings=maximum_percent,
    )
    baseline_tokens = resolve_token_value(baseline, "baseline")
    reduced_tokens = resolve_token_value(reduced, "optimized")
    saved = baseline_tokens - reduced_tokens
    pct = reduction_percent(baseline_tokens, reduced_tokens)

    return TokenReductionSavingsMeasurement(
        method=default_config.token_budget.token_estimation_method,
        baseline_tokens=baseline_tokens,
        reduced_tokens=reduced_tokens,
        saved_tokens=saved,
        savings_percent=pct,
        target_range=target_range,
        meets_minimum_target=pct >= minimum_percent,
        within_target_range=minimum_percent <= pct <= maximum_percent,
        exceeds_target_range=pct > maximum_percent,
    )
