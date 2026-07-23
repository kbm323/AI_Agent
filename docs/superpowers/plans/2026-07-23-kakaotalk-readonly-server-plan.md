# KakaoTalk Read-Only Server Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run a server-resident, read-only KakaoTalk collector on demand for an explicit chat allowlist, then route the requested messages to Hermes for Notion scheduling and Obsidian knowledge capture.

**Architecture:** Run KakaoTalk inside an ARM64-compatible Android container based on redroid only when collection is requested. Iris or the selected Android bridge reads the requested chat room, a small read-only command boundary filters allowed chat rooms and resumes from that room's durable collection cursor, and the bridge forwards only unseen sanitized message records to the existing Runtime v2 ingestion boundary. No background listener, persistent message stream, outbound KakaoTalk tool, reply endpoint, send capability, or automatic response is installed.

**Discord Interaction:** Start collection from `/kakao-collect`, then present the
10 most recently active rooms that are also configured in the server-side
allowlist through Hermes's interactive Discord UI. Hermes plugin command
handlers return direct strings and do not expose the adapter-internal Select
component used by `/model`, so use a Hermes skill command and the documented
`clarify` button flow without forking Hermes core. The selected room and
collection request must still pass the server-side allowlist and cursor checks;
Discord UI selection is not an authorization boundary by itself.

**Tech Stack:** Ubuntu 24.04 ARM64, Docker, redroid, Android KakaoTalk APK, Iris, Python Runtime v2, Hermes Gateway, Notion integration, existing Obsidian vault.

## Global Constraints

- Only explicitly allowlisted KakaoTalk chat rooms may be collected.
- Collection happens only after an explicit user request; there is no real-time or background collection.
- The first request for each room requires an explicit initial baseline; later requests resume after that room's last durable cursor.
- Cursor advancement occurs only after raw persistence succeeds, preventing data loss on interrupted collection.
- KakaoTalk sending, replying, editing, deleting, reacting, and automatic response are prohibited.
- Kakao credentials, session files, access tokens, and message bodies must not be printed in logs or deployment evidence.
- Notion receives extracted schedules and tasks; Obsidian receives raw chat records and durable analysis.
- Existing Runtime v2 architecture and existing documents remain canonical; do not create a parallel runtime.
- Deployment must be reversible and must not change existing Discord bot tokens or commands.
- The Kakao collection command must not expose free-form room names as the primary path; room selection is interactive and limited to the 10 most recently active allowlisted rooms.
- Discord interaction timeouts and abandoned selections must not start collection or advance a cursor.

---

### Task 1: Verify Server Capability

**Files:**
- Create: `docs/operations/kakaotalk-readonly-server.md`
- Test: server diagnostics, no repository test change

- [ ] Check `uname -m`, Ubuntu release, Docker availability, kernel Binder support, `/dev/binder*`, disk, memory, and required privileged-container capability.
- [ ] Record only pass/fail and non-sensitive versions in the operations document.
- [ ] Stop with a documented blocker if ARM64 redroid or Binder support is unavailable; do not install Wine or an emulator fallback automatically.

### Task 2: Define the On-Demand Read Boundary

**Files:**
- Create: `src/runtime_architecture_v2/kakaotalk_readonly.py`
- Create: `tests/test_runtime_architecture_v2_kakaotalk_readonly.py`

