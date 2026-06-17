"""Qwen router prompt template for meeting topic classification.

This module provides the Qwen LLM classification prompt template that
serves as the primary meeting topic router for the multi-agent meeting
system.  The prompt instructs Qwen to analyse a raw meeting topic and
return a structured JSON classification that drives downstream routing:
agenda_type, tags, risk_tags, required_roles, optional_roles,
validator_required, and codex_required.

The template is designed to be:
- Deterministic: same topic → identical prompt (testable without LLM)
- Self-contained: all context (teams, roles, agenda types, risk
  categories) embedded in the system prompt
- Structured-output: enforces a specific JSON schema so the Coordinator
  can deterministically parse the response
- Fallback-compatible: the static routing_rules.yaml serves as
  guardrail when Qwen is unavailable
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Classification result dataclass ────────────────────────────────────

@dataclass(frozen=True)
class QwenClassificationResult:
    """Structured classification produced by the Qwen router.

    This is the parsed output the Coordinator uses to determine meeting
    routing, team composition, and validation strategy.
    """

    agenda_type: str
    """Classified meeting type (e.g. creative_production)."""

    tags: tuple[str, ...]
    """Topic tags for routing and knowledge retrieval."""

    risk_tags: tuple[str, ...]
    """Risk-indicating tags that trigger validation/escalation rules."""

    required_roles: tuple[str, ...]
    """Role IDs that must participate for valid quorum."""

    optional_roles: tuple[str, ...]
    """Role IDs that may participate if capacity allows."""

    validator_required: bool
    """Whether GLM-5.1 validation is needed for this meeting."""

    codex_required: bool
    """Whether Codex GPT-5.5 dual-validation is needed."""

    confidence: float = 1.0
    """Router confidence 0.0–1.0 (populated by Qwen response)."""

    reasoning: str = ""
    """Brief explanation of classification rationale."""


# ── Team and role registry (embedded in prompt) ────────────────────────

# 6 teams with 29 specialised roles + 7 persistent team-leaders
# Format: team_name → list of role_id entries
TEAM_ROLES: dict[str, tuple[dict[str, str], ...]] = {
    "coordination": (
        {"role_id": "coordinator", "display_name": "Coordinator",
         "role_type": "coordinator",
         "description": "Meeting facilitation, routing, state machine, final synthesis"},
    ),
    "content_production": (
        {"role_id": "content-pd", "display_name": "Content PD",
         "role_type": "leader",
         "description": "Content production lead — scripts, storyboards, outlines"},
        {"role_id": "scriptwriter", "display_name": "Scriptwriter",
         "role_type": "worker",
         "description": "Script and dialogue writing"},
        {"role_id": "storyboard-artist", "display_name": "Storyboard Artist",
         "role_type": "worker",
         "description": "Visual sequence planning and scene composition"},
        {"role_id": "music-director", "display_name": "Music Director",
         "role_type": "worker",
         "description": "BGM, sound design, audio direction"},
        {"role_id": "voice-director", "display_name": "Voice Director",
         "role_type": "worker",
         "description": "Voice acting direction and casting notes"},
        {"role_id": "video-editor", "display_name": "Video Editor",
         "role_type": "worker",
         "description": "Editing, post-production planning"},
    ),
    "art_design": (
        {"role_id": "art-director", "display_name": "Art Director",
         "role_type": "leader",
         "description": "Art team lead — visual identity, style guide, quality"},
        {"role_id": "concept-artist", "display_name": "Concept Artist",
         "role_type": "worker",
         "description": "Character, environment, prop concept design"},
        {"role_id": "illustrator", "display_name": "Illustrator",
         "role_type": "worker",
         "description": "Final illustration, key art, promotional images"},
        {"role_id": "ui-designer", "display_name": "UI Designer",
         "role_type": "worker",
         "description": "Interface, HUD, menu, web design"},
        {"role_id": "vfx-artist", "display_name": "VFX Artist",
         "role_type": "worker",
         "description": "Visual effects, particle systems, compositing"},
        {"role_id": "animator", "display_name": "Animator",
         "role_type": "worker",
         "description": "2D/3D animation, motion design"},
    ),
    "tech_development": (
        {"role_id": "tech-director", "display_name": "Tech Director",
         "role_type": "leader",
         "description": "Tech team lead — architecture, tool pipeline, quality"},
        {"role_id": "game-engine-dev", "display_name": "Game Engine Developer",
         "role_type": "worker",
         "description": "Unity/Unreal engine development"},
        {"role_id": "backend-dev", "display_name": "Backend Developer",
         "role_type": "worker",
         "description": "API, database, server infrastructure"},
        {"role_id": "frontend-dev", "display_name": "Frontend Developer",
         "role_type": "worker",
         "description": "Web, mobile, client UI development"},
        {"role_id": "devops-engineer", "display_name": "DevOps Engineer",
         "role_type": "worker",
         "description": "CI/CD, deployment, monitoring, cloud infrastructure"},
        {"role_id": "security-engineer", "display_name": "Security Engineer",
         "role_type": "worker",
         "description": "Security audit, penetration testing, compliance"},
        {"role_id": "data-engineer", "display_name": "Data Engineer",
         "role_type": "worker",
         "description": "Data pipeline, analytics, ML infrastructure"},
    ),
    "marketing": (
        {"role_id": "marketing-lead", "display_name": "Marketing Lead",
         "role_type": "leader",
         "description": "Marketing team lead — strategy, campaigns, brand voice"},
        {"role_id": "sns-strategist", "display_name": "SNS Strategist",
         "role_type": "worker",
         "description": "Twitter/X, Instagram, TikTok strategy"},
        {"role_id": "pr-specialist", "display_name": "PR Specialist",
         "role_type": "worker",
         "description": "Press releases, media relations, crisis comms"},
        {"role_id": "community-manager", "display_name": "Community Manager",
         "role_type": "worker",
         "description": "Fan community, Discord, live event engagement"},
        {"role_id": "market-analyst", "display_name": "Market Analyst",
         "role_type": "worker",
         "description": "Market research, competitor analysis, trend tracking"},
    ),
    "execution": (
        {"role_id": "execution-lead", "display_name": "Execution Lead",
         "role_type": "leader",
         "description": "OpenClaw tool-use execution team lead"},
        {"role_id": "code-executor", "display_name": "Code Executor",
         "role_type": "executor",
         "description": "Code generation, multi-file editing, build/test"},
        {"role_id": "asset-executor", "display_name": "Asset Executor",
         "role_type": "executor",
         "description": "Asset pipeline, image/video processing, format conversion"},
        {"role_id": "automation-executor", "display_name": "Automation Executor",
         "role_type": "executor",
         "description": "CI/CD, deployment, scheduled task execution"},
    ),
}

# Flattened role lookup for prompt injection
ALL_ROLES: tuple[dict[str, str], ...] = tuple(
    role for team_roles in TEAM_ROLES.values() for role in team_roles
)


# ── Agenda type definitions ────────────────────────────────────────────

AGENDA_TYPES: tuple[dict[str, str], ...] = (
    {"type": "creative_production",
     "description": "Content creation — scripts, storyboards, music, video, art"},
    {"type": "technical_development",
     "description": "Code, architecture, infrastructure, tooling, security"},
    {"type": "marketing_strategy",
     "description": "Promotion, SNS, PR, community, brand, market analysis"},
    {"type": "risk_assessment",
     "description": "Legal, budget, security, compliance, incident response"},
    {"type": "general_planning",
     "description": "Strategy, roadmap, brainstorming, project scoping"},
    {"type": "project_review",
     "description": "Status review, milestone check, decision retrospective"},
)


# ── Risk tag definitions ───────────────────────────────────────────────

RISK_CATEGORIES: tuple[dict[str, str], ...] = (
    {"tag": "budget", "description": "Financial cost, budget allocation"},
    {"tag": "legal", "description": "Copyright, licensing, regulatory compliance"},
    {"tag": "security", "description": "Data breach, vulnerability, access control"},
    {"tag": "brand", "description": "Reputation, public image, messaging risk"},
    {"tag": "schedule", "description": "Timeline, deadline, resource availability"},
    {"tag": "technical", "description": "Technical debt, architecture risk, tooling gaps"},
    {"tag": "data_loss", "description": "Data integrity, backup, irreversible operations"},
    {"tag": "external", "description": "Third-party dependency, market shift, platform risk"},
)


# ── Prompt template ────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are the Qwen Meeting Topic Classifier for an AI Virtual Entertainment "
    "Company. Your role is to analyse a user's meeting request and produce a "
    "structured classification that drives the entire meeting orchestration "
    "pipeline: which teams must attend, what risks to guard against, and whether "
    "validation layers are required.\n\n"
    "---\n"
    "## Company Structure\n\n"
    "The company has 6 teams with 29 specialised roles:\n\n"
    "{team_descriptions}\n\n"
    "---\n"
    "## Agenda Types\n\n"
    "{agenda_type_descriptions}\n\n"
    "---\n"
    "## Risk Categories\n\n"
    "Tag these risks when detected in the meeting topic. Risk tags trigger "
    "downstream validation and escalation rules.\n\n"
    "{risk_tag_descriptions}\n\n"
    "---\n"
    "## Classification Rules\n\n"
    "1. **agenda_type** — pick exactly ONE from the agenda types above that "
    "best matches the meeting topic. Default to 'general_planning' if unclear.\n\n"
    "2. **tags** — 3–8 topic tags for routing and knowledge retrieval. Use "
    "kebab-case English (e.g. 'character-design', 'backend-api'). Include "
    "the domain, subdomain, and any specific technology/format mentioned.\n\n"
    "3. **risk_tags** — list ONLY risk tags from the categories above that "
    "apply. Be conservative: tag only when genuinely risky. An empty list "
    "is valid for low-risk topics.\n\n"
    "4. **required_roles** — role IDs that MUST participate for valid quorum. "
    "Always include 'coordinator'. Include the relevant team leader(s) for "
    "the agenda type. Add validator-role only when validator_required=true. "
    "Add specialist workers when the topic demands their specific expertise.\n\n"
    "5. **optional_roles** — role IDs that MAY participate if capacity allows. "
    "These are 'nice to have' but the meeting can proceed without them.\n\n"
    "6. **validator_required** — set true when the topic involves: budget, "
    "schedule commitments, legal/compliance, security, data operations, "
    "external publication, brand decisions, or when risk_tags is non-empty. "
    "Default: true for most production meetings.\n\n"
    "7. **codex_required** — set true ONLY when the topic meets one of these "
    "escalation triggers: budget/schedule impact, legal/copyright issues, "
    "external publication/brand direction, code/automation/security/data-loss "
    "risk, unstable consensus anticipated, latest-information-dependent items. "
    "Default: false for most meetings.\n\n"
    "8. **confidence** — your confidence in this classification (0.0–1.0). "
    "Be honest: use lower confidence for ambiguous or multi-domain topics.\n\n"
    "9. **reasoning** — one or two sentences explaining why you chose this "
    "classification.\n\n"
    "---\n"
    "## Output Format\n\n"
    "Respond with ONLY a valid JSON object. No markdown fences, no preamble, "
    "no commentary outside the JSON.\n\n"
    "```json\n"
    "{{\n"
    '  "agenda_type": "<type>",\n'
    '  "tags": ["tag1", "tag2", ...],\n'
    '  "risk_tags": ["risk1", ...],\n'
    '  "required_roles": ["role_id1", "role_id2", ...],\n'
    '  "optional_roles": ["role_id3", ...],\n'
    '  "validator_required": true|false,\n'
    '  "codex_required": true|false,\n'
    '  "confidence": 0.0-1.0,\n'
    '  "reasoning": "<brief explanation>"\n'
    "}}\n"
    "```\n\n"
    "---\n"
    "## Meeting Topic\n\n"
    "{topic}"
)


def _describe_team(team_name: str, roles: tuple[dict[str, str], ...]) -> str:
    """Format a single team's description block for the prompt."""
    clean = team_name.replace("_", " ").title()
    lines = [f"### {clean}"]
    for i, role in enumerate(roles, 1):
        lines.append(
            f"  {i}. **{role['display_name']}** (`{role['role_id']}`) "
            f"[{role['role_type']}]: {role['description']}"
        )
    return "\n".join(lines)


