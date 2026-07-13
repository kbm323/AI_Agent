# Discord `/save` Slash Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real Hermes-native Discord `/save` command that saves the current thread as immutable Obsidian transcript snapshots plus one updated canonical conversation or meeting page.

**Architecture:** Package AI_Agent as a user Hermes plugin and register `/save` through `PluginContext.register_command`, allowing Hermes's existing Discord adapter to own application-command sync, authorization, defer/follow-up handling, and thread context. Runtime v2 receives a normalized Hermes command context, reads Discord history through a bounded REST client, resolves an optional `MeetingRun`, summarizes through Hermes's host-owned `ctx.llm`, and writes sanitized records to the vault configured by `OBSIDIAN_VAULT_PATH`.

**Tech Stack:** Python 3.11, Hermes Agent plugin API, Hermes `gateway.session_context`, Hermes `PluginLlm`, Discord REST API v10, Runtime Architecture v2 dataclasses, pathlib, urllib, pytest, ruff.

## Global Constraints

- The user-facing command is exactly `/save`; do not add `/save thread`, `/save conversation`, or `/save meeting`.
- Discord native threads define guild conversation boundaries. Reject guild-channel saves outside a thread.
- A persisted Discord `thread_id -> meeting_run_id` relationship, not keywords or participant count, determines `meeting` classification.
- Store all immutable transcript snapshots under `raw/chat-logs/`.
- Store one mutable canonical page per Discord thread under `wiki/conversations/`.
- Resolve the vault from `OBSIDIAN_VAULT_PATH`; deployed default is `/home/ubuntu/Obsidian`.
- Raw snapshots are immutable. Repeating `/save` with the same latest message ID creates no new snapshot.
- `/save` preserves URL and attachment metadata but does not download attachments or ingest URL contents.
- Every successful save updates `wiki/log.md`; only meetings, decisions, action items, or later promotion enter `wiki/index.md`.
- Automatic save remains disabled.
- Reuse Hermes provider/auth/session/plugin facilities. Do not modify Hermes Core or create a standalone Discord webhook.
- Do not print or persist Discord tokens, GitHub tokens, Google credentials, interaction signatures, or unredacted secrets.
- Keep Phase 25's Administrator and permission-mutation prohibitions.

---

## File Structure

Create or modify these files only for this plan:

```text
hermes_plugins/ai-agent-commands/plugin.yaml
hermes_plugins/ai-agent-commands/__init__.py
src/runtime_architecture_v2/hermes_command_context.py
src/runtime_architecture_v2/discord_conversation.py
src/runtime_architecture_v2/discord_history.py
src/runtime_architecture_v2/conversation_summary.py
src/runtime_architecture_v2/obsidian_conversations.py
src/runtime_architecture_v2/save_command.py
src/runtime_architecture_v2/store.py
src/runtime_architecture_v2/knowledge.py
src/runtime_architecture_v2/command_surface.py
scripts/sync_discord_bot_identities.py
tests/test_runtime_architecture_v2_hermes_command_context.py
tests/test_runtime_architecture_v2_discord_history.py
tests/test_runtime_architecture_v2_conversation_summary.py
tests/test_runtime_architecture_v2_obsidian_conversations.py
tests/test_runtime_architecture_v2_save_command.py
tests/test_runtime_architecture_v2_ai_agent_plugin.py
tests/test_runtime_architecture_v2_store.py
tests/test_runtime_architecture_v2_phase15_knowledge_loop.py
tests/test_runtime_architecture_v2_phase25_command_surface.py
docs/operations/discord-save-slash-command.md
```

Responsibilities:

- `hermes_command_context.py`: read Hermes task-local context without importing Hermes during ordinary unit tests.
- `discord_conversation.py`: transport-independent message, attachment, participant, context, and classification dataclasses.
- `discord_history.py`: Discord API v10 channel inspection and paginated message-history reads.
- `conversation_summary.py`: structured summary schema and Hermes LLM adapter.
- `obsidian_conversations.py`: immutable snapshots, canonical pages, checkpoints, index, and log.
- `save_command.py`: orchestration and user-facing result rendering.
- Hermes plugin: register `/save` and pass `ctx.llm`; it contains no domain logic.

---

### Task 1: Normalize Hermes Slash Command Context

**Files:**
- Create: `src/runtime_architecture_v2/hermes_command_context.py`
- Test: `tests/test_runtime_architecture_v2_hermes_command_context.py`

**Interfaces:**
- Consumes: Hermes `gateway.session_context.get_session_env(name, default)` at runtime.
- Produces: `HermesCommandContext` and `read_hermes_command_context(get_env=None)`.

- [ ] **Step 1: Write the failing context tests**

```python
from src.runtime_architecture_v2.hermes_command_context import (
    HermesCommandContext,
    read_hermes_command_context,
)


def test_reads_discord_thread_context_from_hermes_session_vars():
    values = {
        "HERMES_SESSION_PLATFORM": "discord",
        "HERMES_SESSION_CHAT_ID": "200",
        "HERMES_SESSION_CHAT_NAME": "Entertainment / #idea-thread",
        "HERMES_SESSION_THREAD_ID": "200",
        "HERMES_SESSION_USER_ID": "300",
        "HERMES_SESSION_USER_NAME": "KBM",
        "HERMES_SESSION_ID": "session-1",
        "HERMES_SESSION_PROFILE": "aicompanyassistant",
    }
    context = read_hermes_command_context(lambda key, default="": values.get(key, default))
    assert context == HermesCommandContext(
        platform="discord",
        chat_id="200",
        chat_name="Entertainment / #idea-thread",
        thread_id="200",
        user_id="300",
        user_name="KBM",
        session_id="session-1",
        profile="aicompanyassistant",
    )
    assert context.is_discord_thread is True


def test_guild_channel_without_thread_is_not_a_save_boundary():
    context = HermesCommandContext(platform="discord", chat_id="100")
    assert context.is_discord_thread is False
```

