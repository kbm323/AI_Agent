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
- Format-safe fixture/report follow-up: `bb4ec9e701a7f7a1dc8450f8a819e7f0928b5bf2..c8e5f68bf137490822bcc199440da6934466a3e0`
- Whole reviewed branch after fixes: `c7d52c7fc6c3bb19ef048e16acd659a717dd6218..c8e5f68bf137490822bcc199440da6934466a3e0`
- Implementation commit: `bb4ec9e701a7f7a1dc8450f8a819e7f0928b5bf2` (`fix: close discord save final review findings`)
- Fixture/report commit: `c8e5f68bf137490822bcc199440da6934466a3e0` (`test: keep secret scan fixture format-safe`)

The final report-accuracy update is the commit containing this revision; its
resolved hash is recorded in the final response to avoid self-referential commit
content.

## Changed Files

- `.superpowers/sdd/final-fix-report.md`
- `docs/operations/discord-save-slash-command.md`
- `hermes_plugins/ai-agent-commands/__init__.py`
- `scripts/pre-commit-secret-scan.sh`
- `src/runtime_architecture_v2/discord_conversation.py`
- `src/runtime_architecture_v2/discord_history.py`
- `src/runtime_architecture_v2/hermes_command_context.py`
- `src/runtime_architecture_v2/knowledge.py`
- `src/runtime_architecture_v2/obsidian_conversations.py`
- `src/runtime_architecture_v2/save_command.py`
- `tests/test_discord_save_operational_guards.py`
- `tests/test_runtime_architecture_v2_ai_agent_plugin.py`
- `tests/test_runtime_architecture_v2_conversation_summary.py`
- `tests/test_runtime_architecture_v2_discord_history.py`
- `tests/test_runtime_architecture_v2_hermes_command_context.py`
- `tests/test_runtime_architecture_v2_obsidian_conversations.py`
- `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`
- `tests/test_runtime_architecture_v2_save_command.py`

## Finding Root Causes And Solutions

| Finding | Root cause | Implemented solution |
| --- | --- | --- |
| URL credentials | Assignment redaction did not parse URL authority, so plain or encoded userinfo survived into LLM and storage paths. | Centralized URL-aware sanitization removes userinfo for arbitrary schemes, repeatedly decodes encoded/nested URLs, redacts secret parameters, and is used at Discord ingestion, host-LLM input, checkpoints, summaries, and Obsidian writes. |
| Invocation snapshot | History began at delayed tool execution and had no immutable upper bound. | `HERMES_SESSION_MESSAGE_ID` is read as the official verified invocation cutoff. The supported pre-dispatch hook freezes a raw interaction/message ID when available or a conservative dispatch-time snowflake fallback; every page and checkpoint uses an exclusive cutoff. |
| DM contract | Every non-thread context returned thread-only guidance and no reliable session-start boundary was modeled. | Discord source type is classified. An explicit supported start ID can bound `(start, cutoff)`, but pinned Hermes supplies none and the production reader does not consume the unsupported `HERMES_SESSION_START_MESSAGE_ID`; deployed DMs return `dm_boundary_unavailable` before summary, checkpoint, or storage side effects. |
| Retry/resume | Any 429/5xx discarded in-memory pagination progress and restarted from the newest page. | Added bounded Retry-After/5xx/transport retries and atomic sanitized checkpoints keyed by source/cutoff, with cursor validation, deduplication, restart resume, and the 10,000-message ceiling. |
| Secret gate | Deployment required a clean index, then ran a staged-only scanner that inspected zero files. | Preserved staged mode and added non-vacuous `--tree`/`--range` committed-content modes; the runbook scans `REVIEWED_BASE..AI_AGENT_COMMIT`. |
| Rollback | Disk state changed without stopping or resynchronizing the already-loaded assistant gateway. | Snapshot prior profile state, stop the assistant, disable/remove feature state, restore config, restart to force native sync, and require tool plus picker absence evidence. |
| Clean checkout | Tracked diff checks allowed non-ignored untracked executable/importable files. | Reject all non-ignored untracked files before commit pin/install and restrict ignored runtime allowance to non-code data. |
| Responses | Success omitted title and fixed failures lacked actionable retry/remediation text. | Added sanitized document title and deterministic guidance for command and plugin setup failures. |
| Private visibility | Discord type 12 persisted the noncanonical `private_thread` value. | Normalize private thread and DM visibility to `private`, with `channel_kind` kept separately. |

## Implemented Decisions

1. URL-aware sanitization is centralized in `knowledge.py`. It removes URL
   userinfo, including percent-encoded and repeatedly encoded forms, sanitizes
   nested redirect URLs and secret query keys, and remains the single path used
   before the host LLM, collection checkpoints, raw snapshots, canonical pages,
   and LLM-returned summaries.
