# Discord Meeting Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Hermes-native `/meeting-start` and `/meeting-report` commands that reuse Runtime Architecture v2, persist Discord thread linkage, and remain manually archived through `/archive`.

**Architecture:** A new transport-neutral `meeting_commands.py` service validates Hermes command context and calls the existing `gateway_bridge` and `on_demand_exports` boundaries. The Hermes plugin remains a thin async adapter and runs the blocking live meeting path in `asyncio.to_thread`. Runtime v2 stores `discord_thread_id` on the final `MeetingRun`, allowing reports and `/archive` to resolve the current thread without keyword or participant heuristics.

**Tech Stack:** Python 3.12, pytest 9, pytest-asyncio, Hermes plugin API, Runtime Architecture v2, Discord REST projection.

## Global Constraints

- Keep Hermes Core unchanged; modify only AI_Agent code and its user plugin.
- Use `/meeting-start <natural language>` and `/meeting-report <optional natural language>` as top-level commands with one free-form argument.
- Company meetings use the six company profiles; the assistant remains outside the visible meeting participant set.
- A successful meeting start must persist `thread_id -> meeting_run_id` in `MeetingRun` metadata.
- `/meeting-report` must resolve the current Discord thread and never guess the latest meeting.
- Meeting reports do not write to Obsidian; `/archive` remains the only conversation and meeting archive command.
- Live failures return stable Korean messages without tokens, filesystem paths, provider payloads, or exception text.
- Follow RED-GREEN-REFACTOR and commit each independently testable task.

---

### Task 1: Stabilize the Gateway Baseline and Persist Thread Linkage

**Files:**
- Modify: `tests/test_runtime_smoke_packet.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `src/runtime_architecture_v2/gateway_bridge.py`

**Interfaces:**
- Produces: `run_meeting_from_gateway(..., require_meeting_intent: bool = True)`.
- Produces: completed `MeetingRun.metadata["discord_thread_id"]` when a meeting thread exists.

- [ ] **Step 1: Make the existing live-fallback test independent of real profile files**

Add a `_build_profile_env` monkeypatch returning a sentinel token inside `test_gateway_provider_error_falls_back_to_deterministic_live_projection`.

- [ ] **Step 2: Verify the baseline test passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_smoke_packet.py::test_gateway_provider_error_falls_back_to_deterministic_live_projection -q
```

Expected: `1 passed` without reading `~/.hermes/profiles`.

- [ ] **Step 3: Write failing tests for explicit slash-command intent and thread linkage**

Add tests proving:

```python
result = run_meeting_from_gateway(
    GatewayMeetingTrigger(text="신제품 아이디어", user_id="u1", channel_id="c1"),
    root=tmp_root,
    live_discord=False,
    create_thread=False,
    require_meeting_intent=False,
)
assert result.success is True
```

and that a completed pilot with `target_thread_id="thread-123"` reloads as:

```python
stored = MeetingRunStore(tmp_path).find_by_discord_thread_id("thread-123")
assert stored is not None
assert stored.metadata["discord_thread_id"] == "thread-123"
```

- [ ] **Step 4: Run the new tests and confirm RED**

Expected failures: unknown `require_meeting_intent` argument and missing `discord_thread_id` metadata.

- [ ] **Step 5: Implement minimal gateway and persistence changes**

In `gateway_bridge.py`, guard intent classification only when `require_meeting_intent` is true. In `multi_bot.py`, include the resolved thread ID in the final immutable replacement before the final store write:

```python
run = replace(
    run,
    projection_event_ids=projection_ids,
    metadata={
        **run.metadata,
        **({"discord_thread_id": meeting_thread_id} if meeting_thread_id else {}),
    },
)
```