- [ ] **Step 2: Run the tests and verify the import fails**

Run: `python -m pytest tests/test_runtime_architecture_v2_hermes_command_context.py -q`  
Expected: FAIL with `ModuleNotFoundError: src.runtime_architecture_v2.hermes_command_context`.

- [ ] **Step 3: Implement the task-local context adapter**

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

GetEnv = Callable[[str, str], str]


@dataclass(frozen=True)
class HermesCommandContext:
    platform: str = ""
    chat_id: str = ""
    chat_name: str = ""
    thread_id: str = ""
    user_id: str = ""
    user_name: str = ""
    session_id: str = ""
    profile: str = ""

    @property
    def is_discord_thread(self) -> bool:
        return self.platform == "discord" and bool(self.thread_id)


def read_hermes_command_context(get_env: GetEnv | None = None) -> HermesCommandContext:
    if get_env is None:
        from gateway.session_context import get_session_env

        get_env = get_session_env
    return HermesCommandContext(
        platform=get_env("HERMES_SESSION_PLATFORM", ""),
        chat_id=get_env("HERMES_SESSION_CHAT_ID", ""),
        chat_name=get_env("HERMES_SESSION_CHAT_NAME", ""),
        thread_id=get_env("HERMES_SESSION_THREAD_ID", ""),
        user_id=get_env("HERMES_SESSION_USER_ID", ""),
        user_name=get_env("HERMES_SESSION_USER_NAME", ""),
        session_id=get_env("HERMES_SESSION_ID", ""),
        profile=get_env("HERMES_SESSION_PROFILE", ""),
    )
```

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/test_runtime_architecture_v2_hermes_command_context.py -q`  
Expected: `2 passed`.

- [ ] **Step 5: Commit the context adapter**

```bash
git add src/runtime_architecture_v2/hermes_command_context.py tests/test_runtime_architecture_v2_hermes_command_context.py
git commit -m "feat: read Hermes slash command context"
```

---

### Task 2: Model Discord Conversations and Stable Participants

**Files:**
- Create: `src/runtime_architecture_v2/discord_conversation.py`
- Create: `scripts/sync_discord_bot_identities.py`
- Test: `tests/test_runtime_architecture_v2_discord_history.py`

**Interfaces:**
- Produces: `DiscordAttachment`, `DiscordAuthor`, `DiscordMessage`, `DiscordConversation`, `BotIdentity`, `ParticipantResolver`, and `load_bot_identities(path)`.
- The deployment script writes non-secret `runtime/discord_bot_identities.json` from the seven profile tokens without printing token values.

- [ ] **Step 1: Write failing identity and serialization tests**

```python
def test_participant_resolver_uses_discord_id_before_display_name(tmp_path):
    path = tmp_path / "identities.json"
    path.write_text(
        '{"123":{"role":"콘텐츠팀장","hermes_profile":"aicompanycontent"}}',
        encoding="utf-8",
    )
    resolver = ParticipantResolver(load_bot_identities(path))
    resolved = resolver.resolve(
        DiscordAuthor(user_id="123", display_name="새 닉네임", bot=True)
    )
    assert resolved.role == "콘텐츠팀장"
    assert resolved.hermes_profile == "aicompanycontent"
    assert resolved.discord_name == "새 닉네임"


def test_unknown_human_keeps_display_name_without_company_role():
    resolved = ParticipantResolver({}).resolve(
        DiscordAuthor(user_id="999", display_name="KBM", bot=False)
    )
    assert resolved.role == ""
    assert resolved.discord_name == "KBM"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_runtime_architecture_v2_discord_history.py -q`  
Expected: FAIL because the conversation models do not exist.

- [ ] **Step 3: Implement frozen transport models and ID-first resolution**

```python
@dataclass(frozen=True)
class BotIdentity:
    role: str
    hermes_profile: str


@dataclass(frozen=True)
class DiscordAuthor:
    user_id: str
    display_name: str
    bot: bool = False


@dataclass(frozen=True)
class ParticipantIdentity:
    role: str
    hermes_profile: str
    discord_name: str
    discord_user_id: str


class ParticipantResolver:
    def __init__(self, identities: Mapping[str, BotIdentity]) -> None:
        self._identities = dict(identities)

    def resolve(self, author: DiscordAuthor) -> ParticipantIdentity:
        known = self._identities.get(author.user_id)
        return ParticipantIdentity(
            role=known.role if known else "",
            hermes_profile=known.hermes_profile if known else "",
            discord_name=author.display_name,
            discord_user_id=author.user_id,
        )
```

Add frozen attachment/message/conversation dataclasses with these exact fields:

```python
@dataclass(frozen=True)
class DiscordAttachment:
    attachment_id: str
    filename: str
    content_type: str
    size: int
    url: str


@dataclass(frozen=True)
class DiscordMessage:
    message_id: str
    created_at: str
    content: str
    author: DiscordAuthor
    attachments: tuple[DiscordAttachment, ...] = ()


@dataclass(frozen=True)
class DiscordConversation:
    guild_id: str
    parent_channel_id: str
    thread_id: str
    thread_name: str
    visibility: str
    messages: tuple[DiscordMessage, ...]
```

- [ ] **Step 4: Implement the identity sync script**

The script must iterate the seven profile names, read each profile `.env`, call
`GET https://discord.com/api/v10/users/@me`, and atomically write only this
non-secret shape:

```json
{
  "123456789": {
    "role": "콘텐츠팀장",
    "hermes_profile": "aicompanycontent"
  }
}
```

Use the fixed profile-to-role map from `docs/runtime-architecture-v2.md`. Never
log headers or token lengths. Return a JSON status containing only `ok`,
`identity_count`, and `path`.

- [ ] **Step 5: Run focused tests and a dry-run script test with an injected HTTP callable**

Run: `python -m pytest tests/test_runtime_architecture_v2_discord_history.py -q`  
Expected: participant and model tests PASS; no real network request occurs.

- [ ] **Step 6: Commit the conversation models**

