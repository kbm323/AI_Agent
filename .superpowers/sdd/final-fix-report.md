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
