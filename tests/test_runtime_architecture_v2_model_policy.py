"""Phase 30 role model policy tests.

These tests lock the distinction between the 7 Discord-facing profiles and the
29 internal specialist roles. The canonical internal role IDs come from
config/routing_rules.yaml, while legacy pilot IDs remain aliases only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_architecture_v2.model_policy import (
    RoleModelPolicy,
    get_role_model_policy,
    load_role_model_policies,
    normalize_role_id,
    projection_profile_for_role,
)

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "config" / "routing_rules.yaml"


def test_loads_29_canonical_role_model_policies() -> None:
    policies = load_role_model_policies(RULES_PATH)

    assert len(policies) == 29
    assert "content-director" in policies
    assert "validator" in policies
    assert "content_lead" not in policies
    assert all(isinstance(policy, RoleModelPolicy) for policy in policies.values())


def test_every_policy_exposes_worker_model_fields() -> None:
    policies = load_role_model_policies(RULES_PATH)

    for role_id, policy in policies.items():
        assert policy.role_id == role_id
        assert policy.provider == "opencode-go"
        assert policy.primary_model
        assert policy.preferred == policy.primary_model
        assert isinstance(policy.fallback_chain, tuple)
        assert policy.validator_model == "glm-5.1"
        assert policy.projection_profile.startswith("aicompany")
        as_dict = policy.to_worker_model_policy()
        assert as_dict["role_id"] == role_id
        assert as_dict["provider"] == policy.provider
        assert as_dict["preferred"] == policy.primary_model
        assert as_dict["primary_model"] == policy.primary_model
        assert isinstance(as_dict["fallback_chain"], list)
        assert as_dict["projection_profile"] == policy.projection_profile


def test_runtime_worker_policies_do_not_select_unsupported_deepseek_v3() -> None:
    policies = load_role_model_policies(RULES_PATH)

    unsupported = {
        role_id: policy.to_worker_model_policy()
        for role_id, policy in policies.items()
        if policy.primary_model == "deepseek-v3"
        or "deepseek-v3" in policy.fallback_chain
    }

    assert unsupported == {}


def test_legacy_meeting_roles_use_supported_live_models() -> None:
    assert get_role_model_policy("ceo_coordinator", path=RULES_PATH).primary_model == "deepseek-v4-pro"
    assert get_role_model_policy("tech_lead", path=RULES_PATH).primary_model == "deepseek-v4-pro"


def test_projection_profiles_keep_29_roles_behind_7_discord_profiles() -> None:
    assert projection_profile_for_role("content-director", path=RULES_PATH) == "aicompanycontent"
    assert projection_profile_for_role("scriptwriter", path=RULES_PATH) == "aicompanycontent"
    assert projection_profile_for_role("art-director", path=RULES_PATH) == "aicompanyart"
    assert projection_profile_for_role("character-designer", path=RULES_PATH) == "aicompanyart"
    assert projection_profile_for_role("tech-director", path=RULES_PATH) == "aicompanytech"
    assert projection_profile_for_role("security-engineer", path=RULES_PATH) == "aicompanytech"
    assert projection_profile_for_role("marketing-lead", path=RULES_PATH) == "aicompanymarketing"
    assert projection_profile_for_role("legal-reviewer", path=RULES_PATH) == "aicompanyquality"
    assert projection_profile_for_role("execution-lead", path=RULES_PATH) == "aicompanyceo"


def test_legacy_pilot_role_ids_are_aliases_not_canonical_ids() -> None:
    assert normalize_role_id("content_lead") == "content-director"
    assert normalize_role_id("art_lead") == "art-director"
    assert normalize_role_id("tech_lead") == "tech-director"
    assert normalize_role_id("marketing_lead") == "marketing-lead"
    assert normalize_role_id("quality_lead") == "validator"
    assert normalize_role_id("validation_audit") == "validator"

    policy = get_role_model_policy("quality_lead", path=RULES_PATH)
    assert policy.role_id == "validator"
    assert policy.primary_model.startswith("glm")


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(KeyError, match="unknown role"):
        get_role_model_policy("not-a-real-role", path=RULES_PATH)

    with pytest.raises(KeyError, match="unknown role"):
        projection_profile_for_role("not-a-real-role", path=RULES_PATH)
