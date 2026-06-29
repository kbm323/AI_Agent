"""Per-persona single opinion generator for the multi-agent meeting system.

Sub-AC 5a-2: Given one persona definition and one agenda item, produces a
complete opinion packet via LLM call, testable by mocking the LLM and
verifying output matches the expected packet schema.

Architecture
------------
The module follows the same injectable-runner pattern as
``opencode_qwen_wrapper.py`` so all LLM calls are mockable without
real ``opencode-go`` binary or network access.  The pipeline is:

1. **Build persona prompt** — construct a system+user prompt from the
   persona definition and agenda item.
2. **Invoke LLM** — call ``opencode-go --model <model> --context-file <packet>``
   via the injectable ``SubprocessRunner``.
3. **Parse response** — extract the JSON opinion packet from the
   LLM's stdout.
4. **Validate** — run the packet through ``validate_opinion_packet()``
   from ``opinion_packet_validator``.
5. **Return** — an ``OpinionGenerationResult`` with either the
   validated packet or error details.

Usage::

    from src.persona_opinion_generator import (
        OpinionGenerationResult,
        PersonaDefinition,
        generate_opinion,
    )

    persona = PersonaDefinition(
        role_id="art-director",
        display_name="아트 디렉터",
        team="art-design",
        expertise_tags=("visual_direction", "art_style"),
        model_provider="opencode-go",
        model_name="qwen-max",
        model_fallback="deepseek-v4-pro",
    )
    result = generate_opinion(
        persona=persona,
        agenda_item_ref="character-visual-concept",
        agenda_context="새로운 캐릭터 디자인 콘셉트 회의",
        context_file="/tmp/meetings/.../packet.json",
    )
    if result.success:
        packet = result.opinion_packet  # validated dict
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.opinion_packet_validator import (
    OpinionPacketValidationReport,
    validate_opinion_packet,
)


# ── Persona definition dataclass ────────────────────────────────────────


@dataclass(frozen=True)
class PersonaDefinition:
    """Immutable persona definition for a single role in the meeting system.

    Mirrors the ``agent.yaml`` structure from the design spec (Track C)
    with the core fields needed for opinion generation by Sub-AC 5a-2.

    Attributes:
        role_id: Unique kebab-case role identifier (e.g. ``art-director``).
        display_name: Human-readable role name (e.g. ``아트 디렉터``).
        team: Team affiliation (e.g. ``art-design``, ``marketing``).
        role_type: Classification: ``leader``, ``worker``, ``validator``,
                   ``executor``.
        expertise_tags: Topic tags this role handles for routing.
        model_provider: LLM provider name (e.g. ``opencode-go``).
        model_name: Primary model identifier (e.g. ``qwen-max``).
        model_fallback: Fallback model (e.g. ``deepseek-v4-pro``).
        persona_description: Optional extended persona context
                             (loaded from ``persona.md``).
        spec_version: Semantic version of this agent spec.
    """

    role_id: str
    """Kebab-case role identifier."""

    display_name: str
    """Human-readable role name."""

    team: str
    """Team affiliation."""

    expertise_tags: tuple[str, ...]
    """Topic tags this role handles."""

    model_provider: str = "opencode-go"
    """LLM provider name."""

    model_name: str = "qwen-max"
    """Primary model identifier."""

    model_fallback: str = "deepseek-v4-pro"
    """Fallback model identifier."""

    role_type: str = "worker"
    """Classification: leader, worker, validator, executor."""

    persona_description: str = ""
    """Extended persona context (from persona.md)."""

    spec_version: str = "1.0.0"
    """Semantic version of this agent spec."""

    def __post_init__(self) -> None:
        if not self.role_id or not self.role_id.strip():
            raise ValueError("role_id must be a non-empty kebab-case string")
        if not self.display_name or not self.display_name.strip():
            raise ValueError("display_name must be a non-empty string")
        if not self.model_name or not self.model_name.strip():
            raise ValueError("model_name must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        """Serialize to a dict (e.g. for context packet injection)."""
        return {
            "role_id": self.role_id,
            "display_name": self.display_name,
            "team": self.team,
            "role_type": self.role_type,
            "expertise_tags": list(self.expertise_tags),
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "model_fallback": self.model_fallback,
            "spec_version": self.spec_version,
        }


# ── Opinion generation result ───────────────────────────────────────────


@dataclass(frozen=True)
class OpinionGenerationResult:
    """Structured result of a single persona opinion generation attempt.

    Attributes:
        success: ``True`` when a valid opinion packet was produced.
        opinion_packet: The validated opinion packet dict (success only).
        validation_report: The ``OpinionPacketValidationReport`` from the
                           schema validator.
        role_id: The persona's ``role_id`` (for tracing).
        model_name: The model that generated the opinion.
        duration_seconds: Wall-clock elapsed time for the LLM call.
        error_message: Human-readable error description on failure.
        raw_llm_output: Raw stdout from the LLM (for debugging).
    """

    success: bool
    """True when a valid opinion packet was produced."""

    opinion_packet: dict[str, object] | None = None
    """Validated opinion packet dict (success only)."""

    validation_report: OpinionPacketValidationReport | None = None
    """Schema validation report."""

    role_id: str = ""
    """The persona's role_id (for tracing)."""

    model_name: str = ""
    """The model that generated the opinion."""

    duration_seconds: float = 0.0
    """Wall-clock elapsed time for the LLM call."""

    error_message: str = ""
    """Human-readable error description on failure."""

    raw_llm_output: str = ""
    """Raw stdout from the LLM (for debugging)."""