2. The context reader binds Hermes' official `HERMES_SESSION_MESSAGE_ID` as the
   verified invocation cutoff. The plugin also registers the official
   `pre_gateway_dispatch` hook to freeze a raw Discord slash interaction/message
   ID before model/tool delay when available. If neither exact ID is present, it
   creates an exclusive Discord snowflake cutoff from gateway turn-start time. A
   `ContextVar` carries that immutable boundary into the history API.
   Precise fallback limitation: without an exact raw Discord ID, the synthetic
   timestamp-floor snowflake can conservatively omit messages from the same
   millisecond that preceded dispatch; it cannot include messages posted after
   the hook froze the boundary.
3. Installed Hermes revision `1d689e19203281228878ac6770d4a6700d4ae385`
   was inspected read-only. The implementation uses only its supported session
   context, hook, skill, and plugin surfaces; no Hermes Core edit or standalone
   interaction adapter was introduced.
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

The initial affected-suite RED run produced `61 failed, 99 passed`. The first
partial implementation run narrowed this to `5 failed, 143 passed`. The
unsupported DM-start environment test produced `1 failed, 2 passed`; plugin retry
guidance produced `11 failed, 13 deselected`; the standalone repeatedly encoded
URL probe produced `1 failed, 12 deselected`; and operational guards initially
produced `4 failed, 3 passed`. Each was followed by its focused GREEN run before
the final aggregate.

## Final Verification

All final Python runs used `PYTHONUTF8=1` and the worktree `.venv` interpreter.

Focused aggregate, including the historical 162-test selection and save skill:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py -q
```

Result: `190 passed in 10.40s`.

Operational scanner/runbook suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_discord_save_operational_guards.py -q
```

Result: `8 passed in 6.09s`. This executes staged, tree, committed-range,
non-vacuous-range, clean-gate, boundary-documentation, and rollback assertions;
the shell cases ran through Git Bash.

Required regression selection:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 11.32s`. The failures exactly match both the
dispatch report and baseline:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`

The identical command at untouched baseline
`c7d52c7fc6c3bb19ef048e16acd659a717dd6218`, using the feature worktree
interpreter, produced `99 passed, 3 failed in 10.78s` with the same test names and
assertion causes. This is direct baseline evidence, not an inference from the
prior review.

Changed-wave Ruff and format:

```powershell
$files = git diff --name-only --diff-filter=ACM cef6846..HEAD -- '*.py'
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
```

Result: `All checks passed!`; `15 files already formatted`.

Secret and diff gates:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --range 'c7d52c7fc6c3bb19ef048e16acd659a717dd6218..c8e5f68bf137490822bcc199440da6934466a3e0'
git diff --check cef68467fbdddf4eaea67a0d1bfa47fd39403261..c8e5f68bf137490822bcc199440da6934466a3e0
git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..c8e5f68bf137490822bcc199440da6934466a3e0
```

Results: committed range scan passed with `28 file(s) inspected`; both diff
checks passed. The operational suite separately proves empty/default staged,
explicit staged, tree, secret-blocking range, and vacuous-range behavior.

## Remaining Concerns

- The three regression failures above remain baseline fixture/profile-token
  issues and must be rerun with the required Ubuntu profile environment.
- The pinned Hermes revision cannot safely save DMs because it has no exact DM
  start-message boundary. This is visible, dedicated fail-closed behavior.
- Ubuntu `npm run typecheck`, `npm run lint:ruff`, seven-profile install/hash
  proof, gateway restarts, native picker smoke, and rollback smoke remain
  deployment gates. They were not run because this wave explicitly forbids
  deployment.

---

## Second Final-Review Fix Wave

### Status

`DONE_WITH_CONCERNS`

All three Important and both Minor findings in `final-rereview.md` are fixed.
The implementation commit is
`9de53350e25be0178198449fe2a0e46b24e5d7e2` (`fix: close discord save second
rereview findings`). The report commit is the commit containing this section;
its resolved hash is returned with the final status because a commit cannot
contain its own hash. No Hermes Core files or standalone Discord adapter were
added or modified.

### Changed Files

- `docs/operations/discord-save-slash-command.md`
- `hermes_plugins/ai-agent-commands/__init__.py`
- `scripts/pre-commit-secret-scan.sh`
- `src/runtime_architecture_v2/discord_conversation.py`
- `src/runtime_architecture_v2/discord_history.py`
- `src/runtime_architecture_v2/hermes_command_context.py`
- `src/runtime_architecture_v2/obsidian_conversations.py`
- `src/runtime_architecture_v2/save_command.py`
- `tests/test_discord_save_operational_guards.py`
- `tests/test_runtime_architecture_v2_ai_agent_plugin.py`
- `tests/test_runtime_architecture_v2_discord_history.py`
- `tests/test_runtime_architecture_v2_hermes_command_context.py`
- `tests/test_runtime_architecture_v2_save_command.py`
- `.superpowers/sdd/final-fix-report.md` (this report commit only)

### Root Causes And Solutions

| Finding | Root cause | Implemented solution |
| --- | --- | --- |
| Seven-profile lifecycle | Deployment used a start helper that skipped live tmux sessions; only the assistant's prior state, reload, rollback resync, and absence evidence were tracked. | Record `was-running`/`was-stopped` for every profile. Start only the assistant for the first smoke, then deliberately stop/start each previously running non-assistant profile. Rollback stops every profile marked as having loaded the candidate before restoring disk/config state, starts all seven against restored state for per-bot tool and picker absence evidence, and finally restores each profile's prior running/stopped state. |
| Checkpoint lifecycle | Collection files were named by source plus invocation cutoff, so a later native `/save` could not find earlier progress; completed full transcripts were never retired. | Keep one versioned checkpoint per source and explicit DM start boundary, with the immutable cutoff in the payload. A newer cutoff adopts compatible progress, fetches the newer interval first, deduplicates, retains the newest messages under the 10,000 cap, and preserves the DM lower boundary. First-wave cutoff-named files are selected by deepest compatible progress, atomically migrated, and retired. Created, updated, and unchanged durable saves delete compatible collection state. |
| Snowflake boundary | A complete raw interaction snowflake was used as `before`, so a later same-millisecond message from another generator could have a numerically lower ID and pass the filter. | Normalize both official boundary sources, raw gateway interaction/message IDs and `HERMES_SESSION_MESSAGE_ID`, to the exclusive timestamp-floor snowflake by clearing the lower 22 bits. The reversed-lower-bit test proves same-millisecond later messages remain excluded. |
| Explicit DM persistence | The collector represented DMs with empty guild/parent IDs, while the store unconditionally required both as numeric thread containers; the prior positive test mocked the store. | Add `DiscordSourceIdentity` with explicit `thread` and `dm` variants. The collector emits `private_dm(channel_id)` without invented guild/parent IDs; the store validates the private DM invariant and persists source kind/ID through raw and canonical documents. A real collector, summarizer, and Obsidian store test passes. Pinned Hermes remains fail closed because it still has no reliable DM start boundary. |
| Rename secret scan | Staged and range filename selection used `--diff-filter=ACM`, excluding rename destinations. | Use `--diff-filter=ACMR` in both modes and verify a lightly modified, Git-classified rename containing a credential assignment is blocked in staged and committed-range scans. |

### TDD Evidence

The first RED run stopped during collection with the expected
`ImportError: cannot import name 'DiscordSourceIdentity'`. After the initial
implementation, the affected selection produced `3 failed, 103 passed`; the
three failures were the unchanged legacy conversation equality and the two
still-unimplemented seven-profile runbook assertions. The first-wave checkpoint
migration test was then added and failed `1 failed, 34 deselected` by requesting
`before=200` instead of adopting the legacy file. Each RED was followed by a
focused GREEN run; the final direct selection is recorded below.

### Verification

All Python commands used `PYTHONUTF8=1` and the worktree `.venv` interpreter.

Directly affected implementation, persistence, plugin, boundary, and operations
tests:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_discord_save_operational_guards.py -q
```