```bash
git add src/runtime_architecture_v2/discord_conversation.py scripts/sync_discord_bot_identities.py tests/test_runtime_architecture_v2_discord_history.py
git commit -m "feat: model Discord conversation identities"
```

---

### Task 3: Read Complete Discord Thread History

**Files:**
- Create: `src/runtime_architecture_v2/discord_history.py`
- Modify: `tests/test_runtime_architecture_v2_discord_history.py`

**Interfaces:**
- Consumes: a Discord bot token held only in memory and a thread ID from `HermesCommandContext`.
- Produces: `DiscordHistoryClient.fetch_conversation(thread_id) -> DiscordConversation`.

- [ ] **Step 1: Add failing tests for thread validation, pagination, and chronology**

```python
def test_fetch_conversation_paginates_and_sorts_oldest_first():
    calls = []

    def request(method, path, query):
        calls.append((method, path, query))
        if path == "/channels/200":
            return {"id": "200", "type": 11, "name": "idea", "parent_id": "100", "guild_id": "1"}
        if "before" not in query:
            return [_message(str(i)) for i in range(200, 100, -1)]
        return [_message(str(i)) for i in range(100, 0, -1)]

    result = DiscordHistoryClient(token="secret", request_json=request).fetch_conversation("200")
    assert len(result.messages) == 200
    assert result.messages[0].message_id == "1"
    assert result.messages[-1].message_id == "200"
    assert calls[-1][2]["before"] == "101"


def test_fetch_conversation_rejects_non_thread_guild_channel():
    client = DiscordHistoryClient(
        token="secret",
        request_json=lambda *_: {"id": "100", "type": 0, "name": "general"},
    )
    with pytest.raises(DiscordHistoryError, match="thread_required"):
        client.fetch_conversation("100")
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_runtime_architecture_v2_discord_history.py -q`  
Expected: FAIL because `DiscordHistoryClient` is missing.

- [ ] **Step 3: Implement the bounded Discord API v10 client**

Use standard-library `urllib.request`; inject `request_json` for tests. Accept
only channel types `10`, `11`, and `12` for guild threads. Request messages in
pages of 100 using `before=<oldest_id>`, cap at a configurable
`max_messages=10000`, reject malformed non-list pages, deduplicate by message
ID, and sort with `key=lambda message: int(message.message_id)`.

```python
class DiscordHistoryError(RuntimeError):
    pass


class DiscordHistoryClient:
    def __init__(self, *, token: str, request_json=None, max_messages: int = 10_000) -> None:
        if not token:
            raise DiscordHistoryError("missing_discord_token")
        self._token = token
        self._request_json = request_json or self._request
        self._max_messages = max_messages

    def fetch_conversation(self, thread_id: str) -> DiscordConversation:
        channel = self._request_json("GET", f"/channels/{thread_id}", {})
        if int(channel.get("type", -1)) not in {10, 11, 12}:
            raise DiscordHistoryError("thread_required")
        messages = self._fetch_all_messages(thread_id)
        return self._to_conversation(channel, messages)
```

Set `Authorization: Bot <token>` only inside `_request`. Use a 20-second timeout,
`User-Agent: AI_Agent/discord-save`, and sanitized errors containing HTTP status
but never response headers or token material.

- [ ] **Step 4: Run pagination, malformed response, empty thread, attachment, and URL-preservation tests**

Run: `python -m pytest tests/test_runtime_architecture_v2_discord_history.py -q`  
Expected: all tests PASS.

- [ ] **Step 5: Commit the history reader**

```bash
git add src/runtime_architecture_v2/discord_history.py tests/test_runtime_architecture_v2_discord_history.py
git commit -m "feat: read paginated Discord thread history"
```

---

### Task 4: Resolve Meetings by Discord Thread ID

**Files:**
- Modify: `src/runtime_architecture_v2/store.py`
- Modify: `tests/test_runtime_architecture_v2_store.py`

**Interfaces:**
- Produces: `MeetingRunStore.find_by_discord_thread_id(thread_id: str) -> MeetingRun | None`.
- Consumes: existing `MeetingRun.trigger.discord.thread_id` and optional `metadata.discord_thread_id`.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_find_by_discord_thread_id_returns_matching_meeting(tmp_path):
    store = MeetingRunStore(tmp_path)
    run = MeetingRun.create(
        meeting_run_id="mr-1",
        trigger_text="콘텐츠 전략",
        user_id="u1",
        channel_id="c1",
        thread_id="t1",
    )
    store.save_meeting_run(run)
    assert store.find_by_discord_thread_id("t1") == run


def test_find_by_discord_thread_id_returns_none_for_unknown_thread(tmp_path):
    assert MeetingRunStore(tmp_path).find_by_discord_thread_id("missing") is None
```

- [ ] **Step 2: Run the resolver tests and verify failure**

Run: `python -m pytest tests/test_runtime_architecture_v2_store.py -q`  
Expected: FAIL with missing method.

- [ ] **Step 3: Implement deterministic matching**

```python
def find_by_discord_thread_id(self, thread_id: str) -> MeetingRun | None:
    self._validate_id(thread_id, "discord_thread_id")
    if not self.runtime_root.exists():
        return None
    matches: list[MeetingRun] = []
    for path in sorted(self.runtime_root.glob("*/meeting_run.json")):
        try:
            run = MeetingRun.from_dict(self._read_json(path))
        except (StoreError, KeyError, TypeError, ValueError):
            continue
        discord = dict(run.trigger.get("discord") or {})
        linked = str(run.metadata.get("discord_thread_id") or discord.get("thread_id") or "")
        if linked == thread_id:
            matches.append(run)
    if not matches:
        return None
    return sorted(matches, key=lambda run: run.meeting_run_id)[-1]
