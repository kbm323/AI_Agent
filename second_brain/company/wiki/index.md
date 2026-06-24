# Company Second Brain Index

> Obsidian-compatible LLM Wiki index for AI Virtual Entertainment Company knowledge.
> Last updated: 2026-06-24 | Total pages: 2

## Entities

- [[Runtime Architecture v2]] — 17 modules, MeetingRun lifecycle, policy chain
- [[AI Virtual Entertainment Company]] — Discord bot topology, 29 roles

## Concepts

- [[MeetingRun]] — Root aggregate: trigger → route → execute → validate → project
- [[KanbanCardSpec]] — Hermes-native card graph for autonomous scheduling
- [[Second Brain]] — raw/ + wiki/ + AGENTS.md pattern

## Meeting Decisions

- Hermes Core 수정 최소화, opencode-go CLI 우선 (Phase 1 결정)
- Discord = projection only, source of truth = runtime/meeting_runs/ (Phase 6 결정)
- Token rotation: do not rotate now (Phase 12.4)
- Bot permission: inventory complete, no admin escalation needed (Phase 12.3)
- queue_store=none, no custom queue database (Phase 16 결정)
- KanbanClient Protocol for live boundary, dry-run default (Phase 18 결정)

## Debug

- Phase timeline and module map: [[log]]
- Test commands: `python3 -m pytest tests/test_runtime_architecture_v2_phaseN_*.py -v`
- Health check: `python3 scripts/run_phase17_health_check.py`
- Dispatch: `python3 scripts/run_phase18_autonomous_dispatch.py --mode dry-run`
- ruff: `ruff check src/ tests/ scripts/`
