#!/usr/bin/env python3
"""Phase 29 live meeting-thread smoke.

Creates one CEO-owned Discord meeting thread and projects visible team-lead
messages from the live 7-bot profile model into that shared thread. Default mode
is dry-run with an injected fake HTTP client; pass --execute-live to call
Discord REST.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.multi_bot import (  # noqa: E402
    BotMessage,
    _discord_env_for_profile,
    route_bot_projection,
)
from src.runtime_architecture_v2.projection import (  # noqa: E402
    DiscordLiveBoundaryPolicy,
    LiveDiscordThreadManager,
    SharedMeetingThreadProjectionPolicy,
    _default_discord_http_post,
)

CEO_PROFILE = "aicompanyceo"
DEFAULT_PARENT_CHANNEL_ID = "1505600167221526621"
TEAM_LEAD_MESSAGES: tuple[tuple[str, str], ...] = (
    (
        "ceo_coordinator",
        "회의를 개설합니다. 각 팀장은 핵심 의견을 같은 쓰레드에 남겨주세요.",
    ),
    (
        "content_lead",
        "콘텐츠팀 의견: 팬이 바로 이해할 수 있는 콘셉트와 에피소드 후킹이 우선입니다.",
    ),
    (
        "art_lead",
        "아트팀 의견: 캐릭터 실루엣, 컬러 팔레트, "
        "썸네일 가독성을 먼저 고정해야 합니다.",
    ),
    (
        "tech_lead",
        "기술팀 의견: 자동화 파이프라인은 실패 시 중단·기록·재시도 가능해야 합니다.",
    ),
    (
        "marketing_lead",
        "마케팅팀 의견: 첫 공개 메시지는 타깃 팬덤 언어와 "
        "공유 포인트가 분명해야 합니다.",
    ),
    (
        "quality_lead",
        "품질관리 의견: 공개 전 안전성, 저작권, 브랜드 리스크 체크를 통과해야 합니다.",
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Discord meeting thread and post team-lead messages."
    )
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="Actually call Discord REST. Omit for fake dry-run.",
    )
    parser.add_argument(
        "--parent-channel-id",
        default=DEFAULT_PARENT_CHANNEL_ID,
        help="CEO/coordinator parent channel ID where the thread is created.",
    )
    parser.add_argument(
        "--thread-name",
        default="Phase29 실시간 팀장 회의 스모크",
        help="Discord thread name, sanitized and capped to 100 characters.",
    )
    parser.add_argument(
        "--meeting-run-id",
        default="phase29_live_meeting_thread_smoke",
        help="Trace id embedded in projection event ids.",
    )
    return parser.parse_args(argv)


def _fake_http_post(url: str, **kwargs: Any) -> dict[str, object]:
    if url.endswith("/threads"):
        return {"status_code": 201, "json": {"id": "dry-run-thread-123"}}
    return {"status_code": 200, "json": {"id": f"dry-run-message-{abs(hash(url))}"}}


def _env_for_role(role: str, *, execute_live: bool) -> dict[str, str]:
    if not execute_live:
        return {"DISCORD_BOT_TOKEN": f"dry-run-token-{role}"}
    profile_by_role = {
        "ceo_coordinator": "aicompanyceo",
        "content_lead": "aicompanycontent",
        "art_lead": "aicompanyart",
        "tech_lead": "aicompanytech",
        "marketing_lead": "aicompanymarketing",
        "quality_lead": "aicompanyquality",
    }
    return _discord_env_for_profile(profile_by_role[role])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    boundary_policy = DiscordLiveBoundaryPolicy.current_verified()
    http_post = _default_discord_http_post if args.execute_live else _fake_http_post
    ceo_env = (
        _discord_env_for_profile(CEO_PROFILE)
        if args.execute_live
        else {"DISCORD_BOT_TOKEN": "dry-run-token-ceo"}
    )

    thread_manager = LiveDiscordThreadManager(
        env=ceo_env,
        http_post=http_post,
        boundary_policy=boundary_policy,
        profile=CEO_PROFILE,
        guild_id=boundary_policy.guild_id,
    )
    thread_result = thread_manager.create_meeting_thread(
        parent_channel_id=args.parent_channel_id,
        name=args.thread_name,
    )
    if thread_result.status != "created":
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": "live" if args.execute_live else "dry-run",
                    "stage": "create_thread",
                    "thread_status": thread_result.status,
                    "error": thread_result.error,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    shared_policy = SharedMeetingThreadProjectionPolicy(
        boundary_policy=boundary_policy,
        parent_channel_id=args.parent_channel_id,
        thread_id=thread_result.thread_id,
    )
    projection_results = []
    for index, (role, content) in enumerate(TEAM_LEAD_MESSAGES, start=1):
        msg = BotMessage(
            bot_role=role,
            meeting_run_id=args.meeting_run_id,
            round=index,
            msg_type="opinion" if index > 1 else "meeting_open",
            content=content,
        )
        projection_results.append(
            route_bot_projection(
                msg,
                live_discord=True,
                target_channel_id=args.parent_channel_id,
                target_thread_id=thread_result.thread_id,
                env=_env_for_role(role, execute_live=args.execute_live),
                discord_http_post=http_post,
                shared_thread_policy=shared_policy,
            )
        )

    ok = all(result.status == "published" for result in projection_results)
    print(
        json.dumps(
            {
                "ok": ok,
                "mode": "live" if args.execute_live else "dry-run",
                "parent_channel_id": args.parent_channel_id,
                "thread_id": thread_result.thread_id,
                "thread_status": thread_result.status,
                "posted": sum(
                    1 for result in projection_results if result.status == "published"
                ),
                "projection_results": [
                    {
                        "event_id": result.event_id,
                        "status": result.status,
                        "discord_message_id": result.discord_message_id,
                        "error": result.error,
                    }
                    for result in projection_results
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