Result: `156 passed in 17.60s`.

Operational lifecycle and secret-scanner guards alone:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_discord_save_operational_guards.py -q
```

Result: `11 passed in 9.44s`.

Updated aggregate focused suite (the previous 198-test aggregate plus ten new
cases):

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py tests\test_discord_save_operational_guards.py -q
```

Result: `208 passed in 18.92s`.

Required regression selection:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 12.60s`. The failures are exactly the three
recorded in the first-wave report's untouched-baseline run (`99 passed, 3
failed in 10.78s`), with the same test names and causes:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
  returns `live_discord_publish_blocked`.
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
  returns `live_discord_publish_blocked`.
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`
  reports the missing `aicompanyceo` profile Discord token.

Changed-wave Ruff lint and format:

```powershell
$files = git diff --name-only --diff-filter=ACMR a0ea2afac98236b4e6eb5fc7cc5785ebb59fa368..9de53350e25be0178198449fe2a0e46b24e5d7e2 -- '*.py'
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
```

Results: `All checks passed!`; `11 files already formatted`.

Non-vacuous committed-range secret scan:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --range 'a0ea2afac98236b4e6eb5fc7cc5785ebb59fa368..9de53350e25be0178198449fe2a0e46b24e5d7e2'
```

Result: `Secret scan passed: 13 file(s) inspected in --range mode.` The
operational suite separately proves both staged and range modes inspect rename
destinations and reject the renamed secret fixture.

Diff and worktree gates:

```powershell
git diff --check a0ea2afac98236b4e6eb5fc7cc5785ebb59fa368..9de53350e25be0178198449fe2a0e46b24e5d7e2
git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..9de53350e25be0178198449fe2a0e46b24e5d7e2
git status --short
```

Results: both diff checks passed with no output; the worktree was clean before
this report was appended.

### Remaining Concerns

- The three required-regression failures remain baseline-identical profile
  token/fixture issues and require the documented Ubuntu profile environment.
- Pinned Hermes `1d689e19203281228878ac6770d4a6700d4ae385` still has no reliable
  Discord DM session-start message boundary. Production DM `/save` therefore
  intentionally returns `dm_boundary_unavailable` without collection,
  summarization, or storage side effects.
- Ubuntu static checks, real seven-profile install/hash proof, assistant-first
  smoke, six-profile reload, native picker/tool checks, and live rollback smoke
  remain deployment gates. This wave changed and tested the runbook but did not
  perform a deployment.

---

## Third Final-Review Fix Wave

### Status

`DONE_WITH_CONCERNS`

All three blocking Important findings in the overwritten `final-rereview.md`
are fixed. The product/tests/runbook commit is
`dbef4922d8c92e04088543084f954e78010e0fda` (`fix: close discord save third
rereview findings`). The report commit is the commit containing this section;
its resolved hash is returned in the final status because a commit cannot
contain its own hash. No Hermes Core file or standalone Discord adapter was
added or modified.

### Changed Files

- `docs/operations/discord-save-slash-command.md`
- `scripts/rollback_discord_save_profiles.sh`
- `src/runtime_architecture_v2/discord_history.py`
- `src/runtime_architecture_v2/save_command.py`
- `tests/test_discord_save_operational_guards.py`
- `tests/test_runtime_architecture_v2_discord_history.py`
- `tests/test_runtime_architecture_v2_save_command.py`
- `.superpowers/sdd/final-fix-report.md` (this report commit only)

### Root Causes And Solutions

| Finding | Root cause | Implemented solution |
| --- | --- | --- |
| Early/partial rollback | Rollback stopped only the assistant and profiles with `loaded-by-deployment`; untouched live sessions then collided with unconditional restored-state `tmux new-session` calls and aborted the rollback under `set -e`. | Added a tested two-phase rollback helper. `prepare` idempotently stops all seven session names without consulting loaded markers, restores every profile's disk/config/plugin/skill state, and starts all seven against restored state for resynchronization. `finalize` requires tool and picker absence evidence for all seven, then alone uses `was-running`/`was-stopped` to restore final state. Immediate pre-start kills also make resync collision-safe. |
| Near-cap adoption | Inherited entries populated the shared message dictionary, so their count could satisfy `max_messages` after only one newer page and produce a transcript with a missing middle interval. | Checkpoint version 3 persists the active adopted cutoff, cursor, and completion state. Newer entries are counted independently by the adopted cutoff; inherited entries cannot complete collection until the bridge is reached. The bounded payload evicts oldest inherited entries as newer pages arrive. If the newer interval reaches 10,000 by itself, inherited state is fully displaced and the newest contiguous 10,000 are returned. |
| Same-source concurrency | Atomic JSON replacement prevented torn files but did not serialize load/write/persist/delete across profile processes; one invocation could overwrite or delete another generation. | Added a source plus DM-boundary lifecycle lock combining a process-local lock with the existing bounded interprocess file-lock pattern. `run_save_command` acquires it before collection and releases it only after summary, durable Obsidian persistence, and conditional cleanup (or failure). Different sources use different resolved lock paths. Threaded and spawned-process tests prove a successful invocation cannot delete a later failed invocation's resumable generation. |

### TDD Evidence

- Near/full-cap RED: `2 failed, 35 deselected`; both tests observed only one
  newer page instead of three and 100 pages respectively.
- Thread concurrency RED: `1 failed, 34 deselected`; the second invocation
  reached Discord while the first was blocked in persistence.
- Multiprocess concurrency RED: `1 failed, 34 deselected`; the spawned second
  process likewise collected before the first completed.
- Rollback RED: `2 failed, 11 deselected`; both executable state scenarios
  failed because the rollback helper did not yet exist.

Each RED was followed by its focused GREEN run before the combined verification
below.

### Verification

All Python commands used `PYTHONUTF8=1` and the worktree `.venv` interpreter.

Directly affected collector, orchestration, persistence, plugin/context, and
operational tests:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_discord_save_operational_guards.py -q
```

