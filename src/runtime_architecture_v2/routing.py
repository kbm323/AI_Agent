"""Routing adapter policy for Runtime Architecture v2.

This module defines a tiny project-local router boundary.  The real Qwen
integration can implement the same interface later; tests use FakeQwenRouter so
no external model or Hermes core change is required.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from .schemas import MeetingRun, RoutingResult


class RouteType(StrEnum):
    FAST_QA = "fast_qa"
    CREATIVE_MEETING = "creative_meeting"
    TECHNICAL_EXECUTION = "technical_execution"
    LEGAL_RISK = "legal_risk"
    MIXED_REQUEST = "mixed_request"


class RoutingAdapter(Protocol):
    """Boundary implemented by Qwen or deterministic fakes."""

    def route(self, meeting_run: MeetingRun) -> RoutingResult:
        """Classify a MeetingRun into domain teams, workers, and validators."""
        raise NotImplementedError


class FakeQwenRouter:
    """Deterministic keyword router for simulation and tests."""

    def route(self, meeting_run: MeetingRun) -> RoutingResult:
        text = str((meeting_run.trigger or {}).get("text", "")).lower()
        meeting_run_id = meeting_run.meeting_run_id

        if self._is_legal_or_risk(text):
            return RoutingResult(
                meeting_run_id=meeting_run_id,
                route_type=RouteType.LEGAL_RISK,
                teams=("business_support_lead", "validation_audit"),
                validators=("glm_validator", "codex_auditor"),
                research_owner="business_support_lead",
                execution_required=False,
                estimated_rounds=1,
                projection_policy="risk_report",
                confidence=0.91,
                rationale="Legal, copyright, contract, or policy risk request.",
            )

        if self._is_mixed(text):
            return RoutingResult(
                meeting_run_id=meeting_run_id,
                route_type=RouteType.MIXED_REQUEST,
                teams=(
                    "content_lead",
                    "art_lead",
                    "tech_lead",
                    "marketing_lead",
                    "business_support_lead",
                ),
                worker_roles=(
                    "creative_director",
                    "software_engineer",
                    "growth_strategist",
                ),
                validators=("glm_validator", "codex_auditor"),
                research_owner="tech_lead",
                execution_required=True,
                estimated_rounds=3,
                projection_policy="team_lead_threads",
                confidence=0.82,
                rationale="Cross-functional creative, market, and technical request.",
            )

        if self._is_technical_execution(text):
            return RoutingResult(
                meeting_run_id=meeting_run_id,
                route_type=RouteType.TECHNICAL_EXECUTION,
                teams=("tech_lead",),
                worker_roles=("software_engineer", "test_engineer"),
                validators=("glm_validator", "codex_auditor"),
                research_owner="tech_lead",
                execution_required=True,
                estimated_rounds=1,
                projection_policy="execution_status",
                confidence=0.9,
                rationale="Implementation or test execution request.",
            )

        if self._is_creative(text):
            return RoutingResult(
                meeting_run_id=meeting_run_id,
                route_type=RouteType.CREATIVE_MEETING,
                teams=("content_lead", "art_lead", "marketing_lead"),
                worker_roles=(
                    "creative_director",
                    "concept_artist",
                    "growth_strategist",
                ),
                validators=("glm_validator",),
                research_owner="content_lead",
                execution_required=False,
                estimated_rounds=2,
                projection_policy="team_lead_summary",
                confidence=0.88,
                rationale="Creative planning meeting request.",
            )

        return RoutingResult(
            meeting_run_id=meeting_run_id,
            route_type=RouteType.FAST_QA,
            teams=("ceo_coordinator",),
            worker_roles=(),
            validators=(),
            research_owner="",
            execution_required=False,
            estimated_rounds=0,
            projection_policy="direct_reply",
            confidence=0.96,
            rationale="Simple answer can stay inside Hermes coordinator path.",
        )

    @staticmethod
    def _is_legal_or_risk(text: str) -> bool:
        return any(
            token in text
            for token in ("계약", "저작권", "법무", "legal", "risk", "리스크")
        )

    @staticmethod
    def _is_technical_execution(text: str) -> bool:
        return any(
            token in text
            for token in ("구현", "테스트", "코드", "adapter", "어댑터", "실행")
        )

    @staticmethod
    def _is_creative(text: str) -> bool:
        return any(
            token in text
            for token in ("뮤비", "뮤직비디오", "콘셉트", "기획", "creative")
        )

    def _is_mixed(self, text: str) -> bool:
        signals = sum(
            (
                self._is_creative(text),
                self._is_technical_execution(text),
                any(token in text for token in ("홍보", "마케팅", "전략", "growth")),
                self._is_legal_or_risk(text),
            )
        )
        return signals >= 3
