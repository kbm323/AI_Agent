# Discord `/save` Final Fix Report

## Status

`DONE_WITH_CONCERNS`

All final-review blockers and both minor findings are implemented and committed.
The concerns are the three known baseline regression failures, the verified
fail-closed DM limitation in the pinned Hermes revision, and the Ubuntu/live
deployment gates that were intentionally not run. No deployment was performed.

## Commit Ranges

- Reviewed feature dispatch: `c7d52c7fc6c3bb19ef048e16acd659a717dd6218..cef68467fbdddf4eaea67a0d1bfa47fd39403261`
- Final fix implementation: `cef68467fbdddf4eaea67a0d1bfa47fd39403261..bb4ec9e701a7f7a1dc8450f8a819e7f0928b5bf2`
- Whole reviewed branch after fixes: `c7d52c7fc6c3bb19ef048e16acd659a717dd6218..bb4ec9e701a7f7a1dc8450f8a819e7f0928b5bf2`
- Implementation commit: `bb4ec9e701a7f7a1dc8450f8a819e7f0928b5bf2` (`fix: close discord save final review findings`)

This report is committed separately after the implementation commit; the final
response records that documentation commit and the resulting final `HEAD`.

## Implemented Decisions

1. URL-aware sanitization is centralized in `knowledge.py`. It removes URL
   userinfo, including percent-encoded and repeatedly encoded forms, sanitizes
   nested redirect URLs and secret query keys, and remains the single path used
   before the host LLM, collection checkpoints, raw snapshots, canonical pages,
   and LLM-returned summaries.
2. The plugin registers Hermes' official `pre_gateway_dispatch` hook. It freezes
   the raw Discord slash interaction ID before model/tool delay. If no exact
   interaction or message ID exists, it creates an exclusive Discord snowflake
   cutoff from the verified gateway turn-start time. A `ContextVar` carries the
   immutable boundary through Hermes' official tool worker into the history API.
3. Installed Hermes revision `1d689e19203281228878ac6770d4a6700d4ae385`
   was inspected read-only. Its native slash builder keeps the raw interaction
   but does not populate `MessageEvent.message_id`, so
   `HERMES_SESSION_MESSAGE_ID` is not an exact slash boundary. The hook is the
   earliest supported boundary available in this revision.
4. The installed revision exposes session `created_at` but no exact Discord DM
   start-message snowflake and no supported `HERMES_SESSION_START_MESSAGE_ID`.
   Converting `created_at` would guess across dispatch latency, so it is not used.
   Current official-path DM saves return `dm_boundary_unavailable`. The internal
   available branch is tested with an explicit verified start snowflake and saves
   only `(session_start, invocation_cutoff)` with `visibility="private"`.
5. Discord history retries 429 using bounded Retry-After and transient 5xx with
   bounded exponential backoff. Sanitized atomic checkpoints are keyed by
   source/cutoff, preserve incomplete pages across process restarts, stop at the
   10,000-message cap or DM start boundary, and deduplicate on resume.
6. Secret scanning retains empty staged pre-commit behavior and adds non-vacuous
   `--tree` and `--range` modes over committed content. The runbook scans the
   reviewed base through `AI_AGENT_COMMIT`, rejects non-ignored untracked files
   before pin/install, confines ignored output to non-code runtime paths, and runs
   identity sync only after that gate.
7. Rollback records prior profile config/plugin/skill state, stops the assistant,
   removes the feature, restores prior config, restarts to force native command
   resynchronization, and requires live `/tools` and picker absence evidence before
   success. No Hermes Core or standalone adapter changes were made.
8. Success results include the sanitized document title. Every command failure
   code and plugin setup failure has deterministic retry/remediation guidance.
   Private thread and DM visibility is normalized to `private`; channel kind is a
   separate transport field.

## TDD Evidence

Initial RED command over the seven affected Python suites produced
`50 failed, 100 passed in 8.93s`. The expanded plugin/history/command RED run
produced `58 failed, 27 passed`. Operational guard tests first produced
`4 failed, 3 skipped`. The DM boundary-crossing and double-encoded URL tests first
produced `2 failed`. A fresh source-only aggregate later caught a standalone fully
encoded URL case as `1 failed, 189 passed`; the central sanitizer was corrected
before the final GREEN run.

## Final Verification

All final Python runs used `PYTHONUTF8=1` and a fresh
`PYTHONPYCACHEPREFIX` so stale plugin bytecode could not mask source behavior.

Focused aggregate, including the historical 162-test selection and save skill:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPYCACHEPREFIX="$env:TEMP\codex-discord-save-final-aggregate-2"
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py -q
```

Result: `190 passed in 10.40s`.

Operational scanner/runbook suite:

```powershell
$env:PYTHONPYCACHEPREFIX="$env:TEMP\codex-discord-save-final-ops"
.\.venv\Scripts\python.exe -m pytest tests\test_discord_save_operational_guards.py -q
```

Result: `8 passed in 5.71s`. This executes staged, tree, committed-range,
non-vacuous-range, clean-gate, boundary-documentation, and rollback assertions;
the shell cases ran through Git Bash.

Required regression selection:

```powershell
$env:PYTHONPYCACHEPREFIX="$env:TEMP\codex-discord-save-final-regressions"
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 11.69s`. The failures exactly match both the
dispatch report and baseline:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`

Changed-wave Ruff and format:

```powershell
$files = git diff --name-only --diff-filter=ACM -- '*.py'
.\.venv\Scripts\ruff.exe format $files
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
```

Result: `All checks passed!`; `14 files already formatted` after formatting.

Secret and diff gates:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --staged
git diff --cached --check
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --range 'c7d52c7fc6c3bb19ef048e16acd659a717dd6218..bb4ec9e'
git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..bb4ec9e
```

Results: staged scan passed with `17 file(s) inspected`; committed range scan
passed with `27 file(s) inspected`; both diff checks passed.

## Remaining Concerns

- The three regression failures above remain baseline fixture/profile-token
  issues and must be rerun with the required Ubuntu profile environment.
- The pinned Hermes revision cannot safely save DMs because it has no exact DM
  start-message boundary. This is visible, dedicated fail-closed behavior.
- Ubuntu `npm run typecheck`, `npm run lint:ruff`, seven-profile install/hash
  proof, gateway restarts, native picker smoke, and rollback smoke remain
  deployment gates. They were not run because this wave explicitly forbids
  deployment.