- [ ] **Step 6: Run focused gateway, multi-bot, and store tests**

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_architecture_v2/gateway_bridge.py src/runtime_architecture_v2/multi_bot.py tests/test_runtime_smoke_packet.py tests/test_runtime_architecture_v2_phase14_multi_bot.py
git commit -m "fix: persist live meeting thread linkage"
```

---

### Task 2: Add the Transport-Neutral Meeting Command Service

**Files:**
- Create: `src/runtime_architecture_v2/meeting_commands.py`
- Create: `tests/test_runtime_architecture_v2_meeting_commands.py`

**Interfaces:**
- Consumes: `HermesCommandContext`, `GatewayMeetingTrigger`, `run_meeting_from_gateway`, `MeetingRunStore`, `run_on_demand_export`.
- Produces: `MeetingCommandResult(ok, status, message, meeting_run_id, thread_id)`.
- Produces: `run_meeting_start(request, *, context, root, gateway_runner=...)`.
- Produces: `run_meeting_report(request, *, context, root, exporter=...)`.

- [ ] **Step 1: Write failing start-command tests**

Cover blank input, non-Discord context, missing channel, new-channel thread creation, current-thread reuse, intent bypass, sanitized failure, and successful response containing only the thread mention and MeetingRun ID.

- [ ] **Step 2: Run start-command tests and confirm RED**

Expected: import failure because `meeting_commands.py` does not exist.

- [ ] **Step 3: Implement the minimal start service**

Construct `GatewayMeetingTrigger` from `HermesCommandContext`. Use `context.thread_id or context.chat_id` as the current Discord surface; pass `create_thread=not bool(context.thread_id)` and `require_meeting_intent=False`. Convert every gateway failure to a stable status and Korean response.

- [ ] **Step 4: Run start-command tests and confirm GREEN**

Expected: all start tests pass.

- [ ] **Step 5: Write failing report-command tests**

Cover non-thread rejection, unlinked thread rejection, blank default final report, `브리핑/요약/summary` to `SUMMARY`, `합의/결론/agreement` to `AGREEMENT`, `할 일/액션/todo/action` to `ACTION_ITEMS`, arbitrary emphasis to `FINAL_REPORT`, exporter failure sanitization, and no Obsidian/QMD calls.

- [ ] **Step 6: Run report-command tests and confirm RED**

Expected: missing report implementation or wrong export classification.

- [ ] **Step 7: Implement report resolution and natural-language classification**

Resolve only with `MeetingRunStore(root).find_by_discord_thread_id(context.thread_id)`. Call `run_on_demand_export` using the selected `OnDemandExportType`; return the exporter content unchanged except for bounded Discord length and a short MeetingRun header.

- [ ] **Step 8: Run the complete command-service tests**

Expected: all tests pass with no network or profile access.

- [ ] **Step 9: Commit**

```bash
git add src/runtime_architecture_v2/meeting_commands.py tests/test_runtime_architecture_v2_meeting_commands.py
git commit -m "feat: add Runtime v2 meeting command service"
```

---

### Task 3: Register Hermes `/meeting-start` and `/meeting-report`

**Files:**
- Modify: `hermes_plugins/ai-agent-commands/__init__.py`
- Modify: `hermes_plugins/ai-agent-commands/plugin.yaml`
- Modify: `tests/test_runtime_architecture_v2_ai_agent_plugin.py`

**Interfaces:**
- Consumes: `run_meeting_start` and `run_meeting_report` from Task 2.
- Produces: Hermes command registrations `meeting-start` and `meeting-report`.
- Produces: plugin version `0.3.0`.

- [ ] **Step 1: Write failing plugin registration tests**

Assert the command set contains all six user commands and exact argument hints:

```python
assert ctx.commands["meeting-start"]["args_hint"] == "회의 주제"
assert ctx.commands["meeting-report"]["args_hint"] == "선택: 보고 요청"
```

Add handler tests that inject a fake `meeting_commands` module, verify the current Hermes context is passed, and prove the live start call runs through `asyncio.to_thread` rather than blocking the event loop directly.

- [ ] **Step 2: Run plugin tests and confirm RED**

Expected: both command names are absent and plugin version remains `0.2.0`.

- [ ] **Step 3: Implement thin async handlers**

Each handler resolves `_runtime_paths()`, imports `read_hermes_command_context`, fills a missing profile from `ctx.profile_name`, and calls the service through `await asyncio.to_thread(...)`. Return stable Korean environment and failure messages.

- [ ] **Step 4: Register both commands and bump the manifest**

Register:

```python
ctx.register_command("meeting-start", handler=execute_meeting_start, ...)
ctx.register_command("meeting-report", handler=execute_meeting_report, ...)
```

Set `plugin.yaml` version to `0.3.0`.

- [ ] **Step 5: Run plugin and command-service tests**

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add hermes_plugins/ai-agent-commands tests/test_runtime_architecture_v2_ai_agent_plugin.py
git commit -m "feat: expose meeting commands through Hermes"
```

