# Discord Channel Function Matrix

Status: current verified live inventory, approved by user on 2026-06-27.

This document records the actual Discord channel names, IDs, and operational functions for the AI virtual company server. The channel split is not based on “one bot room per bot” alone. The final rule is:

> Discord channels are company operation surfaces. The 7 live bots project into selected home channels, while non-home channels support cross-team announcements, project indexing, operator control, and system log digests.

## Source of truth

The executable source of truth is:

- `src/runtime_architecture_v2/discord_channels.py`

The live Discord projection allowlist is derived from that matrix through:

- `current_discord_home_channel_ids_by_profile()`
- `DiscordLiveBoundaryPolicy.current_verified()`

## Function matrix

| Category | Channel | ID | Home profile | Primary function | Function definition |
|---|---|---:|---|---|---|
| 📋 경영 | 전략-회의실 | `1505600167221526621` | `aicompanyceo` | strategy_decision_room | 최종 의사결정, 우선순위, Phase 승인/보류를 기록한다. |
| 📋 경영 | 일일-브리핑 | `1507063720025522267` | `aicompanyassistant` | user_daily_dashboard | 사용자가 매일 먼저 보는 회사 상태 요약과 다음 행동 대시보드. |
| 🎬 콘텐츠제작팀 | 콘텐츠-메인 | `1505927982722580500` | `aicompanycontent` | content_story_planning | 스토리, 세계관, 대본, 콘텐츠 캘린더와 팬 참여 기획을 다룬다. |
| 🎨 아트팀 | 아트-메인 | `1505928014800752671` | `aicompanyart` | visual_creative_assets | 캐릭터, 배경, 무드보드, 이미지 프롬프트와 비주얼 리뷰를 다룬다. |
| ⚙️ 기술팀 | 기술-메인 | `1505928578016219247` | `aicompanytech` | build_ops_incident | 구현, 인프라, 테스트, 배포, 장애 분석과 기술 의사결정을 다룬다. |
| 📣 마케팅팀 | 마케팅-메인 | `1505931658426060970` | `aicompanymarketing` | audience_growth_branding | 팬덤, 브랜딩, SNS, 시장성, 출시/홍보 전략을 다룬다. |
| 🔀 크로스팀 | 전체-메인 | `1505931688327381042` | - | cross_team_announcements | 회사 전체 공지와 크로스팀 공유를 위한 채널. 일일 브리핑과 분리한다. |
| 🔀 크로스팀 | 전체-리뷰 | `1507063654397378561` | `aicompanyquality` | qa_risk_release_gate | QA, 법무/저작권/개인정보 리스크, pass/revise/reject release gate를 기록한다. |
| 🔀 크로스팀 | 프로젝트-허브 | `1507235292694974645` | - | project_thread_index | 프로젝트별 thread/index, MeetingRun 링크, Phase 산출물 위치를 추적한다. |
| ⚙️ 관리 | 마스터-컨트롤 | `1505931705582878830` | - | operator_control_plane | 운영자 전용 상태 확인, quota 확인, 긴급 중단, live smoke 결과를 다룬다. |
| ⚙️ 관리 | 시스템-로그 | `1507235209878442105` | - | system_log_digest | Gateway, job, smoke, test, 배포 상태의 요약 로그를 기록한다. 원본 로그 덤프는 금지한다. |

## Non-overlap rules

### 일일-브리핑 vs 전체-메인

- `일일-브리핑`: 사용자 개인이 매일 먼저 보는 상태 요약과 다음 행동.
- `전체-메인`: 회사 전체 공지와 크로스팀 공유.

### 전략-회의실 vs 프로젝트-허브

- `전략-회의실`: 결정하는 곳.
- `프로젝트-허브`: 결정된 프로젝트를 추적하는 곳.

### 전체-리뷰 vs 시스템-로그

- `전체-리뷰`: 산출물과 의사결정의 품질/리스크/release gate.
- `시스템-로그`: 시스템, 봇, 테스트, 배포 상태의 요약 로그.

### 마스터-컨트롤 vs 시스템-로그

- `마스터-컨트롤`: 운영자가 명시적으로 조작하는 control plane.
- `시스템-로그`: 자동화된 상태 digest. 원본 로그 덤프 금지.

## Projection rules

Home projection is allowed only for these 7 profiles:

- `aicompanyassistant` → `일일-브리핑`
- `aicompanyceo` → `전략-회의실`
- `aicompanycontent` → `콘텐츠-메인`
- `aicompanyart` → `아트-메인`
- `aicompanytech` → `기술-메인`
- `aicompanymarketing` → `마케팅-메인`
- `aicompanyquality` → `전체-리뷰`

The following channels are intentionally not 7-bot home projection channels:

- `전체-메인`
- `프로젝트-허브`
- `마스터-컨트롤`
- `시스템-로그`

They may be used later by explicit router features, but they should not be silently added to `DiscordLiveBoundaryPolicy.current_verified()` as home channels.

## Guardrails

- No Administrator permission expansion.
- Mention-gated posture remains.
- No free-response channels.
- Do not post raw worker logs or unsanitized secrets.
- Discord remains a projection/control UI; the authoritative workflow state remains in Hermes/MeetingRun/runtime artifacts.