def _describe_agenda_types() -> str:
    """Format agenda type descriptions for the prompt."""
    lines: list[str] = []
    for at in AGENDA_TYPES:
        lines.append(f"- **{at['type']}**: {at['description']}")
    return "\n".join(lines)


def _describe_risk_tags() -> str:
    """Format risk tag descriptions for the prompt."""
    lines: list[str] = []
    for rt in RISK_CATEGORIES:
        lines.append(f"- **{rt['tag']}**: {rt['description']}")
    return "\n".join(lines)


def build_team_descriptions() -> str:
    """Build the complete team description block for the system prompt."""
    sections: list[str] = []
    for team_name, roles in TEAM_ROLES.items():
        sections.append(_describe_team(team_name, roles))
    return "\n\n".join(sections)


# Cached prompt fragments — built once at import time
_TEAM_DESCRIPTIONS = build_team_descriptions()
_AGENDA_TYPE_DESCRIPTIONS = _describe_agenda_types()
_RISK_TAG_DESCRIPTIONS = _describe_risk_tags()


def build_classification_prompt(topic: str) -> str:
    """Build the complete Qwen classification prompt for a meeting topic.

    The returned prompt contains the full system instruction, team/role
    registry, agenda type definitions, risk categories, classification
    rules, output format specification, and the injected meeting topic.

    Args:
        topic: The raw meeting agenda text from the user.

    Returns:
        Complete prompt string ready to send to Qwen LLM.

    Raises:
        ValueError: If ``topic`` is empty or whitespace-only.
    """
    if not topic or not topic.strip():
        raise ValueError("Meeting topic must not be empty")

    return _SYSTEM_PROMPT.format(
        team_descriptions=_TEAM_DESCRIPTIONS,
        agenda_type_descriptions=_AGENDA_TYPE_DESCRIPTIONS,
        risk_tag_descriptions=_RISK_TAG_DESCRIPTIONS,
        topic=topic.strip(),
    )