---

### Task 4: Update Operational Guards and Existing Documentation

**Files:**
- Modify: `tests/test_discord_save_operational_guards.py`
- Modify: `docs/operations/discord-save-slash-command.md`
- Modify: `docs/superpowers/specs/2026-07-13-discord-slash-commands-obsidian-design.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: plugin version `0.3.0` and six-command manifest.
- Produces: assistant-first rollout and rollback instructions for both meeting commands.

- [ ] **Step 1: Write failing operational guard assertions**

Require `/meeting-start`, `/meeting-report`, plugin version `0.3.0`, thread-link verification, assistant-first smoke, and seven-profile hash equality in the runbook.

- [ ] **Step 2: Run operational guard tests and confirm RED**

Expected: runbook does not yet mention the new rollout gates.

- [ ] **Step 3: Update existing documents in place**

Add the implemented status and exact smoke sequence without creating a parallel runbook. Record that content-level live meetings remain supervised and that `/archive` remains manual.

- [ ] **Step 4: Run operational guards and documentation checks**

Expected: all operational tests pass and `git diff --check` exits zero.

- [ ] **Step 5: Commit**

```bash
git add tests/test_discord_save_operational_guards.py docs/operations/discord-save-slash-command.md docs/superpowers/specs/2026-07-13-discord-slash-commands-obsidian-design.md .superpowers/sdd/progress.md
git commit -m "docs: add meeting command rollout gates"
```

---

### Task 5: Regression Verification, Integration, and Seven-Profile Rollout

**Files:**
- Verify only: all changed files and server profile installations.

**Interfaces:**
- Consumes: reviewed plugin `0.3.0` from Tasks 1-4.
- Produces: merged main commit and seven live Discord applications exposing all six commands.

- [ ] **Step 1: Run focused Python tests**

Run command-service, plugin, gateway, multi-bot, store, on-demand export, save, and operational guard tests. Expected: all pass.

- [ ] **Step 2: Run broad Runtime v2 regression and Ruff**

Run `pytest tests/test_runtime_architecture_v2_*.py -q`, changed-file Ruff, `git diff --check`, and staged secret scanning. Record known unrelated failures separately; do not hide them.

- [ ] **Step 3: Merge the reviewed feature branch into main and push**

Use a non-interactive merge, verify `main...origin/main` is even, and fast-forward the aiagent checkout.

- [ ] **Step 4: Deploy assistant first**

Install the official GitHub subdirectory, enable with `--no-allow-tool-override`, compare `plugin.yaml` and `__init__.py` hashes, restart only the assistant gateway, wait for Discord safe reconciliation, and verify all six commands through Discord API without printing tokens.

- [ ] **Step 5: Deploy the remaining six profiles**

Repeat the same hash-verified installation and sequential gateway restarts. Verify every application exposes `/archive`, both `/meeting-*`, and all three `/llmwiki-*` commands.

- [ ] **Step 6: Run a bounded registration smoke**

Verify blank `/meeting-start` rejection and outside-thread `/meeting-report` rejection without starting provider work. Defer the first content-level live meeting to supervised operation as already decided.

- [ ] **Step 7: Record final deployment evidence**

Update the existing progress document with commit, plugin version, command count, profile count, and verification result; commit and push the evidence update.

---

## Trustworthy Meeting Hardening Extension

Tasks 1-5 describe the command rollout already completed on 2026-07-22. The
following tasks extend that implementation in the priority order defined by
`docs/runtime-architecture-v2.md` section 4.3. Existing command names, bot
tokens, role IDs, profile mappings, and manual `/archive` behavior remain
unchanged.

### Task 6: Persist Canonical Meeting Sessions and Outcomes

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `src/runtime_architecture_v2/schemas.py`
- Modify: `src/runtime_architecture_v2/store.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_store.py`

**Interfaces:**
- Produces: `MultiBotSession.from_dict(data) -> MultiBotSession`.
- Produces: `MeetingOutcome` with status, evidence, agreements, disagreements,
  actions, validator metadata, and backward-compatible serialization.
- Produces: `MeetingRunStore.save_meeting_session`, `load_meeting_session`,
  `save_meeting_outcome`, and `load_meeting_outcome`.

- [ ] **Step 1: Write failing store round-trip tests**

Create a two-round `MultiBotSession` and a `MeetingOutcome`, save them, reload
them, and assert exact equality. Assert paths are:

```python
run_dir / "meeting_session.json"
run_dir / "meeting_outcome.json"
```

Also load a legacy `BotMessage` dictionary without evidence fields and assert
`generation_status == "replacement"`.

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_store.py tests/test_runtime_architecture_v2_phase14_multi_bot.py -q
```

