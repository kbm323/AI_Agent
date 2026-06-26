"""Phase 30 role-specific model policy adapter.

The 7 Discord-facing Hermes profiles are projection endpoints. The canonical
internal specialist roles are the 29 kebab-case IDs defined in
``config/routing_rules.yaml``. This module bridges those internal roles to the
model policy shape consumed by Runtime Architecture v2 worker tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.fallback_rules_loader import RoleSpec, load_fallback_rules

_CANONICAL_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "routing_rules.yaml"
)

# Compatibility for older Phase 13/14 pilot role IDs. These are aliases only;
# public policy maps should expose canonical kebab-case role IDs.
_ROLE_ALIASES: dict[str, str] = {
    "content_lead": "content-director",
    "content_director": "content-director",
    "art_lead": "art-director",
    "art_director": "art-director",
    "tech_lead": "tech-director",
    "tech_director": "tech-director",
    "marketing_lead": "marketing-lead",
    "quality_lead": "validator",
    "validation_audit": "validator",
    "business_support_lead": "execution-lead",
    "ceo_coordinator": "execution-lead",
}

_TEAM_PROJECTION_PROFILES: dict[str, str] = {
    "content-production": "aicompanycontent",
    "art-design": "aicompanyart",
    "tech-engineering": "aicompanytech",
    "marketing": "aicompanymarketing",
    "validation": "aicompanyquality",
    "execution": "aicompanyceo",
}

_CODEX_ESCALATION_TEAMS = frozenset({"validation", "tech-engineering"})
_CODEX_ESCALATION_ROLES = frozenset({"legal-reviewer", "security-engineer"})


@dataclass(frozen=True)
class RoleModelPolicy:
    """Worker-facing model policy for one canonical internal role."""

    role_id: str
    team: str
    role_type: str
    persistent_bot: bool
    provider: str
    primary_model: str
    fallback_chain: tuple[str, ...]
    validator_model: str
    codex_escalation: bool
    projection_profile: str
    execution_role: str

    @property
    def preferred(self) -> str:
        """Backward-compatible preferred model name used by worker runners."""

        return self.primary_model

    def to_worker_model_policy(self) -> dict[str, object]:
        """Return the serializable policy shape stored on ``WorkerTask``."""

        return {
            "role_id": self.role_id,
            "team": self.team,
            "role_type": self.role_type,
            "persistent_bot": self.persistent_bot,
            "provider": self.provider,
            "preferred": self.primary_model,
            "primary_model": self.primary_model,
            "fallback_chain": list(self.fallback_chain),
            "validator_model": self.validator_model,
            "codex_escalation": self.codex_escalation,
            "projection_profile": self.projection_profile,
            "execution_role": self.execution_role,
        }


def normalize_role_id(role_id: str) -> str:
    """Normalize legacy pilot IDs to canonical internal role IDs."""

    raw = str(role_id).strip()
    if not raw:
        raise KeyError("unknown role: empty role id")
    return _ROLE_ALIASES.get(raw, raw)


def load_role_model_policies(
    path: str | Path = _CANONICAL_RULES_PATH,
) -> dict[str, RoleModelPolicy]:
    """Load all 29 canonical role model policies from routing rules."""

    rules = load_fallback_rules(path)
    policies: dict[str, RoleModelPolicy] = {}
    for role in rules.roles:
        policy = _policy_from_role(role, validator_model=rules.defaults.validator_model)
        policies[policy.role_id] = policy
    return policies


def get_role_model_policy(
    role_id: str,
    *,
    path: str | Path = _CANONICAL_RULES_PATH,
) -> RoleModelPolicy:
    """Return the model policy for a canonical role or legacy alias."""

    canonical = normalize_role_id(role_id)
    policies = load_role_model_policies(path)
    try:
        return policies[canonical]
    except KeyError as exc:
        raise KeyError(f"unknown role: {role_id}") from exc


def projection_profile_for_role(
    role_id: str,
    *,
    path: str | Path = _CANONICAL_RULES_PATH,
) -> str:
    """Return the 7-bot Hermes profile that should project a role's output."""

    return get_role_model_policy(role_id, path=path).projection_profile


def worker_model_policy_for_role(
    role_id: str,
    *,
    path: str | Path = _CANONICAL_RULES_PATH,
) -> dict[str, object]:
    """Convenience helper for constructing ``WorkerTask.model_policy``."""

    return get_role_model_policy(role_id, path=path).to_worker_model_policy()


def _policy_from_role(role: RoleSpec, *, validator_model: str) -> RoleModelPolicy:
    projection_profile = _TEAM_PROJECTION_PROFILES.get(role.team, "aicompanyceo")
    fallback_chain = (role.model.fallback,) if role.model.fallback else ()
    codex_escalation = (
        role.team in _CODEX_ESCALATION_TEAMS
        or role.role_id in _CODEX_ESCALATION_ROLES
        or role.role_type == "validator"
    )
    return RoleModelPolicy(
        role_id=role.role_id,
        team=role.team,
        role_type=role.role_type,
        persistent_bot=role.persistent_bot,
        provider=role.model.provider,
        primary_model=role.model.name,
        fallback_chain=fallback_chain,
        validator_model=validator_model,
        codex_escalation=codex_escalation,
        projection_profile=projection_profile,
        execution_role=role.role_type or "worker",
    )


__all__ = [
    "RoleModelPolicy",
    "get_role_model_policy",
    "load_role_model_policies",
    "normalize_role_id",
    "projection_profile_for_role",
    "worker_model_policy_for_role",
]
