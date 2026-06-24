# Phase 19: Autonomous Scheduling Daemon — Plan

## Goal

Phase 18의 dispatch loop를 **주기적으로 자동 발동**하는 daemon layer.
정기 회의(데일리 스탠드업, 주간 리뷰 등)를 자동으로 MeetingRun 생성 → dispatch까지 이어지게 한다.

## Design Principle

```
Phase 16: Kanban plan (무엇을 할지)
Phase 17: Health scan (어디가 아픈지)
Phase 18: Dispatch loop (실행하고 회복하기)
Phase 19: Daemon (언제 시작할지 — 시계)
```

- **Cron-like tick**: 설정된 스케줄에 따라 주기적으로 tick
- **Health-gated**: Phase 17 health report로 stuck runs 과다 시 신규 발의 skip
- **Idempotent**: 같은 tick interval 내 중복 실행 방지 (last_tick timestamp)
- **Dry-run default**: 실제 MeetingRun 생성 없이 "would create"만 보고

## Scope

### In Scope

1. `RecurringMeetingSpec` — 반복 회의 정의 (schedule, trigger, priority, worker_roles)
2. `DaemonTick` — daemon의 1회 tick 실행 결과
3. `AutonomousDaemon` — schedule loop runner
4. `run_phase19_daemon_tick()` — CLI 진입점 (dry-run + live)
5. Phase 17 health gate: stuck runs > threshold → skip_new_meetings
6. Phase 18 dispatch: 새 MeetingRun 생성 시 dispatch loop 자동 연결
7. Last tick timestamp 추적 (중복 방지)

### Out of Scope

- 실제 cron job 등록 (Hermes cronjob tool 사용은 별도)
- Discord 알림
- 29개 bot 전개
- Always-on 통합

## Data Structures

```python
@dataclass(frozen=True)
class RecurringMeetingSpec:
    spec_id: str
    name: str                    # "Daily Standup", "Weekly Review"
    schedule: str                # cron expression or "every 24h", "every 7d"
    trigger_text: str            # MeetingRun trigger
    priority: str                # P0-P3
    worker_roles: tuple[str, ...]
    enabled: bool = True

@dataclass(frozen=True)
class DaemonTick:
    ok: bool
    dry_run: bool
    tick_id: str
    scheduled_meetings: int      # 정기 회의 스펙 수
    created_runs: int            # 실제 생성된 MeetingRun 수
    skipped_health: int          # health gate로 skip된 수
    skipped_recent: int          # last_tick 이내로 skip된 수
    dispatch_results: tuple[...] # Phase 18 dispatch 결과들
    health_report: HealthReport  # Phase 17 health
    error: str
```

## Acceptance Criteria

1. **AC-1**: dry-run에서 recurring specs만큼 "would create" 보고, 실제 MeetingRun 생성 안 함
2. **AC-2**: live 모드에서 새 MeetingRun 생성 + Phase 18 dispatch 자동 연결
3. **AC-3**: health gate 작동 — stuck runs > max_stuck_threshold → skip_new_meetings=true
4. **AC-4**: last_tick timestamp로 tick_interval 이내 중복 실행 방지
5. **AC-5**: disabled spec은 건너뜀
6. **AC-6**: DaemonTick JSON 아티팩트 저장
7. **AC-7**: dispatch 결과에 secret/token 누출 없음