```

Add a corruption test proving one malformed unrelated run does not hide a valid
match. Do not silently accept unsafe thread IDs.

- [ ] **Step 4: Run store and schema regressions**

Run: `python -m pytest tests/test_runtime_architecture_v2_store.py tests/test_runtime_architecture_v2_schemas.py -q`  
Expected: all tests PASS.

- [ ] **Step 5: Commit the meeting resolver**

```bash
git add src/runtime_architecture_v2/store.py tests/test_runtime_architecture_v2_store.py
git commit -m "feat: resolve MeetingRun by Discord thread"
```

---

### Task 5: Expose Shared Redaction and Generate Structured Summaries

**Files:**
- Modify: `src/runtime_architecture_v2/knowledge.py`
- Modify: `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`
- Create: `src/runtime_architecture_v2/conversation_summary.py`
- Test: `tests/test_runtime_architecture_v2_conversation_summary.py`

**Interfaces:**
- Produces: `sanitize_knowledge_text(text: str) -> str`, `ConversationSummary`, and `HermesConversationSummarizer`.
- Consumes: a duck-typed Hermes `ctx.llm` exposing `acomplete_structured`.

- [ ] **Step 1: Add failing public-redaction and structured-summary tests**

```python
def test_public_sanitizer_redacts_secret_and_everyone():
    assert sanitize_knowledge_text("token=abc123 @everyone") == (
        "token=[REDACTED_SECRET] @[redacted-mention]"
    )


@pytest.mark.asyncio
async def test_hermes_summarizer_maps_structured_result():
    llm = FakeLlm(parsed={
        "summary": "콘텐츠 방향을 합의했다.",
        "key_ideas": ["쇼츠 우선"],
        "decisions": ["3편 제작"],
        "unresolved_questions": [],
        "action_items": [{"text": "대본 작성", "owner": "콘텐츠팀장"}],
        "user_perspective": "",
    })
    result = await HermesConversationSummarizer(llm).summarize("transcript")
    assert result.decisions == ("3편 제작",)
    assert result.action_items[0].owner == "콘텐츠팀장"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_runtime_architecture_v2_phase15_knowledge_loop.py tests/test_runtime_architecture_v2_conversation_summary.py -q`  
Expected: FAIL because the public API and summary module are missing.

- [ ] **Step 3: Rename the sanitizer without changing behavior**

Rename `_sanitize_knowledge_text` to `sanitize_knowledge_text`, update all
internal callers, and retain `_sanitize_knowledge_text = sanitize_knowledge_text`
temporarily only if an existing test imports the private name. Add the public
name to `__all__` if the module defines one.

- [ ] **Step 4: Implement structured summary dataclasses and Hermes adapter**

```python
@dataclass(frozen=True)
class ActionItem:
    text: str
    owner: str = ""


@dataclass(frozen=True)
class ConversationSummary:
    summary: str
    key_ideas: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    action_items: tuple[ActionItem, ...] = ()
    user_perspective: str = ""

    @property
    def important(self) -> bool:
        return bool(self.decisions or self.action_items)
```

`HermesConversationSummarizer.summarize` must call
`llm.acomplete_structured` with `json_mode=True`, `temperature=0`,
`max_tokens=1800`, `timeout=120`, `purpose="discord_conversation_save"`, and a
JSON schema that requires all six top-level fields. Pass the transcript as a
plain dict input `{"type": "text", "text": transcript}` so Runtime v2 does
not import Hermes classes. Sanitize the transcript before the LLM call and
sanitize every returned string before constructing `ConversationSummary`.

If the model returns invalid JSON or raises, return a deterministic fallback
whose `summary` is the last non-empty sanitized message truncated to 240
characters and whose other fields are empty. Do not fail raw saving merely
because summarization failed.

- [ ] **Step 5: Run summary and existing knowledge-loop tests**

Run: `python -m pytest tests/test_runtime_architecture_v2_conversation_summary.py tests/test_runtime_architecture_v2_phase15_knowledge_loop.py -q`  
Expected: all tests PASS.

- [ ] **Step 6: Commit shared redaction and summaries**

```bash
git add src/runtime_architecture_v2/knowledge.py src/runtime_architecture_v2/conversation_summary.py tests/test_runtime_architecture_v2_phase15_knowledge_loop.py tests/test_runtime_architecture_v2_conversation_summary.py
git commit -m "feat: summarize sanitized Discord conversations"
```

---

### Task 6: Write Immutable Obsidian Snapshots and Canonical Pages

**Files:**
- Create: `src/runtime_architecture_v2/obsidian_conversations.py`
- Test: `tests/test_runtime_architecture_v2_obsidian_conversations.py`

**Interfaces:**
- Consumes: `DiscordConversation`, resolved participants, optional `MeetingRun`, and `ConversationSummary`.
- Produces: `ObsidianConversationStore.save(*, conversation, participant_resolver, summary, meeting_run=None) -> ObsidianSaveResult`.

- [ ] **Step 1: Write failing storage tests**

Start the test module with deterministic fixtures:

```python
from dataclasses import replace

import pytest

from src.runtime_architecture_v2.conversation_summary import (
    ActionItem,
    ConversationSummary,
)
from src.runtime_architecture_v2.discord_conversation import (
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
    ParticipantResolver,
)
from src.runtime_architecture_v2.obsidian_conversations import (
    ObsidianConversationStore,
)
from src.runtime_architecture_v2.schemas import MeetingRun


def _conversation(latest_id: str = "2", content: str = "3편 제작으로 결정"):
    return DiscordConversation(
        guild_id="1",
        parent_channel_id="100",
        thread_id="200",
        thread_name="콘텐츠 전략",
        visibility="guild",
        messages=(
            DiscordMessage(
                message_id="1",
                created_at="2026-07-13T01:00:00+00:00",
                content="쇼츠 기획을 논의하자",
                author=DiscordAuthor("300", "KBM"),
            ),
            DiscordMessage(
                message_id=latest_id,
                created_at="2026-07-13T01:05:00+00:00",
                content=content,
                author=DiscordAuthor("400", "콘텐츠팀장", bot=True),
            ),
        ),
    )


