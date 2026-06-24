# Company Second Brain Log

> Append-only log of company Second Brain actions.
> Format: `## [YYYY-MM-DD] action | subject`

## [2026-06-21] create | Company Second Brain initialized
- Created Aiden/Karpathy-style raw/wiki structure.
- Scope: company strategy, research, meeting decisions, validated outputs.

## [2026-06-24] update | Phase 1-18 full construction log

### Runtime Architecture v2 Foundation (Phase 1-11)
| Phase | Subject | Commit |
|-------|---------|--------|
| 1 | Schema Layer (MeetingRun, WorkerTask, ValidationVerdict, etc.) | 249c2d3 |
| 2 | File Store / State / Logs | f5733ec |
| 3 | Routing / Queue / Scheduling Policy | 8c8e19e |
| 4 | Worker Execution Boundary | bdff06e |
| 4.5 | opencode-go Live Smoke Boundary | ba5b4f8 |
| 5 | Validation Layer | 4aac999 |
| 6 | Discord Projection Layer | c3a91f8 |
| 7 | Runtime Orchestrator / full fake MeetingRun flow | b99367a |
| 8 | Security / quota / observability policies | 23949db |
| 9 | End-to-end simulation CLI | 0f327f5 |
| 10 | Live adapter wiring boundaries | 5c8cd04 |
| 11 | Final verification | 953e072 |

Key decisions:
- Hermes Core 수정 최소화, opencode-go CLI 우선
- MeetingRun = 모든 회의/작업/검증/보고의 장부
- Discord = projection layer only, source of truth = runtime/meeting_runs/

### Live Hardening (Phase 12)
| Phase | Subject | Commit |
|-------|---------|--------|
| 12.1 | Discord live projection smoke | 903f41a |
| 12.2 | opencode-go worker live smoke | f7a77b9 |
| 12.3 | Bot permission inventory / hardening | c938bc1 |
| 12.4 | Token rotation decision: do not rotate | 5f50ff9 |
| 12.5 | Personal assistant UX/channel cleanup | b8906ae |

### Autonomous Company (Phase 13-18)
| Phase | Subject | Commit | Tests |
|-------|---------|--------|-------|
| 13 | Live Company Workflow Pilot | 96212c8 | 14 |
| 14 | Multi-bot Operational Protocol | 96212c8 | 14 |
| 15 | Persistent Second Brain / Knowledge Loop | 662c264 | 11 |
| 16 | Autonomous Scheduling / Kanban Operations | e1743ec | 8 |
| 17 | Production Readiness / Monitoring / Recovery | 0cb6e44 | 9 |
| 18 | Live Kanban Autonomous Dispatch Loop | 1fc71f9 | 22 |

Key decisions:
- Phase 16: KanbanCardSpec dry-run → card graph with queue_store=none
- Phase 17: Stuck detection (non-terminal + age > stuck_hours), recovery triage
- Phase 18: Recovery loop (blocked→reassign, max_reclaim→escalate), KanbanClient Protocol
- 모든 phase: secret/token/bearer/@mention sanitize, no custom queue store, no Hermes Core mutation

## Module Map

```
src/runtime_architecture_v2/
  schemas.py          # MeetingRun, WorkerTask, ValidationVerdict, RecoveryCheckpoint
  store.py            # File-backed MeetingRunStore
  routing.py          # Qwen-style routing adapter
  queue_policy.py     # PriorityQueuePolicy, ConcurrencyPolicy
  scheduling_policy.py # SchedulingPolicy → hermes_kanban/cron/background_process
  workers.py          # WorkerRunner, FakeWorkerRunner
  validation.py       # ValidationPolicy, ValidationDecision
  projection.py       # DiscordProjectionFormatter, DiscordProjectionSink
  policies.py         # SecurityPolicy, QuotaPolicy, ObservabilityPolicy
  orchestrator.py     # RuntimeOrchestrator (full MeetingRun flow)
  pilot.py            # Phase 13 company workflow pilot
  multi_bot.py        # Phase 14 multi-bot protocol
  knowledge.py        # Phase 15 Second Brain knowledge loop
  kanban_ops.py       # Phase 16 Kanban operation planning
  production.py       # Phase 17 health scan / recovery triage
  dispatch_loop.py    # Phase 18 autonomous dispatch loop
  simulation_cli.py   # Phase 9 e2e simulation CLI

tests/
  test_runtime_architecture_v2_*.py  # 174 tests total (all passing)

scripts/
  run_phase{13,14,15,16,17,18}_*.py # Phase pilot CLI entry points
```

## Debug quick reference

- Module not found? → check `sys.path.insert(0, REPO_ROOT)`, import as `from src.runtime_architecture_v2.X import Y`
- Test failure? → run `python3 -m pytest tests/test_runtime_architecture_v2_phaseN_*.py -v --tb=long`
- MeetingRun stuck? → `python3 scripts/run_phase17_health_check.py` then `scripts/run_phase18_autonomous_dispatch.py`
- Secret leak? → grep for `_sanitize_text`, `_TOKEN_PATTERNS`, `_MENTION_RE` across all modules
- ruff check? → `ruff check src/ tests/ scripts/`
