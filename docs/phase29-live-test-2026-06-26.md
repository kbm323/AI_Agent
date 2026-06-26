# Phase 29 Live Test — 2026-06-26 KST

## Scope

This was a bounded live test after Codex hourly quota reset. It did not start an unbounded 24h autonomous operation. It verified the live Discord surface, channel routing, projection safety, and Phase 29 readiness gates under the existing Hermes-first constraints.

## Pre-flight

Result: PASS

Checks performed:

- All 7 Hermes profile `.env` files exist.
- All 7 Discord bot tokens returned `/users/@me = 200`.
- All 7 bot accounts are members of guild `Entertainment` (`1505600166676271244`).
- All 7 configured home channels returned `/channels/{channel_id} = 200`.
- All 7 profiles have `DISCORD_REQUIRE_MENTION=true`.
- All 7 profiles have `DISCORD_THREAD_REQUIRE_MENTION=true`.
- All 7 profiles have no `DISCORD_FREE_RESPONSE_CHANNELS` configured.

Quota before start:

```text
Go:    Monthly 35%, Weekly 73%, Hourly 0%
Codex: Monthly 0%,  Weekly 56%, Hourly 3%
Both available
```

## Controlled live projection smoke

Result: PASS

Mode:

```text
phase29_controlled_live_projection_7_channel
```

Run ID:

```text
20260626-191137
```

The first two attempted runs failed closed before any Discord mutation:

1. `surface_not_allowed` — test payload used an invalid command surface string.
2. `orchestrator_failed` — test payload used invalid priority `normal`; schema accepts `P0`/`P1` style priorities.

Both failures reported `projection_status=not_attempted`, so no Discord message was posted during those failed attempts. The corrected run used:

- surface: `hermes_existing_gateway`
- priority: `P1`
- controlled live projection with the existing Phase 24 channel allowlist resolver

Published messages:

| Profile | Bot | Channel | Message ID | Result |
|---|---|---|---|---|
| `aicompanyassistant` | `비서` | `#일일-브리핑` | `1520008626205360208` | PASS |
| `aicompanyceo` | `대표` | `#전략-회의실` | `1520008629351088129` | PASS |
| `aicompanycontent` | `콘텐츠팀장` | `#콘텐츠-메인` | `1520008632832622622` | PASS |
| `aicompanyart` | `아트팀장` | `#아트-메인` | `1520008635806122004` | PASS |
| `aicompanytech` | `기술팀장` | `#기술-메인` | `1520008639069425806` | PASS |
| `aicompanymarketing` | `마케팅팀장` | `#마케팅-메인` | `1520008642273742938` | PASS |
| `aicompanyquality` | `품질관리팀장` | `#전체-리뷰` | `1520008645461414044` | PASS |

Runtime artifacts, ignored by git:

```text
runtime/phase29-live/20260626-191137/phase29_live_controlled_projection_summary.json
runtime/phase29-live/20260626-191137/phase29_readiness_simulation.json
```

## Phase 29 readiness simulation

Result: READY

Blockers: none

Applied constraints:

- max runs/hour: 10
- allowed window: 09:00-23:00
- allowed channels: 7 profile-local home channels
- mention gated: true
- checkpoint interval: 60 seconds
- budget cap: $100
- hourly spend max: $10
- quota alert thresholds: Go 80%, Codex 80%

## Verification

Related test suite:

```text
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py \
  tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py -q

56 passed
```

Lint:

```text
ruff check src/runtime_architecture_v2 \
  tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py \
  tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py

No issues found
```

Full test suite after regression fix:

```text
5664 passed
```

Regression fixed:

```text
TestArtifactTypeReExport.test_re_exported_from_writer
- root cause: reader doc/test expected ArtifactType re-export, but reader did not import it from writer
- fix: re-export writer ArtifactType from src.gdrive_artifact_reader and declare it in __all__
```

Quota after initial bounded live test:

```text
Go:    Monthly 35%, Weekly 73%, Hourly 0%
Codex: Monthly 0%,  Weekly 58%, Hourly 13%
Both available
```

Quota after regression fix and full-suite verification:

```text
Go:    Monthly 35%, Weekly 73%, Hourly 0%
Codex: Monthly 0%,  Weekly 60%, Hourly 28%
Both available
```

## Verdict

The bounded live test is PASS.

The full test suite is now green (`5664 passed`). The system is safe to proceed to a longer supervised pilot under the Phase 29 bounds before any unattended 24h operation.