def _summary(important: bool = True):
    return ConversationSummary(
        summary="쇼츠 3편 제작 방향을 합의했다.",
        decisions=("3편 제작",) if important else (),
        action_items=(ActionItem("대본 작성", "콘텐츠팀장"),) if important else (),
    )


def _meeting():
    return MeetingRun.create(
        meeting_run_id="mr-1",
        trigger_text="콘텐츠 전략",
        user_id="300",
        channel_id="100",
        thread_id="200",
    )
```

Then cover these exact cases:

```python
def test_first_save_creates_snapshot_canonical_checkpoint_log_and_meeting_index(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    result = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=_meeting(),
    )
    assert result.status == "created"
    assert (vault / result.snapshot_path).exists()
    assert (vault / result.canonical_path).exists()
    assert (vault / "wiki" / "log.md").exists()
    assert "mr-1" in (vault / "wiki" / "index.md").read_text(encoding="utf-8")


def test_same_latest_message_id_is_no_change_and_creates_no_second_snapshot(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    kwargs = {
        "conversation": _conversation(),
        "participant_resolver": ParticipantResolver({}),
        "summary": _summary(),
        "meeting_run": None,
    }
    first = store.save(**kwargs)
    second = store.save(**kwargs)
    assert first.status == "created"
    assert second.status == "unchanged"
    assert len(list((vault / "raw" / "chat-logs").glob("*.md"))) == 1


def test_new_message_creates_new_snapshot_and_updates_same_canonical_page(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    resolver = ParticipantResolver({})
    first = store.save(
        conversation=_conversation(), participant_resolver=resolver,
        summary=_summary(), meeting_run=None,
    )
    second = store.save(
        conversation=_conversation("3", "대본은 콘텐츠팀장이 작성"),
        participant_resolver=resolver, summary=_summary(), meeting_run=None,
    )
    assert second.status == "updated"
    assert second.canonical_path == first.canonical_path
    assert second.snapshot_path != first.snapshot_path
    assert len(list((vault / "raw" / "chat-logs").glob("*.md"))) == 2


def test_raw_snapshot_is_never_overwritten(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    result = store.save(
        conversation=_conversation(), participant_resolver=ParticipantResolver({}),
        summary=_summary(), meeting_run=None,
    )
    snapshot = vault / result.snapshot_path
    original = snapshot.read_text(encoding="utf-8")
    store.save(
        conversation=_conversation(), participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary="변경된 요약"), meeting_run=None,
    )
    assert snapshot.read_text(encoding="utf-8") == original


def test_ordinary_unimportant_conversation_updates_log_but_not_index(tmp_path):
    vault = tmp_path / "vault"
    result = ObsidianConversationStore(
        vault_root=vault, runtime_root=tmp_path
    ).save(
        conversation=_conversation(), participant_resolver=ParticipantResolver({}),
        summary=_summary(important=False), meeting_run=None,
    )
    assert result.status == "created"
    assert (vault / "wiki" / "log.md").exists()
    assert not (vault / "wiki" / "index.md").exists()


def test_paths_reject_traversal_in_thread_name_or_id(tmp_path):
    store = ObsidianConversationStore(vault_root=tmp_path / "vault", runtime_root=tmp_path)
    unsafe = replace(_conversation(), thread_id="../outside", thread_name="../../outside")
    with pytest.raises(ValueError, match="thread_id"):
        store.save(
            conversation=unsafe, participant_resolver=ParticipantResolver({}),
            summary=_summary(), meeting_run=None,
        )


def test_secret_and_uncontrolled_mentions_never_reach_any_markdown_file(tmp_path):
    vault = tmp_path / "vault"
    secret = "token=abc123 @everyone"
    ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path).save(
        conversation=_conversation(content=secret),
        participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary=secret),
        meeting_run=None,
    )
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in vault.rglob("*.md")
    )
    assert "abc123" not in combined
    assert "@everyone" not in combined
    assert "[REDACTED_SECRET]" in combined
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `python -m pytest tests/test_runtime_architecture_v2_obsidian_conversations.py -q`  
Expected: FAIL because `obsidian_conversations` does not exist.

- [ ] **Step 3: Implement paths, stable IDs, and checkpoint state**

```python
@dataclass(frozen=True)
class ObsidianSaveResult:
    status: str  # created | updated | unchanged
    classification: str  # conversation | meeting
    new_message_count: int
    snapshot_path: str
    canonical_path: str
    one_line_summary: str


class ObsidianConversationStore:
    def __init__(self, *, vault_root: str | Path, runtime_root: str | Path) -> None:
        self.vault_root = Path(vault_root)
        self.runtime_root = Path(runtime_root) / "runtime" / "discord_save"
```

Checkpoint path is `runtime/discord_save/<thread_id>.json` and contains:

```json
{
  "thread_id": "200",
  "latest_message_id": "999",
  "canonical_path": "wiki/conversations/2026-07-13_idea__200.md",
  "snapshot_paths": ["raw/chat-logs/2026-07-13_idea__200__999.md"]
}
```

Use a strict snowflake regex `^[0-9]{1,24}$`, a filename sanitizer that removes
path separators and control characters, and atomic UTF-8 writes through a temp
file in the destination directory. Before creating a snapshot, check both the
checkpoint latest ID and destination existence. Open raw destination with
exclusive creation (`"x"`) so accidental overwrite fails closed.

- [ ] **Step 4: Implement raw and canonical Markdown renderers**

Raw snapshot frontmatter includes `type`, `saved_at`, Discord IDs/names,
`visibility`, `first_message_id`, `latest_message_id`, `meeting_run_id`, and
participant records. Body sections are `Source`, `Participants`, `Transcript`,
`URLs`, and `Attachments`.

Canonical page sections are:

```markdown
# <thread title>

## Source
## One-line summary
## Key ideas
## Decisions
## Unresolved questions
## Action items
## Participants
## User perspective
## Related notes
## Raw snapshots
```