# ── Subprocess runner type (mirrors opencode_qwen_wrapper) ──────────────

SubprocessRunner = Callable[
    [list[str], float, dict[str, str] | None, str | None],
    tuple[int, str, str],
]


# ── Default subprocess runner (production path) ─────────────────────────


def _default_subprocess_runner(
    command: list[str],
    timeout_seconds: float,
    env: dict[str, str] | None,
    workdir: str | None,
) -> tuple[int, str, str]:
    """Execute a command via ``subprocess.run`` and return (code, stdout, stderr).

    This is the production runner, called when no mock is injected.
    """
    import os
    import subprocess

    merged_env = None
    if env is not None:
        merged_env = {**os.environ, **env}

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=merged_env,
            cwd=workdir,
        )
        return (completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        partial_stdout = (
            exc.stdout
            if isinstance(exc.stdout, str)
            else (
                exc.stdout.decode("utf-8", errors="replace")
                if exc.stdout
                else ""
            )
        )
        partial_stderr = (
            exc.stderr
            if isinstance(exc.stderr, str)
            else (
                exc.stderr.decode("utf-8", errors="replace")
                if exc.stderr
                else ""
            )
        )
        return (-1, partial_stdout, partial_stderr)
    except OSError as exc:
        return (-1, "", f"OSError: {exc}")


# ── Thread-local subprocess runner storage ────────────────────────────
# Sub-AC 5a-3: Cross-persona isolation enforcement.
# Each thread maintains its own runner reference so concurrent persona
# generations do not leak state across threads.  The _injected_runner
# parameter on generate_opinion() provides per-call isolation; this
# thread-local store protects the module-level inject_runner / get_runner
# API when used concurrently.

_runner_store: threading.local = threading.local()
"""Thread-local storage for the active subprocess runner."""


def _get_default_runner() -> SubprocessRunner:
    """Return the default production subprocess runner (always a fresh ref)."""
    return _default_subprocess_runner


def inject_runner(runner: SubprocessRunner | None) -> None:
    """Replace the active subprocess runner **for the current thread**.

    Pass ``None`` to restore the default production runner.

    Thread-safe — each thread maintains its own runner reference.
    For per-call isolation, prefer passing ``_injected_runner`` to
    ``generate_opinion()`` directly.
    """
    if runner is None:
        try:
            del _runner_store.value
        except AttributeError:
            pass  # no thread-local set yet
    else:
        _runner_store.value = runner


def get_runner() -> SubprocessRunner:
    """Return the currently active subprocess runner **for the current thread**.

    Returns the thread-local runner if one was injected via
    ``inject_runner()``, otherwise the default production runner.
    """
    try:
        return _runner_store.value  # type: ignore[no-any-return]
    except AttributeError:
        return _get_default_runner()


# ── Prompt template ─────────────────────────────────────────────────────

_OPINION_SYSTEM_PROMPT = (
    "You are {display_name} ({role_id}), a {role_type} in the {team} team "
    "of an AI Virtual Entertainment Company.\n\n"
    "## Your Role\n"
    "You are an expert in: {expertise_tags}\n\n"
    "{persona_extra}"
    "## Meeting Context\n"
    "You are participating in a multi-agent round-table meeting. "
    "Your task is to provide a well-reasoned, professional opinion "
    "on the agenda item below, drawing on your domain expertise.\n\n"
    "## Instructions\n"
    "1. Analyse the agenda item from the perspective of your role.\n"
    "2. Provide 2-5 specific, actionable points or recommendations.\n"
    "3. Consider risks, constraints, and dependencies relevant to your domain.\n"
    "4. Express your confidence in your opinion (0.0-1.0).\n"
    "5. Output ONLY a valid JSON object — no markdown fences, no "
    "preamble, no commentary outside the JSON.\n\n"
    "## Required Output JSON Schema\n"
    "{{\n"
    '  "persona_id": "{role_id}",\n'
    '  "agenda_item_ref": "<agenda_item_ref>",\n'
    '  "opinion_content": "<your complete opinion, arguments, and recommendations>",\n'
    '  "confidence": <float 0.0-1.0>,\n'
    '  "timestamp": "<ISO-8601 e.g. 2026-06-10T14:30:00Z>"\n'
    "}}\n\n"
    "## Agenda Item\n"
    "Ref: {agenda_item_ref}\n"
    "{agenda_context}"
)

# ── Prompt builder ──────────────────────────────────────────────────────


def build_opinion_prompt(
    persona: PersonaDefinition,
    agenda_item_ref: str,
    agenda_context: str,
) -> str:
    """Build the complete persona opinion prompt.

    Args:
        persona: The ``PersonaDefinition`` for the target role.
        agenda_item_ref: Short kebab-case reference for the agenda item.
        agenda_context: Full agenda context / meeting topic description.

    Returns:
        Complete prompt string ready to send to the LLM.

    Raises:
        ValueError: If required fields are empty.
    """
    if not agenda_item_ref or not agenda_item_ref.strip():
        raise ValueError("agenda_item_ref must be a non-empty string")
    if not agenda_context or not agenda_context.strip():
        raise ValueError("agenda_context must be a non-empty string")

    tags_str = ", ".join(persona.expertise_tags) if persona.expertise_tags else "general"

    extra = ""
    if persona.persona_description:
        extra = f"## Persona Context\n{persona.persona_description}\n\n"

    return _OPINION_SYSTEM_PROMPT.format(
        display_name=persona.display_name,
        role_id=persona.role_id,
        role_type=persona.role_type,
        team=persona.team,
        expertise_tags=tags_str,
        persona_extra=extra,
        agenda_item_ref=agenda_item_ref.strip(),
        agenda_context=agenda_context.strip(),
    )


# ── Command builder ─────────────────────────────────────────────────────


def build_opencode_command(model: str, context_file: str) -> list[str]:
    """Construct the ``opencode-go`` CLI command for opinion generation.

    ``opencode-go --model <model> --context-file <packet>``

    Args:
        model: Model name for ``--model``.
        context_file: Path to the JSON context packet file.

    Returns:
        Command list for ``subprocess.run``.

    Raises:
        ValueError: If model or context_file is empty/whitespace.
    """
    if not model or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not context_file or not context_file.strip():
        raise ValueError("context_file must be a non-empty string")

    return [
        "opencode-go",
        "--model",
        model.strip(),
        "--context-file",
        context_file.strip(),
    ]


# ── Response parser ─────────────────────────────────────────────────────


def _extract_json_block(raw_text: str) -> str:
    """Extract the JSON object from an LLM response.

    Handles markdown fences (`` ```json ``` ``), leading/trailing text,
    and common LLM output artefacts.

    Args:
        raw_text: Raw text response from the LLM.

    Returns:
        The extracted JSON text (trimmed).

    Raises:
        ValueError: If no JSON object can be found.
    """
    text = raw_text.strip()

    # Try markdown fence extraction first
    fence_match = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL
    )
    if fence_match:
        text = fence_match.group(1).strip()

    # Find the outermost JSON object
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start == -1 or brace_end == -1:
        raise ValueError("No JSON object found in LLM response")

    return text[brace_start : brace_end + 1]


