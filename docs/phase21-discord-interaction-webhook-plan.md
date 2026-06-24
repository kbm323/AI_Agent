# Phase 21: Discord Interaction Webhook / Slash Command — Plan

## Goal

7개 Discord Bot이 실제 slash command와 interaction webhook을 통해 사용자와 상호작용할 수 있도록 스키마와 라우팅 계층을 정의한다.
실제 Discord 앱 등록/배포는 토큰이 필요한 live 작업이므로, 스키마 정의 + dry-run 시뮬레이션으로 완성한다.

## Design Principle

```
Phase 20: 29-role org chart registry (누가 있는지)
Phase 21: Slash commands + webhooks (어떻게 대화하는지)
```

- **DiscordInteraction** = Discord에서 오는 webhook payload (표준 Discord Interaction 구조)
- **DiscordCommandRouter** = interaction을 파싱해서 적절한 bot+MeetingRun으로 라우팅
- **SlashCommand** = 명령어 정의 (이름, 설명, 옵션, 담당 bot)
- **Dry-run**: 실제 Discord webhook 없이 payload simulation + routing 검증

## Scope

### In Scope

1. `DiscordInteraction` — webhook payload schema (type, user, channel, command, options)
2. `SlashCommand` — 명령어 정의 (name, description, handler_bot, options)
3. `DiscordCommandRouter` — interaction → bot routing + MeetingRun 생성
4. `InteractionResponse` — bot 응답 schema (content, ephemeral, embeds)
5. 기본 5개 slash command 정의
6. Dry-run: simulated interaction → routing → response
7. Artifact: slash command manifest (Discord API 등록용 JSON)

### Out of Scope

- Discord API에 실제 command 등록
- Webhook endpoint hosting
- Discord OAuth/token

## Slash Commands

| Command | Handler Bot | 설명 |
|---------|-------------|------|
| `/회의` | 비서 (Hermes) | 새 회의 시작, MeetingRun 생성 |
| `/상태` | 비서 (Hermes) | 현재 회사 상태, 진행 중인 MeetingRun 목록 |
| `/보고` | 대표 (CEO) | 최종 보고 요청 |
| `/팀작업` | 각 팀장 | 특정 팀에 작업 지시 (옵션: 팀 선택) |
| `/도움` | 비서 (Hermes) | 사용 가능한 명령어 목록 |

## Data Structures

```python
@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    handler_bot: str
    options: tuple[SlashCommandOption, ...]
    
@dataclass(frozen=True)
class DiscordInteraction:
    interaction_id: str
    type: int  # 2=slash command
    user_id: str
    channel_id: str
    command_name: str
    options: dict[str, str]
    guild_id: str = ""
    
@dataclass(frozen=True)
class InteractionResponse:
    ok: bool
    interaction_id: str
    content: str
    ephemeral: bool = True
    handler_bot: str = ""
    meeting_run_id: str = ""
    
class DiscordCommandRouter:
    route(interaction) → InteractionResponse
    → command 조회 → handler bot 확인
    → MeetingRun 생성 (Phase 19 daemon 패턴)
    → 응답 생성
```

## Acceptance Criteria

1. **AC-1**: 5개 기본 slash command 등록, handler_bot 모두 유효한 bot_registry role
2. **AC-2**: DiscordCommandRouter.route()가 command_name → handler_bot 매핑 정확
3. **AC-3**: /회의 command → MeetingRun 생성 + meeting_run_id 반환
4. **AC-4**: 알 수 없는 command → ok=False, 안내 메시지
5. **AC-5**: InteractionResponse에 secret/token 누출 없음
6. **AC-6**: slash command manifest JSON → Discord API 등록 포맷 준수
7. **AC-7**: dry-run CLI → simulated interaction 처리 결과 출력