For meetings, add a `## MeetingRun evidence` section containing only available
Runtime v2 IDs, state, agenda, and artifact-relative paths. Do not generate
missing reports. Display company bots by stable role name; preserve Discord
display name and ID in metadata.

Append an idempotent log line keyed by `thread_id:latest_message_id`. Update the
index when `meeting_run is not None or summary.important`.

- [ ] **Step 5: Run all storage tests**

Run: `python -m pytest tests/test_runtime_architecture_v2_obsidian_conversations.py -q`  
Expected: all tests PASS.

- [ ] **Step 6: Commit Obsidian storage**

```bash
git add src/runtime_architecture_v2/obsidian_conversations.py tests/test_runtime_architecture_v2_obsidian_conversations.py
git commit -m "feat: persist Discord conversations to Obsidian"
```

---

### Task 7: Orchestrate `/save` Without Transport Coupling

**Files:**
- Create: `src/runtime_architecture_v2/save_command.py`
- Test: `tests/test_runtime_architecture_v2_save_command.py`

**Interfaces:**
- Consumes: `HermesCommandContext`, `DiscordHistoryClient`, `MeetingRunStore`, `ParticipantResolver`, `HermesConversationSummarizer`, and `ObsidianConversationStore`.
- Produces: `async run_save_command(*, context, history_client, meeting_store, participant_resolver, summarizer, obsidian_store) -> SaveCommandResult` and `render_save_response(result: SaveCommandResult) -> str`.

- [ ] **Step 1: Write failing orchestration tests**

Use small fakes whose calls are inspectable:

```python
from unittest.mock import AsyncMock, Mock

import pytest

from src.runtime_architecture_v2.conversation_summary import ConversationSummary
from src.runtime_architecture_v2.discord_conversation import (
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
)
from src.runtime_architecture_v2.hermes_command_context import HermesCommandContext
from src.runtime_architecture_v2.obsidian_conversations import ObsidianSaveResult
from src.runtime_architecture_v2.save_command import (
    SaveCommandResult,
    render_save_response,
    run_save_command,
)
from src.runtime_architecture_v2.schemas import MeetingRun


def _save_conversation():
    return DiscordConversation(
        guild_id="1", parent_channel_id="100", thread_id="200",
        thread_name="콘텐츠 전략", visibility="guild",
        messages=(
            DiscordMessage(
                message_id="1", created_at="2026-07-13T01:00:00+00:00",
                content="3편 제작으로 결정",
                author=DiscordAuthor("300", "KBM"),
            ),
        ),
    )


def _save_meeting():
    return MeetingRun.create(
        meeting_run_id="mr-1", trigger_text="콘텐츠 전략", user_id="300",
        channel_id="100", thread_id="200",
    )


def _dependencies(conversation, meeting_run=None):
    history = Mock()
    history.fetch_conversation.return_value = conversation
    meetings = Mock()
    meetings.find_by_discord_thread_id.return_value = meeting_run
    summarizer = Mock()
    summarizer.summarize = AsyncMock(
        return_value=ConversationSummary(summary="콘텐츠 방향을 합의했다.")
    )
    obsidian = Mock()
    obsidian.save.return_value = ObsidianSaveResult(
        status="created",
        classification="meeting" if meeting_run else "conversation",
        new_message_count=len(conversation.messages),
        snapshot_path="raw/chat-logs/snapshot.md",
        canonical_path="wiki/conversations/page.md",
        one_line_summary="콘텐츠 방향을 합의했다.",
    )
    return history, meetings, Mock(), summarizer, obsidian


@pytest.mark.asyncio
async def test_save_rejects_non_discord_and_non_thread_contexts():
    result = await run_save_command(
        context=HermesCommandContext(platform="discord", chat_id="100"),
        history_client=Mock(), meeting_store=Mock(), participant_resolver=Mock(),
        summarizer=Mock(), obsidian_store=Mock(),
    )
    assert result.ok is False
    assert result.error == "thread_required"


@pytest.mark.asyncio
async def test_save_classifies_linked_thread_as_meeting():
    conversation = _save_conversation()
    meeting = _save_meeting()
    history, meetings, resolver, summarizer, obsidian = _dependencies(
        conversation, meeting
    )
    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord", chat_id="200", thread_id="200"
        ),
        history_client=history, meeting_store=meetings,
        participant_resolver=resolver, summarizer=summarizer,
        obsidian_store=obsidian,
    )
    assert result.ok is True
    assert result.classification == "meeting"
    assert obsidian.save.call_args.kwargs["meeting_run"] == meeting


@pytest.mark.asyncio
async def test_save_classifies_unlinked_thread_as_conversation():
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord", chat_id="200", thread_id="200"
        ),
        history_client=history, meeting_store=meetings,
        participant_resolver=resolver, summarizer=summarizer,
        obsidian_store=obsidian,
    )
    assert result.classification == "conversation"
    assert obsidian.save.call_args.kwargs["meeting_run"] is None


@pytest.mark.asyncio
async def test_fallback_summary_still_persists():
    conversation = _save_conversation()
    history, meetings, resolver, summarizer, obsidian = _dependencies(conversation)
    summarizer.summarize.return_value = ConversationSummary(
        summary="3편 제작으로 결정"
    )
    result = await run_save_command(
        context=HermesCommandContext(
            platform="discord", chat_id="200", thread_id="200"
        ),
        history_client=history, meeting_store=meetings,
        participant_resolver=resolver, summarizer=summarizer,
        obsidian_store=obsidian,
    )
    assert result.ok is True
    obsidian.save.assert_called_once()


def test_response_reports_path_count_summary_and_status():
    result = SaveCommandResult(
        ok=True, status="created", classification="meeting",
        new_message_count=42,
        canonical_path="wiki/conversations/page.md",
        summary="쇼츠 3편 제작 방향을 합의했습니다.",
    )
    rendered = render_save_response(result)
    assert "유형: 회의" in rendered
    assert "새 메시지: 42개" in rendered
    assert "wiki/conversations/page.md" in rendered
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_runtime_architecture_v2_save_command.py -q`  
Expected: FAIL because `save_command` does not exist.