- [ ] Add a typed collection request containing `chat_name`, an optional initial baseline, and a request ID; later requests use the stored room cursor.
- [ ] Inspect Hermes Discord command/plugin interfaces and identify whether a custom Select menu can be created from a command/skill; document the verified API and version-specific limitation.
- [ ] Implement the Discord flow as `/kakao-collect` followed by a Select menu containing exactly the 10 most recently active allowlisted rooms; use Hermes `clarify` buttons as the supported fallback. Do not paginate because the display set is fixed at 10.
- [ ] Define and test the recent-room ordering key, tie-breaker, and behavior when fewer than 10 allowlisted rooms are available.
- [ ] Ensure expired, cancelled, or unauthorized interactions produce no collection request and no cursor mutation.
- [ ] Add a typed inbound record containing `event_id`, `chat_name`, `sender`, `message`, `sent_at`, and source metadata.
- [ ] Reject requests whose normalized chat name is not in the configured allowlist.
- [ ] Reject requests without an explicit room, first-run baseline, or cursor, outbound-shaped requests, and any request containing send/reply operation names.
- [ ] Deduplicate records by stable event ID, with a deterministic fallback hash for records without an ID.
- [ ] Write failing tests for allowlist rejection, missing baseline/cursor, cursor resume, cursor non-advancement on failure, duplicate suppression, malformed input, and explicit send/reply rejection.
- [ ] Implement the minimal adapter and pass the focused tests.

### Task 3: Add Durable Read-Only Ingestion

**Files:**
- Modify: existing Runtime v2 raw chat ingestion/store module selected after Task 1 repository inspection
- Test: focused Runtime v2 ingestion tests

- [ ] Persist accepted messages under the existing `raw/chat` convention without changing the canonical vault layout.
- [ ] Preserve source event ID, room name, sender, timestamp, and ingestion status.
- [ ] Store one durable cursor per allowlisted chat room and advance it only after the complete batch is persisted.
- [ ] Make retries idempotent and prevent duplicate Notion or Obsidian writes.
- [ ] Keep message content out of operational logs.

### Task 4: Deploy On-Demand redroid and KakaoTalk Without Write Integration

**Files:**
- Create: `scripts/start_kakaotalk_readonly.sh`
- Create: `scripts/status_kakaotalk_readonly.sh`
- Modify: `docs/operations/kakaotalk-readonly-server.md`

- [ ] Pin the tested redroid image and ARM64 KakaoTalk APK version after capability validation.
- [ ] Run redroid with only the device and storage permissions required for an on-demand Android container.
- [ ] Complete first-time KakaoTalk login through a temporary remote screen; never place credentials in scripts or environment files committed to Git.
- [ ] Install Iris and verify a requested read using one allowlisted test room.
- [ ] Do not expose or invoke Iris reply/send endpoints or keep a background event subscription.
- [ ] Add status checks for container health, Android device availability, Iris health, and read-only request completion.

### Task 5: Connect Hermes, Notion, and Obsidian

**Files:**
- Modify: existing Hermes/runtime integration modules identified during Task 3
- Test: Runtime v2 integration tests
- Modify: existing canonical architecture and operations documents

- [ ] Forward only records returned by an explicit accepted collection request to Hermes.
- [ ] Report the selected room, collected count, cursor status, and destination paths back to Discord without exposing message bodies or secrets.
- [ ] Extract candidate schedules and tasks for Notion confirmation flow.
- [ ] Store raw records and analysis in the existing Obsidian structure.
- [ ] Ensure no tool registry, prompt, skill, or bridge exposes KakaoTalk send/reply operations.
- [ ] Stop the Android container and bridge after the requested collection completes, unless another approved collection is active.

### Task 6: Verify On-Demand Collection and Read-Only Safety

**Files:**
- Modify: `docs/operations/kakaotalk-readonly-server.md`
- Test: focused tests and controlled server smoke checks

- [ ] Run the focused Python suite and Runtime v2 regression suite.
- [ ] Request an initial read from the allowlisted room and verify the cursor is created.
- [ ] Request collection again and verify only messages after the saved cursor are returned.
- [ ] Force a persistence failure and verify the cursor does not advance.
- [ ] Verify repeating the same request is idempotent and creates no duplicate raw records or Notion candidates.
- [ ] Verify a non-allowlisted room is ignored.
- [ ] Verify no send/reply tool or endpoint is available.
- [ ] Stop and start the redroid/Iris/bridge stack and verify a later explicit request works without background collection.
- [ ] Record final evidence without message bodies, credentials, or tokens.