Expected: missing outcome type, session loader, and store methods.

- [ ] **Step 3: Add backward-compatible message and outcome schemas**

Extend `BotMessage` with defaulted `generation_status`, `provider`, `model`, and
`error_code` fields. Add `MultiBotSession.from_dict`. Add a frozen
`MeetingOutcome` schema with strict allowed statuses and `to_dict/from_dict`.

- [ ] **Step 4: Add guarded store methods**

Reuse `MeetingRunStore._atomic_write_json` and existing meeting ID validation.
Reject a session or outcome whose `meeting_run_id` differs from the requested
directory.

- [ ] **Step 5: Run focused tests and commit**

Expected: focused tests pass and `git diff --check` exits zero.

```bash
git add src/runtime_architecture_v2/multi_bot.py src/runtime_architecture_v2/schemas.py src/runtime_architecture_v2/store.py tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_store.py
git commit -m "feat: persist canonical meeting evidence"
```

### Task 7: Record Generation Evidence and Persist Every Round

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Interfaces:**
- Produces: internal `BotGenerationResult(content, generation_status, provider,
  model, error_code)`.
- Extends: `run_meeting_phase(..., on_round_completed=None)`.

- [ ] **Step 1: Write failing generation-evidence tests**

Use an injected runner that succeeds for one role and fails for another. Assert
successful messages are `live`, deterministic text is `replacement`, failures
carry a sanitized category, and no raw exception text is serialized.

- [ ] **Step 2: Write a failing per-round persistence test**

Inject a callback and assert it receives a one-round session before round two,
then a two-round session. In the pilot integration test, reload
`meeting_session.json` and assert twelve ordered visible messages for six roles.

- [ ] **Step 3: Run the new tests and confirm RED**

Expected: content generation returns plain strings and no session file exists.

- [ ] **Step 4: Implement structured generation and callback persistence**

Convert provider results to `BotGenerationResult`. Never label deterministic
text as live. Call `on_round_completed` immediately after each immutable session
snapshot is built. The pilot passes `MeetingRunStore.save_meeting_session`.

- [ ] **Step 5: Run focused tests and commit**

```bash
git add src/runtime_architecture_v2/multi_bot.py tests/test_runtime_architecture_v2_phase14_multi_bot.py
git commit -m "feat: record durable meeting transcript evidence"
```

### Task 8: Replace Participant-Count Consensus with Structured Validation