Result: `162 passed in 45.87s`.

Executable rollback and operational guards alone:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_discord_save_operational_guards.py -q
```

Result: `13 passed in 18.69s`. This includes both early-failure shell-state
scenarios, all-seven resynchronization and absence evidence, collision checks,
and final prior-state restoration.

Updated aggregate focused suite:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py tests\test_discord_save_operational_guards.py -q
```

Result: `214 passed in 49.23s`.

Required regression selection:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 11.66s`. The failures exactly match the
untouched-baseline evidence already recorded in this report:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
  returns `live_discord_publish_blocked`.
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
  returns `live_discord_publish_blocked`.
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`
  reports the missing `aicompanyceo` profile Discord token.

Shell syntax, Ruff, and format:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/rollback_discord_save_profiles.sh
$files = git diff --name-only --diff-filter=ACMR be95fce82fb5302be19481784f2760e23ae80cb2..dbef4922d8c92e04088543084f954e78010e0fda -- '*.py'
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
```

Results: shell syntax passed with no output; `All checks passed!`; `5 files
already formatted`.

Non-vacuous committed-range secret scan:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --range 'be95fce82fb5302be19481784f2760e23ae80cb2..dbef4922d8c92e04088543084f954e78010e0fda'
```

Result: `Secret scan passed: 7 file(s) inspected in --range mode.`

Diff and clean-worktree gates:

```powershell
git diff --check be95fce82fb5302be19481784f2760e23ae80cb2..dbef4922d8c92e04088543084f954e78010e0fda
git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..dbef4922d8c92e04088543084f954e78010e0fda
git status --short
```

