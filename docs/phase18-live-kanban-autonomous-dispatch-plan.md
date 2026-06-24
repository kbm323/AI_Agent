# Phase 18: Live Kanban Autonomous Dispatch — Plan

## Goal

Phase 16의 dry-run Kanban card plan을 **실제 live dispatch → monitor → recover** 하는 자율 루프로 승격시킨다.

Phase 17의 health scan/recovery triage 결과를 Kanban card 수준으로 확장하여, stuck card를 감지하고 자동으로 reclaim/reassign/escalate 할 수 있게 한다.

## Design Principle

```
Phase 16: build plan (dry-run) → "어떻게 할지 결정"
Phase 18: dispatch + monitor + recover → "실행하고 지키는 루프"
```

- **Hermes Kanban은 source of truth** — card state는 Hermes가 소유한다.
- **AI_Agent는 observer + actuator** — card 상태를 폴링하고, 필요 시 action(claim/reassign/complete/unblock)을 수행한다.
- **Recovery loop는 bounded** — 무한 재시도 방지 (max_reclaim_attempts, escalation threshold).
- **Live mode는 injected KanbanClient 뒤에 숨는다** — dry-run은 항상 가능.

## Scope

### In Scope

1. `KanbanCardStatus` — live Kanban card의 observable state (claimed_by, completed_at, blocked_reason 등)
2. `AutonomousDispatchLoop` — plan dispatch → status poll → recovery decision 루프
3. `DispatchLoopResult` — 루프 1회 실행 결과 (dispatched/claimed/recovered/escalated counts)
4. `run_phase18_autonomous_dispatch()` — CLI pilot 진입점 (dry-run + live mode)
5. Phase 17 health report와 통합: stuck run → kanban recovery action 연결
6. Card-level recovery: blocked → unblock signal, orphaned → reclaim, failed → reassign or escalate

### Out of Scope

- 실제 Hermes Kanban API 통합 (injected client boundary만 제공)
- Cron/daemon 형태의 상시 실행 (Phase 19)
- Discord 알림/슬래시 커맨드 (Phase 20/21)
- 29개 bot 전개 (Phase 20)

## Data Structures

### KanbanCardStatus

```python
@dataclass(frozen=True)
class KanbanCardStatus:
    card_id: str
    meeting_run_id: str
    kind: str                           # worker | review
    state: str                          # pending | claimed | completed | blocked | failed
    claimed_by: str                     # assignee or bot role
    completed_at: datetime | None
    blocked_reason: str
    reclaim_count: int                  # 이미 몇 번 reclaim 했는지
    age_hours: float
```

### AutonomousDispatchLoop

```python
class AutonomousDispatchLoop:
    def run(*, root, client, dry_run, max_rounds) -> DispatchLoopResult:
        """1회 full loop: pending dispatch → status poll → recovery"""
```

### DispatchLoopResult

```python
@dataclass(frozen=True)
class DispatchLoopResult:
    ok: bool
    dry_run: bool
    meeting_run_id: str
    round_number: int
    dispatched_count: int
    claimed_count: int
    completed_count: int
    blocked_count: int
    failed_count: int
    recovered_count: int
    escalated_count: int
    card_statuses: tuple[KanbanCardStatus, ...]
    recovery_actions: tuple[RecoveryAction, ...]
    error: str
```

### RecoveryAction

```python
@dataclass(frozen=True)
class RecoveryAction:
    card_id: str
    action: str            # claim | reassign | unblock | escalate | retry
    reason: str
    target_assignee: str
```

## Acceptance Criteria

1. **AC-1**: dry-run 모드에서 plan의 모든 card가 `pending → claimed → completed`로 simulate 된다.
2. **AC-2**: live 모드에서 KanbanClient가 없으면 `kanban_client_required` error 반환.
3. **AC-3**: blocked card가 감지되면 recovery action(unblock or escalate)이 생성된다.
4. **AC-4**: reclaim_count가 max_reclaim_attempts를 초과하면 escalate action으로 전환된다.
5. **AC-5**: Phase 17 stuck run에 Kanban plan이 있으면 Phase 18의 dispatch loop로 연결된다.
6. **AC-6**: 모든 card가 completed되면 loop가 정상 종료한다 (converged=true).
7. **AC-7**: Card status에 meeting_run_id 노출은 허용. secret/token/bearer/mention 누출 없음.
8. **AC-8**: DispatchLoopResult는 JSON 직렬화 가능하며 phase18 dispatch artifact로 저장된다.

## Implementation Steps

1. `src/runtime_architecture_v2/dispatch_loop.py` — KanbanCardStatus, RecoveryAction, DispatchLoopResult, AutonomousDispatchLoop
2. `scripts/run_phase18_autonomous_dispatch.py` — CLI 진입점
3. `tests/test_runtime_architecture_v2_phase18_dispatch_loop.py` — RED → GREEN
4. `docs/phase18-live-kanban-autonomous-dispatch.md` — 결과 문서
5. README.md 갱신
