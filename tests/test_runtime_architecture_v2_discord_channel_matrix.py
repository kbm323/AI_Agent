"""Discord channel function matrix tests.

These tests lock the live Discord channel inventory to the user's approved
operational split: home channels are bot projection surfaces, while non-home
channels have distinct cross-team/control/logging duties.
"""

from src.runtime_architecture_v2.discord_channels import (
    DiscordChannelFunction,
    current_discord_channel_function_matrix,
)
from src.runtime_architecture_v2.projection import DiscordLiveBoundaryPolicy


def test_channel_function_matrix_records_actual_live_inventory():
    matrix = current_discord_channel_function_matrix()

    assert len(matrix) == 11
    by_name = {channel.name: channel for channel in matrix}
    assert set(by_name) == {
        "전략-회의실",
        "일일-브리핑",
        "콘텐츠-메인",
        "아트-메인",
        "기술-메인",
        "마케팅-메인",
        "전체-메인",
        "전체-리뷰",
        "프로젝트-허브",
        "마스터-컨트롤",
        "시스템-로그",
    }

    assert by_name["일일-브리핑"].category == "📋 경영"
    assert by_name["일일-브리핑"].primary_function == "user_daily_dashboard"
    assert by_name["전체-메인"].primary_function == "cross_team_announcements"
    assert by_name["전체-리뷰"].primary_function == "qa_risk_release_gate"
    assert by_name["프로젝트-허브"].primary_function == "project_thread_index"
    assert by_name["마스터-컨트롤"].primary_function == "operator_control_plane"
    assert by_name["시스템-로그"].primary_function == "system_log_digest"


def test_home_channel_functions_match_live_boundary_policy():
    matrix = current_discord_channel_function_matrix()
    policy = DiscordLiveBoundaryPolicy.current_verified()
    home_channels = {
        channel.profile: channel
        for channel in matrix
        if channel.profile
    }

    assert set(home_channels) == set(policy.allowed_channel_ids_by_profile)
    for profile, channel_id in policy.allowed_channel_ids_by_profile.items():
        channel = home_channels[profile]
        assert channel.channel_id == channel_id
        assert channel.is_home_channel is True
        assert "home_projection" in channel.allowed_message_types


def test_non_home_channels_have_distinct_functions_and_no_projection_profile():
    matrix = current_discord_channel_function_matrix()
    non_home = [channel for channel in matrix if not channel.is_home_channel]

    assert {channel.name for channel in non_home} == {
        "전체-메인",
        "프로젝트-허브",
        "마스터-컨트롤",
        "시스템-로그",
    }
    assert all(channel.profile == "" for channel in non_home)
    assert len({channel.primary_function for channel in non_home}) == len(non_home)
    assert all("home_projection" not in channel.allowed_message_types for channel in non_home)


def test_channel_function_to_markdown_row_has_stable_shape():
    channel = DiscordChannelFunction(
        channel_id="1",
        name="테스트",
        category="분류",
        primary_function="sample_function",
        purpose="목적",
        profile="profile",
        allowed_message_types=("summary", "decision"),
        human_actions=("approve",),
        forbidden_content=("raw_logs",),
    )

    assert channel.to_markdown_row() == (
        "| 분류 | 테스트 | profile | sample_function | 목적 | "
        "summary, decision | approve | raw_logs |"
    )
