"""classify() orchestrator — wires prompt builder + CLI wrapper + response parser.

Sub-AC 2c-4: Single-entry-point orchestrator that integrates the three
Qwen router pipeline stages into one ``classify(meeting_topic) →
ClassificationResult`` function.

Pipeline::

    meeting_topic
        │
        ▼
    build_classification_prompt()      ← qwen_router (Sub-AC 2c-1)
        │
        ▼
    write context file to disk
        │
        ▼
    invoke_qwen()                      ← opencode_qwen_wrapper (Sub-AC 2c-2)
        │
        ▼
    parse_response()                   ← response_parser (Sub-AC 2c-3)
        │
        ▼
    ClassificationResult

Every failure path — empty topic, prompt build error, file write error,
CLI timeout / non-zero exit, empty response, parse failure — returns a
valid ``ClassificationResult`` with ``validation_verdict`` set to
``"fail"`` and diagnostic information in ``reasoning``.  No bare
exceptions propagate to the Coordinator.

The CLi wrapper is injectable via ``_injected_runner`` for testing
without real ``opencode-go`` calls.  A mock runner returning controlled
(exit_code, stdout, stderr) tuples exercises every error path.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Callable

from src.opencode_qwen_wrapper import (
    OpencodeCallConfig,
    OpencodeCallResult,
    SubprocessRunner,
    invoke_qwen,
)
from src.qwen_router import build_classification_prompt
from src.rate_limit_guard import (
    guard_llm_call,
    is_rate_limit_error,
    handle_rate_limit,
)
from src.response_parser import ClassificationResult, parse_response


# ── Context file helpers ────────────────────────────────────────────────

def _write_context_file(prompt: str, meeting_id: str | None) -> str:
    """Write the classification prompt to a JSON context packet file.

    The file follows the context-packet convention: a JSON object with
    a ``prompt`` key containing the full classification prompt string.
    The file is placed under ``meetings/{meeting_id}/`` when
    *meeting_id* is provided, otherwise in a system temp directory.

    Args:
        prompt: The full classification prompt from
                ``build_classification_prompt()``.
        meeting_id: Optional meeting ID for directory-isolated storage.

    Returns:
        Absolute path to the written context file.

    Raises:
        OSError: If the directory cannot be created or file written.
    """
    packet = {"prompt": prompt}

    if meeting_id:
        meeting_dir = os.path.join("meetings", meeting_id)
        os.makedirs(meeting_dir, exist_ok=True)
        fd, path = tempfile.mkstemp(
            suffix=".json",
            prefix="classify_context_",
            dir=meeting_dir,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(packet, fp, ensure_ascii=False)
    else:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="classify_context_",
            delete=False,
            encoding="utf-8",
        ) as fp:
            json.dump(packet, fp, ensure_ascii=False)
            path = fp.name

    return path


# ── Public API ───────────────────────────────────────────────────────────

# The default Qwen model used for meeting topic classification.
_DEFAULT_QWEN_MODEL = "qwen-max"

# Default classification timeout (seconds).
_DEFAULT_TIMEOUT = 120.0


def classify(
    meeting_topic: str,
    *,
    meeting_id: str | None = None,
    model: str = _DEFAULT_QWEN_MODEL,
    timeout_seconds: float = _DEFAULT_TIMEOUT,
    _injected_runner: SubprocessRunner | None = None,
) -> ClassificationResult:
    """Classify a meeting topic through the Qwen LLM router pipeline.

    This is the **single entry point** for Sub-AC 2c-4 — the orchestrator
    that wires together prompt construction → CLI invocation → response
    parsing into one call.

    Pipeline stages:
    1. Validate input
    2. Build classification prompt via ``build_classification_prompt()``
    3. Write prompt to a JSON context file on disk
    4. Invoke Qwen via ``opencode-go`` CLI with the context file
    5. Parse raw stdout into ``ClassificationResult``
    6. Clean up context file
    7. Return result (or failure result on any error)

    Args:
        meeting_topic: Raw meeting agenda text from the user.
        meeting_id: Optional meeting ID for directory-isolated temp files.
                    When provided, context files are written under
                    ``meetings/{meeting_id}/``.
        model: Qwen model name to use (default: ``"qwen-max"``).
        timeout_seconds: Maximum wall-clock time for the CLI subprocess.
        _injected_runner: Override the subprocess runner for testing.
                          When ``None`` (default), the production
                          ``_default_subprocess_runner`` is used.

    Returns:
        ``ClassificationResult`` — always returns a valid object, never
        raises.  Check ``result.is_valid`` or
        ``result.validation_verdict`` to determine if the classification
        is usable.

    Examples:
        >>> result = classify(
        ...     "신규 캐릭터 '루나'의 비주얼 디자인 회의",
        ...     meeting_id="meeting_20260610_test",
        ... )
        >>> result.is_valid
        True
        >>> result.agenda_type
        'creative_production'

        With a mock runner for testing::

            def mock_runner(cmd, timeout, env, wd):
                return (0, '{"agenda_type": "general_planning", ...}', "")

            result = classify(
                "Test topic",
                _injected_runner=mock_runner,
            )
    """
    # ── Stage 1: Input validation ───────────────────────────────────
    if not meeting_topic or not meeting_topic.strip():
        return ClassificationResult(
            agenda_type="general_planning",
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            teams=(),
            priority="P2",
            confidence=0.0,
            reasoning="Empty meeting topic — classification not possible",
            validation_score=0.0,
            validation_verdict="fail",
            validator_required=True,
            codex_required=False,
        )

    topic = meeting_topic.strip()

    # ── Stage 2: Build prompt ───────────────────────────────────────
    try:
        prompt = build_classification_prompt(topic)
    except ValueError as exc:
        return ClassificationResult(
            agenda_type="general_planning",
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            teams=(),
            priority="P2",
            confidence=0.0,
            reasoning=f"Prompt build error: {exc}",
            validation_score=0.0,
            validation_verdict="fail",
            validator_required=True,
            codex_required=False,
        )

    # ── Stage 3: Write context file ─────────────────────────────────
    context_file: str | None = None
    try:
        context_file = _write_context_file(prompt, meeting_id)
    except OSError as exc:
        return ClassificationResult(
            agenda_type="general_planning",
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            teams=(),
            priority="P2",
            confidence=0.0,
            reasoning=f"Context file write error: {exc}",
            validation_score=0.0,
            validation_verdict="fail",
            validator_required=True,
            codex_required=False,
        )

    try:
        # ── Stage 4: Invoke Qwen CLI ────────────────────────────────
        # 4a. Pre-call quota guard
        guard = guard_llm_call()
        if not guard.can_proceed:
            return ClassificationResult(
                agenda_type="general_planning",
                tags=(),
                risk_tags=(),
                required_roles=(),
                optional_roles=(),
                teams=(),
                priority="P2",
                confidence=0.0,
                reasoning=f"Rate limit guard: {guard.reason}",
                validation_score=0.0,
                validation_verdict="fail",
                validator_required=True,
                codex_required=False,
                exit_condition=guard.exit_condition,
            )

        config = OpencodeCallConfig(
            model=model,
            context_file=context_file,
            timeout_seconds=timeout_seconds,
        )

        cli_result: OpencodeCallResult = invoke_qwen(
            config,
            _injected_runner=_injected_runner,
        )

        # 4b. Check for rate-limit errors in stderr/stdout
        if not cli_result.success and (
            is_rate_limit_error(cli_result.stderr)
            or is_rate_limit_error(cli_result.stdout)
        ):
            # Attempt backoff and retry once
            retry_result = handle_rate_limit(
                invoke_qwen,
                config,
                max_retries=1,
                backoff_seconds=60.0,
            )
            if retry_result is not None and retry_result.success:
                cli_result = retry_result
            else:
                return ClassificationResult(
                    agenda_type="general_planning",
                    tags=(),
                    risk_tags=(),
                    required_roles=(),
                    optional_roles=(),
                    teams=(),
                    priority="P2",
                    confidence=0.0,
                    reasoning=(
                        "Rate limit hit — backoff retry failed. "
                        f"Original error: {cli_result.error_message}"
                    ),
                    validation_score=0.0,
                    validation_verdict="fail",
                    validator_required=True,
                    codex_required=False,
                    exit_condition="rate_limit_paused",
                )

        if not cli_result.success:
            return ClassificationResult(
                agenda_type="general_planning",
                tags=(),
                risk_tags=(),
                required_roles=(),
                optional_roles=(),
                teams=(),
                priority="P2",
                confidence=0.0,
                reasoning=(
                    f"CLI invocation failed: {cli_result.error_message}"
                ),
                validation_score=0.0,
                validation_verdict="fail",
                validator_required=True,
                codex_required=False,
            )

        # ── Stage 5: Parse response ─────────────────────────────────
        raw_output = cli_result.stdout
        if not raw_output or not raw_output.strip():
            return ClassificationResult(
                agenda_type="general_planning",
                tags=(),
                risk_tags=(),
                required_roles=(),
                optional_roles=(),
                teams=(),
                priority="P2",
                confidence=0.0,
                reasoning="CLI returned empty stdout",
                validation_score=0.0,
                validation_verdict="fail",
                validator_required=True,
                codex_required=False,
            )

        result = parse_response(raw_output)

        # If parse_response returned a failure but CLI succeeded,
        # enrich the reasoning with diagnostic info
        if not result.is_valid:
            # parse_response already sets appropriate verdict and score;
            # just return it as-is — the Coordinator handles it upstream
            pass

        return result

    finally:
        # ── Stage 6: Clean up context file ──────────────────────────
        if context_file is not None:
            try:
                os.unlink(context_file)
            except OSError:
                # Best-effort cleanup — failure is non-fatal
                pass