Results: both diff checks passed with no output; the worktree was clean before
this report was appended.

### Remaining Concerns

- The three required-regression failures remain baseline-identical profile
  token/fixture issues and require the documented Ubuntu profile environment.
- Pinned Hermes still has no reliable Discord DM session-start message
  boundary, so production DM `/save` intentionally remains fail closed with no
  collection, summary, or persistence side effect.
- Ubuntu static checks, real seven-profile install/hash proof, assistant-first
  smoke, six-profile reload, native picker/tool checks, and live rollback smoke
  remain deployment gates. The executable rollback state machine is tested but
  no deployment was performed in this wave.

---

## Fourth Final-Review Fix Wave

### Status

`DONE_WITH_CONCERNS`

All three blocking Important findings in the independent fourth rereview are
fixed. The product/tests commit is
`4400eda551540695485a09b2a5c2dce83f075a7e` (`fix: close discord save fourth
rereview findings`). The report commit is the commit containing this section;
its resolved hash is returned in the final status because a commit cannot
contain its own hash. No Hermes Core file or standalone Discord adapter was
added or modified.

### Changed Files

- `src/runtime_architecture_v2/discord_history.py`
- `src/runtime_architecture_v2/knowledge.py`
- `src/runtime_architecture_v2/save_command.py`
- `tests/test_runtime_architecture_v2_conversation_summary.py`
- `tests/test_runtime_architecture_v2_discord_history.py`
- `tests/test_runtime_architecture_v2_obsidian_conversations.py`
- `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`
- `tests/test_runtime_architecture_v2_save_command.py`
- `.superpowers/sdd/final-fix-report.md` (this report commit only)

### Root Causes And Solutions

| Finding | Root cause | Implemented solution |
| --- | --- | --- |
| Version-3 partial adoption restart | Loader cursor validation compared an active newer-interval cursor with the minimum across both active and inherited messages. A later cutoff also flattened an unfinished active adoption into a new generation, losing the state needed to bridge the middle interval. | Validate active and inherited intervals against their own cursors, with complete-generation cursors treated as historical. A later cutoff first resumes and durably finishes the persisted active generation under its stored cutoff, then adopts that complete generation into the requested cutoff. Same-cutoff, later-cutoff, chained restart, near-cap, and full-cap tests assert exact contiguous ranges with no middle gap. |
| Quoted and credential-style secrets | The shared assignment regex required an unquoted value and omitted `credential`/`auth` keys. Escaped JSON newlines also made a following secret key look embedded in a word. | Expanded only the central sanitizer to cover quoted keys and values, JSON-style assignments, credential/authentication/authorization keys, and secret keys after escaped whitespace while preserving existing URL sanitization and non-secret fields. Exact host-LLM input, failure checkpoint, immutable raw snapshot, and canonical page tests cover the new forms. |
| Cancellation while acquiring lifecycle lock | Cancelling `await asyncio.to_thread(lock.acquire)` stopped the coroutine but not the worker; the worker could later acquire local and interprocess ownership with no remaining release path. | Added an acquisition ownership handoff guarded by a thread mutex. Cancellation marks ownership abandoned before a late worker can hand it off; the worker then releases immediately. If acquisition wins the race, cancellation schedules release. Deterministic tests prove late-acquired release and successful same-source reuse; existing threaded and spawned-process serialization tests remain green. |

### TDD Evidence

- Initial blocker RED: `8 failed in 4.14s`. The failures were the exact quoted
  sanitizer/LLM/checkpoint/raw/canonical leaks, both
  `invalid_collection_checkpoint` restart paths, and both abandoned-lock
  cancellation assertions.
- First blocker GREEN: `8 passed in 0.47s` after the three minimal product
  changes.
- Directly affected module run then exposed two compatibility issues:
  `151 passed, 3 failed in 42.67s`; one was escaped-JSON assignment handling,
  one was the expected acquisition-wrapper assertion update, and one was a
  Windows subprocess code-page failure. The two actionable cases plus the CLI
  rerun with `PYTHONUTF8=1` passed `4 passed in 0.64s`.
- Directly affected modules then passed `154 passed in 43.06s`.
- Final review strengthened the later-cutoff test with a second failure after
  finishing the inherited generation. It failed `1 failed in 0.58s` at the
  inherited complete-cursor check, then passed `1 passed in 0.51s` after the
  completion-aware validation fix.
- Final blocker selection: `8 passed in 0.69s`.

### Verification

All final Python commands used `PYTHONUTF8=1` and the worktree `.venv`
interpreter.