**Files:**
- Create: `src/runtime_architecture_v2/meeting_outcome.py`
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Create: `tests/test_runtime_architecture_v2_meeting_outcome.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Interfaces:**
- Produces: `evaluate_meeting_outcome(session, *, command_runner, workdir) -> MeetingOutcome`.
- Consumes: persisted `MultiBotSession` and the `validation_audit` model policy.

- [ ] **Step 1: Write failing outcome parser tests**

Cover `agreed`, `partial_agreement`, `blocked`, malformed JSON, unknown status,
missing evidence, and provider failure. Assert malformed or failed synthesis
returns `needs_user_decision` with no fabricated agreements or actions.

- [ ] **Step 2: Write failing response-coverage tests**

Assert `agreed` requires twelve live statements. Assert `partial_agreement`
requires at least four live roles in both rounds including `validation_audit`.
Lower coverage must override provider JSON to `needs_user_decision`.

- [ ] **Step 3: Run outcome tests and confirm RED**

Expected: module import failure.

- [ ] **Step 4: Implement strict structured evaluation**

Build one transcript prompt, request JSON from the validation model, validate
all evidence references against stored `round:<number>:<role>` identifiers, and
apply response-coverage gates after parsing. Sanitize all error codes.

- [ ] **Step 5: Integrate outcome persistence**

Run evaluation only after the two-round session is stored. Save
`meeting_outcome.json`, set compatibility fields `consensus_reached` and
`escalation_required` from the outcome status, and surface degraded outcomes in
the Gateway summary.

- [ ] **Step 6: Run focused tests and commit**

```bash
git add src/runtime_architecture_v2/meeting_outcome.py src/runtime_architecture_v2/multi_bot.py tests/test_runtime_architecture_v2_meeting_outcome.py tests/test_runtime_architecture_v2_phase14_multi_bot.py
git commit -m "feat: validate meeting outcomes from transcript evidence"
```

### Task 9: Generate Reports from Canonical Meeting Evidence

**Files:**
- Modify: `src/runtime_architecture_v2/on_demand_exports.py`
- Modify: `src/runtime_architecture_v2/meeting_commands.py`
- Modify: `tests/test_runtime_architecture_v2_on_demand_exports.py`
- Modify: `tests/test_runtime_architecture_v2_meeting_commands.py`

**Interfaces:**
- Changes: all export types load canonical session and outcome artifacts.
- Produces: `reports/<export_type>.md` for every successful export.

- [x] **Step 1: Write failing evidence-based report tests**

Persist unique phrases in round messages and structured action items in the
outcome. Assert summary, agreement, action, and final reports contain those
phrases and do not contain the old generic action text. Assert legacy meetings
display an explicit reduced-evidence notice.

- [x] **Step 2: Write a failing long-report delivery test**

Assert the compact Discord response ends at a complete section boundary,
identifies the `MeetingRun`, and leaves the complete Markdown in
`reports/final_report.md` instead of adding `...` after 1,900 characters.

- [x] **Step 3: Run report tests and confirm RED**

Expected: empty-session reconstruction, generic actions, and character slicing
violate the assertions.

- [x] **Step 4: Rebuild exports from stored artifacts**

Remove `_reconstruct_session`. Load the canonical session and outcome, render
evidence citations by role and round, write reports atomically, and use only
verifiable worker artifacts for disclosed legacy fallback.

- [x] **Step 5: Replace character slicing with compact rendering**

Return a bounded summary containing outcome status, summary, disagreements,
next actions, and full artifact location. Never cut a Markdown section or
fabricate omitted content.

- [x] **Step 6: Run focused tests and commit**

Implemented on 2026-07-22. Canonical export and command tests passed (34),
and the wider meeting/outcome/store/report compatibility suite passed (93).

```bash
git add src/runtime_architecture_v2/on_demand_exports.py src/runtime_architecture_v2/meeting_commands.py tests/test_runtime_architecture_v2_on_demand_exports.py tests/test_runtime_architecture_v2_meeting_commands.py
git commit -m "fix: ground meeting reports in canonical evidence"
```

### Task 10: Preserve Discord Provenance, Routing, and Idempotency

**Files:**
- Modify: `src/runtime_architecture_v2/gateway_bridge.py`
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `src/runtime_architecture_v2/meeting_commands.py`
- Modify: `src/runtime_architecture_v2/hermes_command_context.py`
- Modify: `src/runtime_architecture_v2/store.py`
- Modify: `hermes_plugins/ai-agent-commands/__init__.py`
- Modify: `tests/test_runtime_smoke_packet.py`
- Modify: `tests/test_runtime_architecture_v2_meeting_commands.py`

**Interfaces:**
- Extends: `GatewayMeetingTrigger` with `invocation_id`.
- Extends: the pilot entry point with real trigger provenance.
- Produces: store lookup and reservation by invocation or 90-second fallback key.

- [x] **Step 1: Write failing provenance tests**

Start through the real Gateway boundary and assert the stored `MeetingRun`
contains the real user, guild, parent channel, thread, priority, platform, and
invocation values rather than `phase14-*` fixtures.

- [x] **Step 2: Write failing routing tests**

Assert new starts outside the CEO parent channel fail before provider work.
Assert an already-linked meeting thread resolves its stored parent channel.
Assert an unlinked thread fails closed instead of treating the thread as its own
parent.

- [x] **Step 3: Write failing duplicate-delivery tests**

Invoke the same interaction twice and assert only one MeetingRun, one thread
creation, and one provider sequence. Cover the 90-second fallback key when no
interaction ID is available.

- [x] **Step 4: Run the new tests and confirm RED**

Expected: fixture provenance, parent mismatch, and duplicate work.

- [x] **Step 5: Implement provenance and routing corrections**

Pass the trigger fields into the pilot request. Resolve linked threads through
`MeetingRunStore`; otherwise require the verified CEO parent channel. Keep
profile tokens and channel IDs unchanged.

- [x] **Step 6: Implement idempotent reservation**

Persist the invocation key before provider execution. A repeated delivery
returns the stored MeetingRun result. Never use token values or raw command text
as key material.

- [x] **Step 7: Run focused tests and commit**

Implemented on 2026-07-22. The focused Gateway, command, Hermes context,
plugin, and store suite passed (90 tests) before final cross-meeting
verification.

```bash
git add src/runtime_architecture_v2/gateway_bridge.py src/runtime_architecture_v2/multi_bot.py src/runtime_architecture_v2/meeting_commands.py src/runtime_architecture_v2/hermes_command_context.py src/runtime_architecture_v2/store.py hermes_plugins/ai-agent-commands/__init__.py tests/test_runtime_smoke_packet.py tests/test_runtime_architecture_v2_meeting_commands.py
git commit -m "fix: preserve meeting origin and prevent duplicates"
```

### Task 11: Remove Duplicate Calls and Complete Verification

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`
- Modify: `docs/operations/discord-save-slash-command.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Keeps: twelve visible-role calls for two six-role rounds.
- Keeps: selected internal specialist calls.
- Adds: one outcome evaluation and a maximum of three concurrent calls per round.

- [ ] **Step 1: Write a failing exact-call-count test**

For six live roles and no specialists, assert exactly thirteen provider calls:
twelve discussion calls plus one outcome evaluation. Assert visible-role worker
artifacts contain their stored final statements without another provider call.

- [ ] **Step 2: Write a failing bounded-concurrency test**

Instrument the runner and assert peak simultaneous calls is at most three,
round-two calls start only after every round-one call finishes, and outcome
evaluation starts only after round two is persisted.

- [ ] **Step 3: Run focused tests and confirm RED**

Expected: eighteen sequential calls.

- [ ] **Step 4: Reuse final statements and add bounded round execution**

Create visible worker outputs from round-two messages. Dispatch only internal
specialists through worker runners. Use a three-worker executor inside each
round while preserving deterministic role order in the stored session and
Discord projection.

- [ ] **Step 5: Run all meeting and Runtime v2 tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_meeting_outcome.py tests/test_runtime_architecture_v2_on_demand_exports.py tests/test_runtime_architecture_v2_meeting_commands.py tests/test_runtime_smoke_packet.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_*.py -q
.\.venv\Scripts\ruff.exe check src/runtime_architecture_v2 hermes_plugins/ai-agent-commands tests
git diff --check
```

- [ ] **Step 6: Update existing operations and progress documents**

Record the canonical artifact checks, outcome statuses, replacement disclosure,
exact call-count expectation, and bounded supervised live smoke. Do not create a
parallel runbook or rotate any Discord token.

- [ ] **Step 7: Commit verification documentation**

```bash
git add src/runtime_architecture_v2/multi_bot.py tests/test_runtime_architecture_v2_phase14_multi_bot.py docs/operations/discord-save-slash-command.md .superpowers/sdd/progress.md
git commit -m "perf: remove duplicate meeting worker calls"
```