# ── Response parsing (for production use) ──────────────────────────────

def parse_classification_response(
    raw_text: str,
    *,
    default_agenda_type: str = "general_planning",
) -> QwenClassificationResult:
    """Parse a Qwen LLM JSON response into a structured classification.

    Handles common LLM output artefacts: markdown fences, trailing
    whitespace, and extra text before/after the JSON block.

    Args:
        raw_text: Raw text response from the Qwen LLM.
        default_agenda_type: Fallback agenda_type if parsing fails.

    Returns:
        QwenClassificationResult with parsed values.

    Raises:
        ValueError: If the response contains no parseable JSON.
    """
    import json
    import re

    text = raw_text.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Find the outermost JSON object
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start == -1 or brace_end == -1:
        raise ValueError("No JSON object found in Qwen response")

    json_text = text[brace_start : brace_end + 1]

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in Qwen response: {exc}") from exc

    agenda_type = str(data.get("agenda_type", default_agenda_type)).strip()
    tags = _parse_string_list(data.get("tags", []))
    risk_tags = _parse_string_list(data.get("risk_tags", []))
    required_roles = _parse_string_list(data.get("required_roles", []))
    optional_roles = _parse_string_list(data.get("optional_roles", []))
    validator_required = bool(data.get("validator_required", False))
    codex_required = bool(data.get("codex_required", False))
    confidence = float(data.get("confidence", 0.5))
    reasoning = str(data.get("reasoning", "")).strip()

    return QwenClassificationResult(
        agenda_type=agenda_type,
        tags=tags,
        risk_tags=risk_tags,
        required_roles=required_roles,
        optional_roles=optional_roles,
        validator_required=validator_required,
        codex_required=codex_required,
        confidence=min(max(confidence, 0.0), 1.0),
        reasoning=reasoning,
    )


def _parse_string_list(value: object) -> tuple[str, ...]:
    """Safely parse a JSON value into a tuple of non-empty strings."""
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        s = str(item).strip().lower()
        if s:
            result.append(s)
    return tuple(result)


# ── Prompt introspection (for testing without LLM) ─────────────────────

REQUIRED_OUTPUT_FIELDS: tuple[str, ...] = (
    "agenda_type",
    "tags",
    "risk_tags",
    "required_roles",
    "optional_roles",
    "validator_required",
    "codex_required",
    "confidence",
    "reasoning",
)

REQUIRED_PROMPT_SECTIONS: tuple[str, ...] = (
    "## Company Structure",
    "## Agenda Types",
    "## Risk Categories",
    "## Classification Rules",
    "## Output Format",
    "## Meeting Topic",
)