Final updated aggregate, including all directly affected modules and
operational guards:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py tests\test_discord_save_operational_guards.py -q
```

Result: `222 passed in 38.60s`.

Required regression selection:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 10.25s`. The failures exactly match the
untouched-baseline evidence already recorded in this report:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
  returns `live_discord_publish_blocked`.
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
  returns `live_discord_publish_blocked`.
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`
  reports the missing `aicompanyceo` profile Discord token.

Whole-wave Ruff and format:

```powershell
.\.venv\Scripts\ruff.exe check <the 8 changed Python files>
.\.venv\Scripts\ruff.exe format --check <the 8 changed Python files>
```

Results: `All checks passed!`; `8 files already formatted`. No shell file was
changed in this wave, so no shell syntax command applied.

Secret and diff gates:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --staged
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --range 'f2ec8b294c5158ef6bf005a0dc42b3539fc6f3fd..4400eda551540695485a09b2a5c2dce83f075a7e'
git diff --check f2ec8b294c5158ef6bf005a0dc42b3539fc6f3fd..4400eda551540695485a09b2a5c2dce83f075a7e
git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..4400eda551540695485a09b2a5c2dce83f075a7e
```

Results: staged scan passed with `8 file(s) inspected`; the non-vacuous
committed-range scan passed with `8 file(s) inspected`; both diff checks passed
with no output. The worktree was clean at the product commit before this report
section was appended.

### Remaining Concerns

- The three required-regression failures remain baseline-identical profile
  token/fixture issues and require the documented Ubuntu profile environment.
- Pinned Hermes still has no reliable Discord DM session-start message
  boundary, so production DM `/save` intentionally remains fail closed with no
  collection, summarization, or persistence side effect.
- Ubuntu static checks, real seven-profile install/hash proof, assistant-first
  smoke, six-profile reload, native picker/tool checks, and live rollback smoke
  remain deployment gates. No deployment was performed in this wave.

---

## Fifth Final-Review Fix Wave

### Status

`DONE_WITH_CONCERNS`

The final two Important findings are fixed in commit `5f891c6` (`fix: settle
save workers and redact yaml secrets`). No Hermes Core file or standalone
Discord adapter was added or modified.

### Root Causes And Solutions

| Finding | Root cause | Implemented solution |
| --- | --- | --- |
| Cancellation after lifecycle-lock acquisition | Cancelling a coroutine awaiting `asyncio.to_thread` released the source lifecycle lock while the synchronous worker could continue, allowing a later same-source save to overlap. | Added `_settled_to_thread`, which shields each started worker, waits for it to settle after cancellation, then propagates cancellation before the lifecycle lock is released. Collection, lookup, persistence, checkpoint cleanup, and lock release use the helper. |
| YAML secret scalar suffix leaks | The central sanitizer did not consume complete valid YAML scalars such as doubled single quotes, multiword plain values, or block scalars with chomping/indent indicators. | Added a bounded line-oriented YAML secret-assignment sanitizer that replaces complete scalar assignments and indented block-scalar bodies before the existing token and mention passes. |

### Verification

- Directly affected modules: `160 passed in 27.72s` with `PYTHONUTF8=1`.
- Updated aggregate: `228 passed in 42.20s`.
- Required regression selection: `99 passed, 3 failed in 10.28s`; the three
  failures exactly match the previously recorded local profile-token fixture
  failures.
- Ruff: `All checks passed!`.
- Format: `7 files already formatted`.
- Staged secret scan: `Secret scan passed: 7 file(s) inspected in staged mode`.
- `git diff --check`: passed with no output.

### Remaining Concerns

- The three required-regression failures still require the documented Ubuntu
  profile environment.
- Production DM `/save` remains intentionally fail closed because pinned Hermes
  does not expose a reliable Discord DM session-start boundary.
- Final independent review, branch integration, GitHub push, Ubuntu gates,
  seven-profile install, assistant-first smoke, remaining profile reload, and
  live deployment verification remain pending.

---

## Sixth Final-Review Fix Wave

### Status

`DONE_WITH_CONCERNS`

Both Important findings and the whole-range formatting finding in
`final-rereview-after-fifth.md` are closed. The product/tests/format commit is
`8ad8941fbd32246569f97570af0ecbd549c2fbe5` (`fix: close discord save sixth
rereview findings`). The report commit is the commit containing this section;
its resolved hash is returned in the final status because a commit cannot
contain its own hash. No Hermes Core file or standalone Discord adapter was
added or modified.

### Changed Files

- `scripts/sync_discord_bot_identities.py`
- `src/runtime_architecture_v2/knowledge.py`
- `src/runtime_architecture_v2/obsidian_conversations.py`
- `tests/test_runtime_architecture_v2_conversation_summary.py`
- `tests/test_runtime_architecture_v2_discord_history.py`
- `tests/test_runtime_architecture_v2_obsidian_conversations.py`
- `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`
- `tests/test_runtime_architecture_v2_store.py`
- `.superpowers/sdd/final-fix-report.md` (this report commit only)

Ruff was also run directly on `src/runtime_architecture_v2/store.py`, the third
reported formatter offender. Its working-tree line endings normalized and the
format check passed, but Git produced no content delta for that file. The other
two reported offenders have the expected minimal formatter-only diffs.

### Root Causes And Solutions

