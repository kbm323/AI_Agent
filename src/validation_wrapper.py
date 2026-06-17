"""Validation wrapper orchestrator — composes CLI invocation and parser.

Sub-AC 7.1c: Composes the GLM-5.1 CLI invocation (``opencode_glm_wrapper``)
with the structured output parser (``glm_output_parser``), handling all
error modes (non-zero exit, timeout, parse failure) and returning a
single standardized result object.

This module is the **primary validator orchestrator** in the dual-validation
pipeline.  The Coordinator calls ``run_glm_validation()`` and receives a
normalised ``ValidationResult`` regardless of what happened downstream
(GLMI CLI error, GLM model failure, output parse failure, etc.).

Every error path produces a ``ValidationResult`` with ``passed=False`` and
a non-null ``error`` string — the Coordinator never sees a raw exception
from the validation layer.

Architecture
------------
::

    Coordinator
        │
        ▼
    run_glm_validation(config)
        ├── invoke_glm(config)          # 7.1a — CLI mechanics
        │   ├── success → raw stdout
        │   └── failure → error result
        ├── parse_glm_output(stdout)    # 7.1b — structured parsing
        │   ├── success → verdict + confidence
        │   └── failure → error result
        └── ValidationResult            # normalised output

Standardised result contract
----------------------------
Every path returns::

    ValidationResult(
        passed=bool,       # True only when GLM says pass or conditional_pass
        confidence=float,  # 0.0–1.0 from GLM overall_score (0.0 on error)
        error=str | None,  # null on success, descriptive message on failure
        verdict_raw=str,   # raw GLM verdict string for logging/escalation logic
        duration_seconds=float,  # wall-clock time for the full pipeline
    )

Usage::

    from src.validation_wrapper import (
        ValidationResult,
        GlmValidationConfig,
        run_glm_validation,
    )

    config = GlmValidationConfig(
        model="glm-5.1",
        context_file="/path/to/validation_packet.json",
    )
    result = run_glm_validation(config)
    if result.passed:
        print(f"GLM-5.1 passed with confidence {result.confidence}")
    else:
        print(f"Validation failed: {result.error}")
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.glm_output_parser import (
    GlmParseResult,
    parse_glm_output,
)
from src.opencode_glm_wrapper import (
    GlmCallConfig,
    GlmCallResult,
    SubprocessRunner,
    invoke_glm,
)

# ═════════════════════════════════════════════════════════════════════════
# Configuration
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GlmValidationConfig:
    """Configuration for a complete GLM-5.1 validation run.

    Mirrors ``GlmCallConfig`` but is owned by the orchestrator layer
    so the Coordinator does not need to import the lower-level wrapper
    directly.

    Attributes:
        model: Model name for ``--model`` (e.g. ``"glm-5.1"``).
        context_file: Path to JSON context packet for ``--context-file``.
        timeout_seconds: Maximum wall-clock time for the CLI subprocess
                         (default 180s — validator tier).
        env: Optional extra environment variables.
        workdir: Optional working directory for the subprocess.
    """

    model: str
    """LLM model name (e.g. ``glm-5.1``)."""

    context_file: str
    """Filesystem path to the validation context packet JSON."""

    timeout_seconds: float = 180.0
    """Maximum subprocess wall-clock time (seconds)."""

    env: dict[str, str] | None = None
    """Extra environment variables merged with os.environ."""

    workdir: str | None = None
    """Working directory for the subprocess."""

    def __post_init__(self) -> None:
        if not self.model or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not self.context_file or not self.context_file.strip():
            raise ValueError("context_file must be a non-empty path")
        if self.timeout_seconds < 1:
            raise ValueError(
                f"timeout_seconds must be >= 1, got {self.timeout_seconds}"
            )

    def to_glm_call_config(self) -> GlmCallConfig:
        """Convert to the lower-level ``GlmCallConfig`` for CLI invocation."""
        return GlmCallConfig(
            model=self.model,
            context_file=self.context_file,
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            workdir=self.workdir,
        )


# ═════════════════════════════════════════════════════════════════════════
# Standardised result
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ValidationResult:
    """Standardised validation result — the single return type for all paths.

    This is the contract described in Sub-AC 7.1c:
    ``{pass: bool, confidence: float, error: string|null}``.

    Extended with additional fields useful for the Coordinator's
    escalation logic and logging.

    Attributes:
        passed: ``True`` when GLM-5.1 verdict is ``pass`` or
                ``conditional_pass`` AND parsing succeeded.
        confidence: Float in ``[0.0, 1.0]`` from GLM ``overall_score``.
                    ``0.0`` on any error path.
        error: ``None`` on success; descriptive string on any failure
               (CLI error, timeout, parse error, etc.).
        verdict_raw: The raw verdict string from GLM-5.1 output
                     (empty string on error paths).
        duration_seconds: Wall-clock time for the full pipeline
                          (CLI call + parsing).
        error_category: Broad error category for Coordinator routing:
                        ``"cli_error"``, ``"parse_error"``,
                        ``"validation_failed"``, or empty string
                        on success.
    """

    passed: bool = False
    """Whether the GLM-5.1 verdict maps to 'pass'."""

    confidence: float = 0.0
    """Extracted overall_score (0.0 on error)."""

    error: str | None = None
    """Error description — ``None`` when everything succeeded."""

    verdict_raw: str = ""
    """Raw verdict string from GLM-5.1 (empty on error)."""

    duration_seconds: float = 0.0
    """Total wall-clock time for the validation pipeline."""

    error_category: str = ""
    """Broad error category for Coordinator routing logic."""

    @property
    def passed_clean(self) -> bool:
        """``True`` when passed with verdict ``pass`` (not conditional_pass)."""
        return self.passed and self.verdict_raw == "pass"

    @property
    def passed_conditional(self) -> bool:
        """``True`` when passed with verdict ``conditional_pass``."""
        return self.passed and self.verdict_raw == "conditional_pass"

    @property
    def requires_codex_escalation(self) -> bool:
        """``True`` when the result suggests Codex secondary validation.

        Heuristic: error occurred, or verdict is escalate/fail, or
        confidence is below 0.75.
        """
        if self.error is not None:
            return True
        if self.verdict_raw in ("escalate", "fail"):
            return True
        if self.confidence < 0.75:
            return True
        return False


@dataclass(frozen=True)
class DualValidationDecision:
    """Final decision after comparing GLM and Codex validation results.

    AC8 domain policy:
    - tech/security/data domains: GLM is authoritative.
    - legal/budget/brand domains: Codex is authoritative.
    - factual domains: higher-confidence validator wins.
    - identical pass/fail verdicts: agreement, no override.
    """

    winner: str
    passed: bool
    confidence: float
    policy: str
    conflict: bool
    glm_result: ValidationResult
    codex_result: ValidationResult


def resolve_dual_validation_conflict(
    glm_result: ValidationResult,
    codex_result: ValidationResult,
    *,
    domain: str,
) -> DualValidationDecision:
    """Resolve conflicting GLM/Codex validation verdicts by domain.

    The function is pure and filesystem-free so the Coordinator can apply
    deterministic policy after two validator calls have completed.
    """
    if not isinstance(glm_result, ValidationResult):
        raise TypeError("glm_result must be a ValidationResult")
    if not isinstance(codex_result, ValidationResult):
        raise TypeError("codex_result must be a ValidationResult")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be a non-empty string")

    normalized_domain = domain.strip().lower().replace("_", "-")
    conflict = glm_result.passed != codex_result.passed

    if not conflict:
        confidence = max(glm_result.confidence, codex_result.confidence)
        return DualValidationDecision(
            winner="agreement",
            passed=glm_result.passed,
            confidence=confidence,
            policy="validators_agree",
            conflict=False,
            glm_result=glm_result,
            codex_result=codex_result,
        )

    technical_domains = {"tech", "technical", "security", "data", "engineering"}
    policy_domains = {"legal", "budget", "brand", "policy", "finance"}
    factual_domains = {"factual", "fact", "facts", "grounding", "evidence"}

    if normalized_domain in technical_domains:
        winner, selected, policy = (
            "glm",
            glm_result,
            "technical_domain_glm_authoritative",
        )
    elif normalized_domain in policy_domains:
        winner, selected, policy = (
            "codex",
            codex_result,
            "policy_domain_codex_authoritative",
        )
    elif normalized_domain in factual_domains:
        if codex_result.confidence > glm_result.confidence:
            winner, selected = "codex", codex_result
        else:
            winner, selected = "glm", glm_result
        policy = "factual_domain_confidence_tiebreak"
    else:
        # Unknown domains should be conservative: prefer the stricter failure,
        # otherwise use confidence if both are somehow equivalent after conflict.
        if not glm_result.passed:
            winner, selected = "glm", glm_result
        elif not codex_result.passed:
            winner, selected = "codex", codex_result
        elif codex_result.confidence > glm_result.confidence:
            winner, selected = "codex", codex_result
        else:
            winner, selected = "glm", glm_result
        policy = "unknown_domain_conservative_failure"

    return DualValidationDecision(
        winner=winner,
        passed=selected.passed,
        confidence=selected.confidence,
        policy=policy,
        conflict=True,
        glm_result=glm_result,
        codex_result=codex_result,
    )


# ═════════════════════════════════════════════════════════════════════════
# Error message builders
# ═════════════════════════════════════════════════════════════════════════


def _cli_error_result(
    cli_result: GlmCallResult, duration_seconds: float
) -> ValidationResult:
    """Build a ``ValidationResult`` for CLI-level failures.

    Covers: non-zero exit code, timeout, OSError.

    Prefers ``error_message`` string analysis over ``timeout_occurred``
    flag because the GLM wrapper sets ``timeout_occurred=True`` for
    all ``exit_code=-1`` paths (including OSError), making the flag
    unreliable for distinguishing timeout from internal error.

    Args:
        cli_result: The failed ``GlmCallResult`` from the CLI wrapper.
        duration_seconds: Total elapsed time for the pipeline.

    Returns:
        ``ValidationResult`` with ``passed=False`` and descriptive error.
    """
    error_lower = cli_result.error_message.lower()

    # Distinguish timeout vs OSError vs non-zero exit by error_message content
    if "timed out" in error_lower or "timeout" in error_lower:
        error_msg = (
            f"GLM-5.1 validation timed out after "
            f"{cli_result.duration_seconds:.1f}s. "
            f"Partial stdout: {cli_result.stdout[:200] if cli_result.stdout else '(none)'}"
        )
        error_category = "cli_error"
    elif "oserror" in error_lower or "subprocess error" in error_lower:
        error_msg = (
            f"GLM-5.1 subprocess internal error: {cli_result.error_message}. "
            f"Stderr: {cli_result.stderr[:200] if cli_result.stderr else '(none)'}"
        )
        error_category = "cli_error"
    elif cli_result.exit_code == -1:
        # Unknown exit_code=-1 (no matching keyword) — treat as internal error
        error_msg = (
            f"GLM-5.1 subprocess error (exit_code=-1): "
            f"{cli_result.error_message}. "
            f"Stderr: {cli_result.stderr[:200] if cli_result.stderr else '(none)'}"
        )
        error_category = "cli_error"
    else:
        error_msg = (
            f"GLM-5.1 exited with code {cli_result.exit_code}. "
            f"Stderr: {cli_result.stderr[:200] if cli_result.stderr else 'no stderr'}"
        )
        error_category = "cli_error"

    return ValidationResult(
        passed=False,
        confidence=0.0,
        error=error_msg,
        verdict_raw="",
        duration_seconds=duration_seconds,
        error_category=error_category,
    )


def _parse_error_result(
    parse_result: GlmParseResult, duration_seconds: float
) -> ValidationResult:
    """Build a ``ValidationResult`` for parser-level failures.

    Covers: empty output, malformed JSON, missing verdict, etc.

    Args:
        parse_result: The failed ``GlmParseResult`` from the parser.
        duration_seconds: Total elapsed time for the pipeline.

    Returns:
        ``ValidationResult`` with ``passed=False`` and descriptive error.
    """
    err = parse_result.error
    if err is not None:
        error_msg = (
            f"GLM-5.1 output parse failed [{err.error_type}]: {err.message}. "
            f"Raw excerpt: {err.raw_excerpt[:200]}"
        )
    else:
        error_msg = (
            "GLM-5.1 output parse failed with unknown error. "
            "No structured verdict could be extracted."
        )

    return ValidationResult(
        passed=False,
        confidence=0.0,
        error=error_msg,
        verdict_raw=parse_result.verdict_raw,
        duration_seconds=duration_seconds,
        error_category="parse_error",
    )


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════


def run_glm_validation(
    config: GlmValidationConfig,
    *,
    _injected_runner: SubprocessRunner | None = None,
) -> ValidationResult:
    """Run the complete GLM-5.1 validation pipeline — invoke then parse.

    This is the main entry point for **Sub-AC 7.1c**.

    Composes the CLI invocation (Sub-AC 7.1a) and structured output
    parser (Sub-AC 7.1b) into a single call that always returns a
    ``ValidationResult``.  No raw exceptions escape — every error
    path is captured and normalised.

    Pipeline::

        1. Convert ``GlmValidationConfig`` → ``GlmCallConfig``
        2. ``invoke_glm()`` — run opencode-go CLI
        3. If CLI fails → ``ValidationResult(passed=False, error=...)``
        4. ``parse_glm_output()`` — extract verdict + confidence
        5. If parse fails → ``ValidationResult(passed=False, error=...)``
        6. Return ``ValidationResult(passed=..., confidence=..., ...)``

    Args:
        config: ``GlmValidationConfig`` with model, context_file, timeout.
        _injected_runner: Override the subprocess runner for testing.
                          Passed through to ``invoke_glm()``.

    Returns:
        ``ValidationResult`` — always check ``result.error is None``
        to determine if the pipeline was fully successful.

    Raises:
        ValueError: If config validation fails (empty model/context_file).
        TypeError: If config is not a ``GlmValidationConfig``.

    Examples:
        >>> config = GlmValidationConfig(
        ...     model="glm-5.1",
        ...     context_file="/tmp/validation_packet.json",
        ... )
        >>> result = run_glm_validation(config)
        >>> if result.error is None:
        ...     print(f"Verdict: {result.verdict_raw}, "
        ...           f"Confidence: {result.confidence}")
        ... else:
        ...     print(f"Validation error: {result.error}")
    """
    if not isinstance(config, GlmValidationConfig):
        raise TypeError(
            f"config must be GlmValidationConfig, got {type(config).__name__}"
        )

    start = time.monotonic()

    # ── Phase 1: CLI invocation (Sub-AC 7.1a) ─────────────────────────
    cli_config = config.to_glm_call_config()
    cli_result = invoke_glm(cli_config, _injected_runner=_injected_runner)

    # ── Phase 2: Handle CLI failure ───────────────────────────────────
    if not cli_result.success:
        elapsed = time.monotonic() - start
        return _cli_error_result(cli_result, round(elapsed, 4))

    # ── Phase 3: Parse structured output (Sub-AC 7.1b) ────────────────
    parse_result = parse_glm_output(cli_result.stdout)

    elapsed = time.monotonic() - start

    # ── Phase 4: Handle parse failure ─────────────────────────────────
    if not parse_result.success:
        return _parse_error_result(parse_result, round(elapsed, 4))

    # ── Phase 5: Success — normalise and return ───────────────────────
    return ValidationResult(
        passed=parse_result.passed,
        confidence=parse_result.confidence,
        error=None,
        verdict_raw=parse_result.verdict_raw,
        duration_seconds=round(elapsed, 4),
        error_category="",
    )


def run_glm_validation_from_stdout(
    stdout: str,
    *,
    duration_seconds: float = 0.0,
) -> ValidationResult:
    """Run the GLM-5.1 output parser only — for when CLI output is already captured.

    Useful for:
    - Replaying historical validation output
    - Testing with pre-recorded GLM-5.1 responses
    - Coordinator crash recovery (output already saved to disk)

    Skips the CLI invocation entirely and goes straight to parsing.

    Args:
        stdout: Raw GLM-5.1 stdout (already captured).
        duration_seconds: Duration to record in the result (for
                          consistency with the full pipeline).

    Returns:
        ``ValidationResult`` from parsing only.  There is no CLI error
        path here — all failures are parse-level.

    Examples:
        >>> result = run_glm_validation_from_stdout(
        ...     stdout='{"verdict": "pass", "overall_score": 0.92}',
        ... )
        >>> result.passed
        True
    """
    start = time.monotonic()

    parse_result = parse_glm_output(stdout)

    elapsed = time.monotonic() - start
    if duration_seconds > 0:
        elapsed = duration_seconds

    if not parse_result.success:
        return _parse_error_result(parse_result, round(elapsed, 4))

    return ValidationResult(
        passed=parse_result.passed,
        confidence=parse_result.confidence,
        error=None,
        verdict_raw=parse_result.verdict_raw,
        duration_seconds=round(elapsed, 4),
        error_category="",
    )