def parse_opinion_response(raw_text: str) -> dict[str, Any]:
    """Parse an LLM raw response into an opinion packet dict.

    Extracts the JSON block from the raw text and parses it into a dict.
    The caller is responsible for schema validation.

    Args:
        raw_text: Raw text response from the LLM.

    Returns:
        Parsed opinion packet dict.

    Raises:
        ValueError: If no JSON object is found or JSON parsing fails.
    """
    json_text = _extract_json_block(raw_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in LLM response: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"LLM response JSON must be an object, got {type(data).__name__}"
        )

    return data


# ── Public API ──────────────────────────────────────────────────────────


def generate_opinion(
    persona: PersonaDefinition,
    agenda_item_ref: str,
    agenda_context: str,
    *,
    context_file: str = "",
    timeout_seconds: float = 120.0,
    workdir: str | None = None,
    _injected_runner: SubprocessRunner | None = None,
) -> OpinionGenerationResult:
    """Generate a single persona's opinion packet for an agenda item.

    This is the main entry point for **Sub-AC 5a-2**.

    Steps:
     1. Build the persona-specific opinion prompt.
     2. Write the prompt to *context_file* (if provided).
     3. Call ``opencode-go`` via the injectable subprocess runner.
     4. Parse the JSON opinion packet from stdout.
     5. Validate against the opinion packet schema.
     6. Return an ``OpinionGenerationResult``.

    When *context_file* is provided, the prompt is written to that file
    before invoking the CLI.  When empty, the prompt is passed via a
    temp file created automatically.

    Args:
        persona: ``PersonaDefinition`` for the target role.
        agenda_item_ref: Short reference for the agenda item.
        agenda_context: Full agenda context / meeting topic.
        context_file: Path for the ``--context-file`` argument.
                      If empty, a temp file is created.
        timeout_seconds: Max wall-clock time for the subprocess (default 120s).
        workdir: Optional working directory for the subprocess.
        _injected_runner: Override the subprocess runner (for testing).

    Returns:
        ``OpinionGenerationResult`` — check ``result.success`` before
        accessing ``result.opinion_packet``.

    Raises:
        ValueError: If persona or agenda parameters are invalid.
        TypeError: If persona is not a ``PersonaDefinition``.

    Examples:
        >>> persona = PersonaDefinition(
        ...     role_id="art-director",
        ...     display_name="아트 디렉터",
        ...     team="art-design",
        ...     expertise_tags=("visual_direction", "art_style"),
        ...     model_name="qwen-max",
        ... )
        >>> result = generate_opinion(
        ...     persona=persona,
        ...     agenda_item_ref="character-visual-concept",
        ...     agenda_context="새 캐릭터 디자인 회의",
        ... )
        >>> if result.success:
        ...     packet = result.opinion_packet
        ...     print(packet["persona_id"])
    """
    if not isinstance(persona, PersonaDefinition):
        raise TypeError(
            f"persona must be PersonaDefinition, got {type(persona).__name__}"
        )
    if not agenda_item_ref or not agenda_item_ref.strip():
        raise ValueError("agenda_item_ref must be non-empty")
    if not agenda_context or not agenda_context.strip():
        raise ValueError("agenda_context must be non-empty")

    # ── Step 1: Build the prompt ────────────────────────────────────
    prompt = build_opinion_prompt(persona, agenda_item_ref, agenda_context)

    # ── Step 2: Resolve context file ─────────────────────────────────
    import tempfile
    import os as _os

    _temp_file: str | None = None
    if not context_file:
        fd, _temp_file = tempfile.mkstemp(
            suffix=".txt", prefix=f"opinion_{persona.role_id}_"
        )
        _os.close(fd)
        context_file = _temp_file

    try:
        with open(context_file, "w", encoding="utf-8") as f:
            f.write(prompt)

        # ── Step 3: Build and execute command ───────────────────────
        command = build_opencode_command(persona.model_name, context_file)

        runner = (
            _injected_runner if _injected_runner is not None else get_runner()
        )

        start = time.monotonic()
        exit_code, stdout, stderr = runner(
            command, timeout_seconds, None, workdir
        )
        elapsed = time.monotonic() - start

        # ── Step 4: Handle subprocess failure ───────────────────────
        if exit_code != 0:
            error_msg = _format_cli_error(exit_code, stderr, timeout_seconds)
            return OpinionGenerationResult(
                success=False,
                role_id=persona.role_id,
                model_name=persona.model_name,
                duration_seconds=round(elapsed, 4),
                error_message=error_msg,
                raw_llm_output=stdout,
            )

        # ── Step 5: Parse JSON response ─────────────────────────────
        try:
            packet = parse_opinion_response(stdout)
        except ValueError as exc:
            return OpinionGenerationResult(
                success=False,
                role_id=persona.role_id,
                model_name=persona.model_name,
                duration_seconds=round(elapsed, 4),
                error_message=f"Response parsing failed: {exc}",
                raw_llm_output=stdout,
            )

        # ── Step 6: Validate against opinion packet schema ──────────
        report = validate_opinion_packet(packet)

        if not report.passed:
            error_strs = [f"{e.field_name}: {e.message}" for e in report.errors]
            return OpinionGenerationResult(
                success=False,
                validation_report=report,
                role_id=persona.role_id,
                model_name=persona.model_name,
                duration_seconds=round(elapsed, 4),
                error_message=(
                    f"Schema validation failed ({report.error_count} errors): "
                    + "; ".join(error_strs[:5])
                ),
                raw_llm_output=stdout,
            )

        # ── Step 7: Success ─────────────────────────────────────────
        return OpinionGenerationResult(
            success=True,
            opinion_packet=packet,
            validation_report=report,
            role_id=persona.role_id,
            model_name=persona.model_name,
            duration_seconds=round(elapsed, 4),
            raw_llm_output=stdout,
        )

    finally:
        # Clean up temp file if we created one
        if _temp_file is not None:
            try:
                _os.unlink(_temp_file)
            except OSError:
                pass


def _format_cli_error(
    exit_code: int, stderr: str, timeout_seconds: float
) -> str:
    """Format a human-readable error from a CLI failure."""
    if exit_code == -1:
        is_timeout = "timeout" in stderr.lower() if stderr else False
        if is_timeout:
            return f"opencode-go timed out after {timeout_seconds}s"
        return f"opencode-go subprocess error: {stderr[:200] if stderr else 'unknown'}"
    return (
        f"opencode-go exited with code {exit_code}: "
        f"{stderr[:200] if stderr else 'no stderr output'}"
    )


# ── Exports ─────────────────────────────────────────────────────────────

__all__ = [
    "OpinionGenerationResult",
    "PersonaDefinition",
    "SubprocessRunner",
    "build_opencode_command",
    "build_opinion_prompt",
    "generate_opinion",
    "get_runner",
    "inject_runner",
    "parse_opinion_response",
]