| Finding | Root cause | Implemented solution |
| --- | --- | --- |
| Namespaced, quoted-key, and flow/plain secret leaks | The central assignment patterns encoded only complete bare secret key names. The YAML line scanner did not parse quoted keys, and the fallback token regex stopped unquoted values at the first space. | Added one central key parser/normalizer that classifies exact secret components in namespaced keys, a quoted-or-bare YAML assignment scanner that consumes complete block bodies, and a bounded inline scanner that consumes complete plain flow scalars up to structural delimiters while preserving adjacent safe fields. |
| Same-range conversation-to-meeting transition | Immutable evidence validation recomputed the snapshot hash with the current mutable classification and `MeetingRun`, even though the raw snapshot correctly retained the classification and meeting identity recorded when it was created. | Reconstruct immutable evidence hashes from the snapshot's own validated `type`, `meeting_run_id`, and stored thread name. The unchanged path can now rewrite canonical/index meeting linkage without creating or rewriting raw evidence, while same-range transcript mutation still mismatches the immutable evidence hash and fails closed. |
| Whole-range Ruff format | Three branch files were not accepted by the current Ruff formatter, primarily because of mixed working-tree line endings plus one quote normalization. | Ran the repository Ruff formatter only on the three reported files and the two newly edited files that required formatting; the final whole-range check reports all 22 changed Python files formatted. |

### TDD Evidence

The focused RED command was:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase15_knowledge_loop.py::test_public_sanitizer_redacts_namespaced_quoted_and_flow_yaml_secrets tests\test_runtime_architecture_v2_conversation_summary.py::test_hermes_summarizer_redacts_namespaced_and_flow_yaml_from_exact_input tests\test_runtime_architecture_v2_discord_history.py::test_failed_page_checkpoint_redacts_namespaced_and_flow_yaml_secrets tests\test_runtime_architecture_v2_obsidian_conversations.py::test_namespaced_quoted_and_flow_yaml_secrets_are_redacted_in_all_pages tests\test_runtime_architecture_v2_obsidian_conversations.py::test_same_latest_conversation_can_acquire_meeting_linkage_without_new_snapshot tests\test_runtime_architecture_v2_obsidian_conversations.py::test_same_latest_meeting_transition_rejects_transcript_mutation -q
```

Result: `5 failed, 1 passed in 0.73s`. The five failures were the central
sanitizer, exact host-LLM input, failed checkpoint, raw/canonical pages, and
positive meeting-link transition. The negative transcript-mutation case already
failed closed, as required.

After the minimal product changes, the same selection passed
`6 passed in 0.61s`. Its fresh final rerun after formatting passed
`6 passed in 0.37s`. The four directly affected sanitizer/persistence modules
then passed `127 passed in 23.83s`.

### Verification

All Python commands used `PYTHONUTF8=1` and the worktree `.venv` interpreter.

Updated aggregate command from this report:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py tests\test_discord_save_operational_guards.py -q
```

Result: `234 passed in 41.22s`.

Required regression selection:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 10.85s`. The failures exactly match the known
local profile-token baseline:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
  returns `live_discord_publish_blocked`.
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
  returns `live_discord_publish_blocked`.
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`
  reports the missing `aicompanyceo` profile Discord token.

Whole-range Ruff lint and format after the product commit:

```powershell
$files = @(git diff --name-only --diff-filter=ACMR c7d52c7fc6c3bb19ef048e16acd659a717dd6218..HEAD -- '*.py')
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
```

Results: `Python files: 22`; `All checks passed!`; `22 files already formatted`.

Staged secret and diff gates before the product commit:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --staged
git diff --cached --check
```

Results: `Secret scan passed: 8 file(s) inspected in staged mode`; the staged
diff check passed with no output. The post-commit whole-branch command
`git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..HEAD`
also passed with no output, and the worktree was clean before this report was
appended.

### Remaining Concerns

- The three required-regression failures remain baseline-identical profile
  token/fixture issues and require the documented Ubuntu profile environment.
- Pinned Hermes still has no reliable Discord DM session-start boundary, so
  production DM `/save` intentionally remains fail closed without collection,
  summarization, or persistence side effects.
- Ubuntu static checks, real seven-profile install/hash proof, assistant-first
  smoke, remaining profile reloads, native picker/tool checks, rollback smoke,
  branch integration, and live deployment verification remain pending. No
  deployment was performed in this wave.

---

## Seventh Final-Review Fix Wave

### Status

`DONE_WITH_CONCERNS`

The remaining Important classifier defect and Minor over-redaction finding in
`final-rereview-after-sixth.md` are fixed in product/tests commit
`972651d8320bbb73e7a7fdcd92447b43ea5eed40` (`fix: harden discord save
credential classification`). The report commit is the commit containing this
section; its resolved hash is returned in the final status because a commit
cannot contain its own hash. No Hermes Core file, adapter, or downstream sink
implementation was changed.

### Changed Files

- `src/runtime_architecture_v2/knowledge.py`
- `tests/test_runtime_architecture_v2_conversation_summary.py`
- `tests/test_runtime_architecture_v2_discord_history.py`
- `tests/test_runtime_architecture_v2_obsidian_conversations.py`
- `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`
- `.superpowers/sdd/final-fix-report.md` (this report commit only)

### Root Cause And Solution

The sixth-wave classifier split keys only on non-alphanumeric separators and
treated any matching component as secret. CamelCase credentials therefore had
no recognized component, `private_key` was absent from the credential corpus,
and safe names such as `token_count`, `authorization_url`, `auth_method`, and
`password_policy` were removed because one component happened to be sensitive.
The unquoted `=` parser also treated whitespace as the end of the value, leaking
the remaining words.

The central sanitizer now inserts camelCase boundaries before normalization and
uses exact credential names plus directional credential suffixes. It recognizes
`private_key`/`privateKey`, `secret_access_key`/`secretAccessKey`,
`access_token`/`accessToken`, `client_secret`/`clientSecret`, API keys,
namespaced credentials, and the earlier snake/dotted/hyphenated forms without
classifying metadata/url suffixes as secrets. Unquoted `=` values consume all
words until a structural delimiter or a following assignment, mention, or
protected URL boundary. Adjacent safe assignments and URLs remain verbatim.

### TDD Evidence

The focused RED command was:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase15_knowledge_loop.py::test_public_sanitizer_pairs_secret_keys_with_safe_metadata tests\test_runtime_architecture_v2_conversation_summary.py::test_hermes_summarizer_redacts_google_private_key_from_exact_input tests\test_runtime_architecture_v2_discord_history.py::test_failed_page_checkpoint_redacts_complete_camel_case_equals_value tests\test_runtime_architecture_v2_obsidian_conversations.py::test_camel_case_secrets_are_redacted_without_losing_safe_page_fields -q
```