- [ ] **Step 3: Implement the async service**

```python
@dataclass(frozen=True)
class SaveCommandResult:
    ok: bool
    status: str = ""
    classification: str = ""
    new_message_count: int = 0
    canonical_path: str = ""
    summary: str = ""
    error: str = ""


async def run_save_command(
    *,
    context: HermesCommandContext,
    history_client: DiscordHistoryClient,
    meeting_store: MeetingRunStore,
    participant_resolver: ParticipantResolver,
    summarizer: HermesConversationSummarizer,
    obsidian_store: ObsidianConversationStore,
) -> SaveCommandResult:
    if context.platform != "discord":
        return SaveCommandResult(ok=False, error="discord_only")
    if not context.thread_id:
        return SaveCommandResult(ok=False, error="thread_required")
    conversation = await asyncio.to_thread(
        history_client.fetch_conversation, context.thread_id
    )
    meeting_run = await asyncio.to_thread(
        meeting_store.find_by_discord_thread_id, context.thread_id
    )
    transcript = render_summary_input(conversation, participant_resolver)
    summary = await summarizer.summarize(transcript)
    saved = await asyncio.to_thread(
        obsidian_store.save,
        conversation=conversation,
        participant_resolver=participant_resolver,
        summary=summary,
        meeting_run=meeting_run,
    )
    return SaveCommandResult(
        ok=True,
        status=saved.status,
        classification=saved.classification,
        new_message_count=saved.new_message_count,
        canonical_path=saved.canonical_path,
        summary=saved.one_line_summary,
    )
```

Catch only expected history, storage, and summary exceptions at this boundary;
map them to `missing_discord_token`, `thread_required`, `history_unavailable`,
`vault_unavailable`, or `save_failed`. Sanitize all error text before rendering.

- [ ] **Step 4: Implement concise Korean Discord responses**

```text
저장 완료
- 유형: 회의
- 새 메시지: 42개
- 문서: wiki/conversations/2026-07-13_콘텐츠-전략__200.md
- 요약: 쇼츠 3편 제작 방향을 합의했습니다.
```

For unchanged saves, render `새로 저장할 메시지가 없습니다` and the existing
canonical path. For guild-channel calls, render `대화를 저장하려면 Discord
스레드 안에서 /save를 실행해주세요.`

- [ ] **Step 5: Run orchestration and storage tests**

Run: `python -m pytest tests/test_runtime_architecture_v2_save_command.py tests/test_runtime_architecture_v2_obsidian_conversations.py -q`  
Expected: all tests PASS.

- [ ] **Step 6: Commit save orchestration**

```bash
git add src/runtime_architecture_v2/save_command.py tests/test_runtime_architecture_v2_save_command.py
git commit -m "feat: orchestrate Discord conversation saves"
```

---

### Task 8: Register `/save` as a Hermes-Native Discord Slash Command

**Files:**
- Create: `hermes_plugins/ai-agent-commands/plugin.yaml`
- Create: `hermes_plugins/ai-agent-commands/__init__.py`
- Test: `tests/test_runtime_architecture_v2_ai_agent_plugin.py`
- Modify: `src/runtime_architecture_v2/command_surface.py`
- Modify: `tests/test_runtime_architecture_v2_phase25_command_surface.py`

**Interfaces:**
- Consumes: Hermes `PluginContext.register_command`, `ctx.llm`, `ctx.profile_name`, and the process's profile-scoped `DISCORD_BOT_TOKEN`.
- Produces: one native `/save` command surfaced by Hermes's existing Discord adapter.

- [ ] **Step 1: Write failing plugin-registration tests with a fake context**

```python
def test_plugin_registers_parameterless_save_command():
    ctx = FakePluginContext()
    plugin.register(ctx)
    assert list(ctx.commands) == ["save"]
    meta = ctx.commands["save"]
    assert meta["args_hint"] == ""
    assert "Obsidian" in meta["description"]


@pytest.mark.asyncio
async def test_save_handler_rejects_trailing_arguments(monkeypatch):
    ctx = FakePluginContext()
    plugin.register(ctx)
    result = await ctx.commands["save"]["handler"]("meeting")
    assert result == "사용법: /save"
```

- [ ] **Step 2: Run plugin tests and verify failure**

Run: `python -m pytest tests/test_runtime_architecture_v2_ai_agent_plugin.py -q`  
Expected: FAIL because the plugin does not exist.

- [ ] **Step 3: Create the official Hermes plugin manifest**

```yaml
name: ai-agent-commands
version: 0.1.0
description: "Runtime Architecture v2 Discord commands for meetings and Obsidian knowledge capture."
author: "kbm323"
```

Do not declare provider dependencies or secrets. The plugin uses host-owned
`ctx.llm` and profile-scoped Discord credentials.

- [ ] **Step 4: Implement the thin plugin handler**

`register(ctx)` defines one async closure and calls:

```python
ctx.register_command(
    "save",
    handler=handle_save,
    description="현재 Discord 스레드를 Obsidian에 저장합니다.",
    args_hint="",
)
```

The closure rejects non-empty args, obtains the Hermes context, reads
`AI_AGENT_ROOT` with default `/home/ubuntu/hermes-workspace/AI_Agent`, reads
`OBSIDIAN_VAULT_PATH`, constructs the Runtime v2 dependencies, passes `ctx.llm`
to `HermesConversationSummarizer`, awaits `run_save_command`, and returns
`render_save_response(result)`. Keep all Hermes imports inside functions so
ordinary AI_Agent tests run without Hermes installed.

- [ ] **Step 5: Update Phase 25 policy to explicitly allow the Hermes plugin surface**

Add a policy field `hermes_plugin_commands_enabled: bool = False`. Permit
`HERMES_SUPPORTED_CUSTOM_SURFACE` only when it is true and all existing safe
posture checks pass. Keep `SEPARATE_STANDALONE_SLASH_ADAPTER` and
`interaction_endpoint_enabled` blocked.

