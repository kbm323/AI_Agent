"""Discord channel function matrix for the live AI company guild.

This module records the live Discord inventory verified through the Discord API
and the user's approved operational split. Channel functions are intentionally
separate from bot identity: home channels are projection surfaces for the 7 live
interface bots, while non-home channels are cross-team, project, control, or log
surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscordChannelFunction:
    """Stable function contract for one live Discord text channel."""

    channel_id: str
    name: str
    category: str
    primary_function: str
    purpose: str
    profile: str = ""
    allowed_message_types: tuple[str, ...] = ()
    human_actions: tuple[str, ...] = ()
    forbidden_content: tuple[str, ...] = ()

    @property
    def is_home_channel(self) -> bool:
        """Return True when this channel is a 7-bot profile home projection."""

        return bool(self.profile)

    def to_markdown_row(self) -> str:
        """Render a stable documentation table row."""

        profile = self.profile or "-"
        return (
            f"| {self.category} | {self.name} | {profile} | "
            f"{self.primary_function} | {self.purpose} | "
            f"{', '.join(self.allowed_message_types)} | "
            f"{', '.join(self.human_actions)} | "
            f"{', '.join(self.forbidden_content)} |"
        )


def current_discord_channel_function_matrix() -> tuple[DiscordChannelFunction, ...]:
    """Return the current verified live Discord channel function matrix."""

    return (
        DiscordChannelFunction(
            channel_id="1505600167221526621",
            name="전략-회의실",
            category="📋 경영",
            profile="aicompanyceo",
            primary_function="strategy_decision_room",
            purpose="최종 의사결정, 우선순위, Phase 승인/보류를 기록한다.",
            allowed_message_types=(
                "home_projection",
                "decision",
                "priority_change",
                "approval_request",
                "strategy_summary",
            ),
            human_actions=("approve", "hold", "reprioritize", "request_rework"),
            forbidden_content=("raw_worker_logs", "system_noise"),
        ),
        DiscordChannelFunction(
            channel_id="1507063720025522267",
            name="일일-브리핑",
            category="📋 경영",
            profile="aicompanyassistant",
            primary_function="user_daily_dashboard",
            purpose="사용자가 매일 먼저 보는 회사 상태 요약과 다음 행동 대시보드.",
            allowed_message_types=(
                "home_projection",
                "daily_summary",
                "status_digest",
                "pending_user_action",
                "important_link",
            ),
            human_actions=("ask_status", "request_detail", "acknowledge"),
            forbidden_content=("deep_team_debate", "raw_worker_logs"),
        ),
        DiscordChannelFunction(
            channel_id="1505927982722580500",
            name="콘텐츠-메인",
            category="🎬 콘텐츠제작팀",
            profile="aicompanycontent",
            primary_function="content_story_planning",
            purpose="스토리, 세계관, 대본, 콘텐츠 캘린더와 팬 참여 기획을 다룬다.",
            allowed_message_types=(
                "home_projection",
                "content_proposal",
                "script_draft",
                "story_revision",
                "content_calendar",
            ),
            human_actions=("select_direction", "request_revision", "approve_draft"),
            forbidden_content=("release_gate_verdict", "system_noise"),
        ),
        DiscordChannelFunction(
            channel_id="1505928014800752671",
            name="아트-메인",
            category="🎨 아트팀",
            profile="aicompanyart",
            primary_function="visual_creative_assets",
            purpose="캐릭터, 배경, 무드보드, 이미지 프롬프트와 비주얼 리뷰를 다룬다.",
            allowed_message_types=(
                "home_projection",
                "visual_direction",
                "asset_review",
                "prompt_review",
                "reference_summary",
            ),
            human_actions=("choose_style", "request_variant", "approve_visual"),
            forbidden_content=("raw_generation_dump", "unreviewed_sensitive_asset"),
        ),
        DiscordChannelFunction(
            channel_id="1505928578016219247",
            name="기술-메인",
            category="⚙️ 기술팀",
            profile="aicompanytech",
            primary_function="build_ops_incident",
            purpose="구현, 인프라, 테스트, 배포, 장애 분석과 기술 의사결정을 다룬다.",
            allowed_message_types=(
                "home_projection",
                "implementation_plan",
                "test_result",
                "deployment_result",
                "incident_summary",
            ),
            human_actions=("approve_deploy", "request_debug", "pause_operation"),
            forbidden_content=("unsanitized_secret", "full_raw_log_dump"),
        ),
        DiscordChannelFunction(
            channel_id="1505931658426060970",
            name="마케팅-메인",
            category="📣 마케팅팀",
            profile="aicompanymarketing",
            primary_function="audience_growth_branding",
            purpose="팬덤, 브랜딩, SNS, 시장성, 출시/홍보 전략을 다룬다.",
            allowed_message_types=(
                "home_projection",
                "campaign_proposal",
                "audience_insight",
                "brand_message",
                "launch_plan",
            ),
            human_actions=("choose_campaign", "request_copy", "approve_launch_message"),
            forbidden_content=("qa_release_gate", "raw_private_user_data"),
        ),
        DiscordChannelFunction(
            channel_id="1505931688327381042",
            name="전체-메인",
            category="🔀 크로스팀",
            primary_function="cross_team_announcements",
            purpose="회사 전체 공지와 크로스팀 공유를 위한 채널. 일일 브리핑과 분리한다.",
            allowed_message_types=("announcement", "cross_team_summary", "handoff_notice"),
            human_actions=("acknowledge", "ask_owner", "request_followup"),
            forbidden_content=("team_internal_debate", "raw_worker_logs"),
        ),
        DiscordChannelFunction(
            channel_id="1507063654397378561",
            name="전체-리뷰",
            category="🔀 크로스팀",
            profile="aicompanyquality",
            primary_function="qa_risk_release_gate",
            purpose="QA, 법무/저작권/개인정보 리스크, pass/revise/reject release gate를 기록한다.",
            allowed_message_types=(
                "home_projection",
                "qa_verdict",
                "risk_review",
                "release_gate",
                "revise_request",
            ),
            human_actions=("accept_risk", "request_fix", "block_release", "approve_release"),
            forbidden_content=("unverified_claim", "raw_secret_evidence"),
        ),
        DiscordChannelFunction(
            channel_id="1507235292694974645",
            name="프로젝트-허브",
            category="🔀 크로스팀",
            primary_function="project_thread_index",
            purpose="프로젝트별 thread/index, MeetingRun 링크, Phase 산출물 위치를 추적한다.",
            allowed_message_types=("project_index", "thread_link", "phase_status", "artifact_link"),
            human_actions=("open_project_thread", "request_status", "link_artifact"),
            forbidden_content=("long_debate", "raw_worker_logs"),
        ),
        DiscordChannelFunction(
            channel_id="1505931705582878830",
            name="마스터-컨트롤",
            category="⚙️ 관리",
            primary_function="operator_control_plane",
            purpose="운영자 전용 상태 확인, quota 확인, 긴급 중단, live smoke 결과를 다룬다.",
            allowed_message_types=("operator_command", "quota_status", "emergency_stop", "smoke_result"),
            human_actions=("stop", "restart", "check_quota", "run_smoke"),
            forbidden_content=("free_response_chat", "public_marketing_copy"),
        ),
        DiscordChannelFunction(
            channel_id="1507235209878442105",
            name="시스템-로그",
            category="⚙️ 관리",
            primary_function="system_log_digest",
            purpose="Gateway, job, smoke, test, 배포 상태의 요약 로그를 기록한다. 원본 로그 덤프는 금지한다.",
            allowed_message_types=("gateway_status", "job_digest", "test_digest", "deploy_digest"),
            human_actions=("inspect_detail", "request_rerun", "open_log_file"),
            forbidden_content=("full_raw_log_dump", "unsanitized_secret"),
        ),
    )


def current_discord_home_channel_ids_by_profile() -> dict[str, str]:
    """Return the verified 7-profile home channel allowlist."""

    return {
        channel.profile: channel.channel_id
        for channel in current_discord_channel_function_matrix()
        if channel.profile
    }


def render_discord_channel_function_markdown() -> str:
    """Render the channel function matrix as a markdown table."""

    header = (
        "| Category | Channel | Profile | Primary function | Purpose | "
        "Allowed message types | Human actions | Forbidden content |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    rows = [channel.to_markdown_row() for channel in current_discord_channel_function_matrix()]
    return "\n".join((header, *rows))
