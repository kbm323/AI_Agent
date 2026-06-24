# Phase 22: Always-on Autonomous Company 통합 — Plan

## Goal

Phase 13~21의 모든 모듈을 하나의 **AutonomousCompany** 런타임으로 통합.
1회 `run()` 호출로 전체 자율 회사 사이클(건강체크→정기회의→디스패치→지식저장)을 실행한다.

## Design Principle

```
AutonomousCompany.run()
  ├─ Phase 17: health scan → stuck 확인
  ├─ Phase 19: daemon tick → 정기회의 발의
  ├─ Phase 18: dispatch loop → 작업 분배
  ├─ Phase 15: knowledge loop → Second Brain 갱신
  └─ Phase 21: command simulation → interaction 검증

Phase 20: bot registry → worker pool lookup
Phase 14: multi-bot → meeting protocol
Phase 16: kanban ops → card graph
```

## Data Structures

```python
@dataclass(frozen=True)
class CompanyCycleResult:
    ok: bool
    dry_run: bool
    cycle_id: str
    health: HealthReport           # Phase 17
    daemon_tick: DaemonTick         # Phase 19
    dispatch_results: tuple[...]    # Phase 18
    knowledge_updated: bool         # Phase 15
    commands_simulated: int         # Phase 21
    total_meeting_runs: int
    active_bots: int                # Phase 20
    error: str

class AutonomousCompany:
    def __init__(root, dry_run, specs)
    def run() → CompanyCycleResult
```

## Acceptance Criteria

1. **AC-1**: run()이 모든 phase 모듈을 순차 호출, 하나라도 실패 시 중단 없이 계속
2. **AC-2**: CompanyCycleResult에 모든 phase 결과 포함
3. **AC-3**: dry-run에서 실제 MeetingRun 생성 없음
4. **AC-4**: health gate → stuck 과다 시 daemon tick skip
5. **AC-5**: CLI dry-run → 전체 사이클 1회 실행 + JSON 결과
6. **AC-6**: Artifact 저장 (runtime/phase22-company/)
7. **AC-7**: error 필드 sanitize (secret/token 누출 없음)