Add tests proving:

```python
policy.evaluate(CommandSurfaceMode.HERMES_SUPPORTED_CUSTOM_SURFACE)
    == CommandSurfaceDecision(False, "hermes_plugin_commands_disabled")

replace(policy, hermes_plugin_commands_enabled=True).evaluate(
    CommandSurfaceMode.HERMES_SUPPORTED_CUSTOM_SURFACE
) == CommandSurfaceDecision(True, "hermes_supported_custom_surface_allowed")
```

- [ ] **Step 6: Run plugin and Phase 25 tests**

Run: `python -m pytest tests/test_runtime_architecture_v2_ai_agent_plugin.py tests/test_runtime_architecture_v2_phase25_command_surface.py -q`  
Expected: all tests PASS and standalone interaction endpoint remains disabled.

- [ ] **Step 7: Commit the Hermes plugin**

```bash
git add hermes_plugins/ai-agent-commands src/runtime_architecture_v2/command_surface.py tests/test_runtime_architecture_v2_ai_agent_plugin.py tests/test_runtime_architecture_v2_phase25_command_surface.py
git commit -m "feat: register Hermes native save command"
```

---

### Task 9: Document, Install, and Verify the Live Command

**Files:**
- Create: `docs/operations/discord-save-slash-command.md`
- Modify only if required by established export convention: `src/runtime_architecture_v2/__init__.py`

**Interfaces:**
- Consumes: the completed plugin and server paths.
- Produces: reproducible install, rollback, test, and live-smoke instructions.

- [ ] **Step 1: Write the operations document**

Include these exact deployment inputs and commands:

```bash
export AI_AGENT_ROOT=/home/ubuntu/hermes-workspace/AI_Agent
export OBSIDIAN_VAULT_PATH=/home/ubuntu/Obsidian

python scripts/sync_discord_bot_identities.py \
  --output runtime/discord_bot_identities.json

hermes plugins install \
  /home/ubuntu/hermes-workspace/AI_Agent/hermes_plugins/ai-agent-commands

hermes plugins list
```

Document that all seven gateway processes must load the same plugin code, but
the first guild smoke uses the assistant profile in a designated test thread.
Record backup/rollback commands using `hermes plugins disable ai-agent-commands`
or the exact CLI action supported by the installed Hermes version. Do not
manually edit Hermes Core.

- [ ] **Step 2: Run the focused Python suite**

Run:

```bash
python -m pytest \
  tests/test_runtime_architecture_v2_hermes_command_context.py \
  tests/test_runtime_architecture_v2_discord_history.py \
  tests/test_runtime_architecture_v2_conversation_summary.py \
  tests/test_runtime_architecture_v2_obsidian_conversations.py \
  tests/test_runtime_architecture_v2_save_command.py \
  tests/test_runtime_architecture_v2_ai_agent_plugin.py \
  tests/test_runtime_architecture_v2_store.py \
  tests/test_runtime_architecture_v2_phase15_knowledge_loop.py \
  tests/test_runtime_architecture_v2_phase25_command_surface.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 3: Run established Runtime v2 regressions**

Run:

```bash
python -m pytest \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py \
  tests/test_runtime_architecture_v2_phase21_discord_webhook.py \
  tests/test_runtime_architecture_v2_phase30_meeting_e2e.py \
  tests/test_runtime_architecture_v2_phase32_live_audit.py \
  tests/test_runtime_architecture_v2_on_demand_exports.py \
  tests/test_runtime_smoke_packet.py -q
```

Expected: all selected tests PASS with no automatic final report or knowledge
write regression.

- [ ] **Step 4: Run static and secret checks**

Run:

```bash
npm run typecheck
npm run lint:ruff
git diff --check
git diff --cached --check
```

Then run the repository's established added-line secret-pattern scan over
`src`, `tests`, `scripts`, `hermes_plugins`, and `docs`. Expected findings: `0`.

- [ ] **Step 5: Install on `aiagent` and perform bounded live smoke**

After pushing and pulling the reviewed commits:

1. Confirm `/home/ubuntu/Obsidian`, `raw/chat-logs`, and `wiki` are writable.
2. Install and enable the plugin through `hermes plugins install`.
3. Restart only the assistant gateway first.
4. Confirm `/save` appears in Discord's native command picker.
5. In a designated ordinary test thread, invoke `/save` and verify a
   `conversation` snapshot and canonical page.
6. Invoke `/save` again without new messages and verify `unchanged` with no new
   snapshot.
7. In a thread linked to a test `MeetingRun`, invoke `/save` and verify
   `type: meeting` plus MeetingRun evidence.
8. Invoke `/save` in a normal guild channel and verify the safe thread-required
   response.
9. Inspect generated files for token, bearer, password, `@everyone`, and
   `@here`; expected findings: none.
10. Restart the remaining six gateways only after the assistant smoke passes.

- [ ] **Step 6: Commit operations documentation**

```bash
git add docs/operations/discord-save-slash-command.md src/runtime_architecture_v2/__init__.py
git commit -m "docs: add Discord save command runbook"
```

If `__init__.py` does not require a project-standard export change, omit it from
both the edit and `git add` command.

---

## Follow-Up Plans

Do not mix these into the `/save` implementation commits:

1. `2026-07-13-discord-meeting-slash-command.md`: add `/meeting start <topic>`
   and `/meeting report`, reuse the Hermes plugin, bind the actual Discord thread
   ID to `MeetingRun`, and call existing gateway/on-demand-export services.
2. `2026-07-13-discord-llmwiki-slash-command.md`: add `/llmwiki ingest|find|note`,
   implement SSRF-safe URL retrieval and source-type adapters, and apply the
   vault's Source Summary/index/log rules.

The `/save` plan intentionally creates the shared Hermes plugin, command context,
redaction, identity, and Obsidian storage foundations those plans will reuse.