Result: `10 failed in 0.66s`. All seven paired positive/negative classifier rows
failed, as did the exact host-LLM Google service-account fixture, failed
checkpoint, and raw/canonical page regressions. The fixture uses a realistic but
fake service-account JSON document with a fake PEM-shaped `private_key`, safe
project/key IDs, service email, and OAuth URLs.

After the minimal central revision, the same selection passed
`10 passed in 0.58s`. The first four-module compatibility run then produced
`136 passed, 1 failed in 26.36s`: the existing bare-URL test showed that a URL
following the final unquoted secret assignment was being consumed as part of
the multiword value. Adding the already-protected internal URL placeholder as a
value boundary made the strengthened compatibility selection pass
`11 passed in 0.27s`. The final directly affected module run passed
`137 passed in 25.04s`.

### Verification

All Python commands used `PYTHONUTF8=1` and the worktree `.venv` interpreter.

Updated aggregate:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_hermes_command_context.py tests\test_runtime_architecture_v2_discord_history.py tests\test_runtime_architecture_v2_conversation_summary.py tests\test_runtime_architecture_v2_obsidian_conversations.py tests\test_runtime_architecture_v2_save_command.py tests\test_runtime_architecture_v2_ai_agent_plugin.py tests\test_runtime_architecture_v2_store.py tests\test_runtime_architecture_v2_phase15_knowledge_loop.py tests\test_runtime_architecture_v2_phase25_command_surface.py tests\test_runtime_architecture_v2_save_skill.py tests\test_discord_save_operational_guards.py -q
```

Result: `244 passed in 43.80s`.

Required regression selection:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_architecture_v2_phase14_multi_bot.py tests\test_runtime_architecture_v2_phase21_discord_webhook.py tests\test_runtime_architecture_v2_phase30_meeting_e2e.py tests\test_runtime_architecture_v2_phase32_live_audit.py tests\test_runtime_architecture_v2_on_demand_exports.py tests\test_runtime_smoke_packet.py -q
```

Result: `99 passed, 3 failed in 11.24s`. The failures exactly match the known
local profile-token baseline:

- `test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages`
  returns `live_discord_publish_blocked`.
- `test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake`
  returns `live_discord_publish_blocked`.
- `test_gateway_provider_error_falls_back_to_deterministic_live_projection`
  reports the missing `aicompanyceo` profile Discord token.

Whole-range Ruff lint and format after the product commit:

```powershell
$files = @(git diff --name-only --diff-filter=ACMR c7d52c7fc6c3bb19ef048e16acd659a717dd6218..HEAD -- '*.py')
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
```

Results: `Python files: 22`; `All checks passed!`; `22 files already formatted`.

Staged secret and range diff gates:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/pre-commit-secret-scan.sh --staged
git diff --cached --check
git diff --check c7d52c7fc6c3bb19ef048e16acd659a717dd6218..HEAD
```

Results: `Secret scan passed: 5 file(s) inspected in staged mode`; both diff
checks passed with no output. The worktree was clean before this report was
appended.

### Remaining Concerns

- The three required-regression failures remain baseline-identical profile
  token/fixture issues and require the documented Ubuntu profile environment.
- Pinned Hermes still has no reliable Discord DM session-start boundary, so
  production DM `/save` intentionally remains fail closed without collection,
  summarization, or persistence side effects.
- Ubuntu static checks, real seven-profile install/hash proof, assistant-first
  smoke, remaining profile reloads, native picker/tool checks, rollback smoke,
  branch integration, and live deployment verification remain pending. No
  deployment was performed in this wave.
