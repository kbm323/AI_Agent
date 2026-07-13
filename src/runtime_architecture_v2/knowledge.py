"""Phase 15 repo-local Second Brain knowledge loop.

This module intentionally stays domain-local. It writes plain markdown artifacts
that can later be opened as an Obsidian vault, but it does not depend on
Obsidian, vectors, databases, or Hermes Core internals.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .multi_bot import MultiBotSession, run_phase14_multi_bot_pilot
from .schemas import MeetingRun
from .store import MeetingRunStore

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_TOKEN_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*"
        r"[^\s\\`'\"]+"
    ),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{6,}"),
)
_MENTION_RE = re.compile(r"@(everyone|here)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z0-9가-힣_:-]+")
_URL_RE = re.compile(
    r'\b[a-z][a-z0-9+.-]*://[^\s<>"`]+',
    re.IGNORECASE,
)
_ENCODED_TOKEN_RE = re.compile(r"\S*%[0-9a-f]{2}\S*", re.IGNORECASE)
_SECRET_URL_KEYS = {
    "access_token",
    "auth",
    "hm",
    "key",
    "sig",
    "signature",
    "token",
    "x_amz_signature",
}


@dataclass(frozen=True)
class KnowledgeEntry:
    """One Obsidian-compatible markdown knowledge record."""

    knowledge_id: str
    title: str
    kind: str
    source_meeting_run_id: str
    summary: str
    tags: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    created_at: str = ""
    raw_path: str = ""
    wiki_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_safe_id(self.knowledge_id, "knowledge_id")
        _validate_safe_id(self.source_meeting_run_id, "source_meeting_run_id")
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "links", tuple(self.links))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "title": self.title,
            "kind": self.kind,
            "source_meeting_run_id": self.source_meeting_run_id,
            "summary": self.summary,
            "tags": list(self.tags),
            "links": list(self.links),
            "created_at": self.created_at,
            "raw_path": self.raw_path,
            "wiki_path": self.wiki_path,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> KnowledgeEntry:
        return cls(
            knowledge_id=str(payload["knowledge_id"]),
            title=str(payload["title"]),
            kind=str(payload["kind"]),
            source_meeting_run_id=str(payload["source_meeting_run_id"]),
            summary=str(payload.get("summary", "")),
            tags=tuple(str(t) for t in payload.get("tags", ())),
            links=tuple(str(link) for link in payload.get("links", ())),
            created_at=str(payload.get("created_at", "")),
            raw_path=str(payload.get("raw_path", "")),
            wiki_path=str(payload.get("wiki_path", "")),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_markdown(self) -> str:
        tags = "\n".join(f"  - {tag}" for tag in self.tags)
        links = "\n".join(f"- {link}" for link in self.links) or "- none"
        metadata = json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)
        return (
            "---\n"
            f"knowledge_id: {self.knowledge_id}\n"
            f"kind: {self.kind}\n"
            f"source_meeting_run_id: {self.source_meeting_run_id}\n"
            f"created_at: {self.created_at}\n"
            "obsidian_compatible: true\n"
            "tags:\n"
            f"{tags or '  - untagged'}\n"
            f"metadata_json: {metadata}\n"
            "---\n\n"
            f"# {self.title}\n\n"
            "## Summary\n\n"
            f"{self.summary}\n\n"
            "## Links\n\n"
            f"{links}\n"
        )


@dataclass(frozen=True)
class KnowledgeWriteResult:
    """Result for one knowledge write loop."""

    ok: bool
    entry: KnowledgeEntry
    meeting_run: MeetingRun
    raw_path: Path
    wiki_path: Path
    index_path: Path
    log_path: Path
    agents_path: Path
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "knowledge_entry_id": self.entry.knowledge_id,
            "meeting_run_id": self.entry.source_meeting_run_id,
            "raw_path": str(self.raw_path),
            "wiki_path": str(self.wiki_path),
            "index_path": str(self.index_path),
            "log_path": str(self.log_path),
            "agents_path": str(self.agents_path),
            "error": self.error,
        }


@dataclass(frozen=True)
class KnowledgeContextResult:
    """Deterministic retrieval output over wiki markdown files."""

    ok: bool
    query: str
    matches: tuple[dict[str, Any], ...]
    context_markdown: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "query": self.query,
            "matches": list(self.matches),
            "context_markdown": self.context_markdown,
            "error": self.error,
        }


def write_meeting_knowledge(
    *,
    root: str | Path,
    meeting_run: MeetingRun,
    session: MultiBotSession,
    phase: str = "phase15",
    tags: tuple[str, ...] = ("phase15", "ai-company"),
) -> KnowledgeWriteResult:
    """Persist a meeting session into raw and wiki markdown knowledge files."""

    if meeting_run.meeting_run_id != session.meeting_run_id:
        raise ValueError("meeting_run_id mismatch between MeetingRun and session")
    _validate_safe_id(meeting_run.meeting_run_id, "meeting_run_id")
    _validate_safe_id(phase, "phase")

    root = Path(root)
    knowledge_root = root / "knowledge"
    raw_dir = knowledge_root / "raw"
    wiki_dir = knowledge_root / "wiki"
    meetings_dir = wiki_dir / "meetings"
    raw_dir.mkdir(parents=True, exist_ok=True)
    meetings_dir.mkdir(parents=True, exist_ok=True)

    agents_path = knowledge_root / "AGENTS.md"
    index_path = wiki_dir / "index.md"
    log_path = wiki_dir / "log.md"
    meeting_id = meeting_run.meeting_run_id
    knowledge_id = f"kb_{meeting_id}_meeting_summary"
    wikilink = f"[[meetings/{meeting_id}]]"
    created_at = _now_iso()

    summary = sanitize_knowledge_text(
        session.consensus_summary
        or str(meeting_run.trigger.get("text") or "No consensus summary recorded.")
    )
    title = f"Meeting {meeting_id} Knowledge Summary"
    raw_path = raw_dir / f"{created_at[:10]}_{meeting_id}.md"
    wiki_path = meetings_dir / f"{meeting_id}.md"

    entry = KnowledgeEntry(
        knowledge_id=knowledge_id,
        title=title,
        kind="meeting_summary",
        source_meeting_run_id=meeting_id,
        summary=summary,
        tags=tuple(dict.fromkeys((*tags, phase, "obsidian-compatible"))),
        links=(wikilink,),
        created_at=created_at,
        raw_path=str(raw_path.relative_to(root)),
        wiki_path=str(wiki_path.relative_to(root)),
        metadata={
            "participants": [
                sanitize_knowledge_text(participant)
                for participant in session.participants
            ],
            "consensus_reached": session.consensus_reached,
            "escalation_required": session.escalation_required,
            "phase": phase,
        },
    )

    raw_text = _build_raw_note(entry, meeting_run, session)
    wiki_text = entry.to_markdown() + _build_wiki_body(meeting_run, session)
    _atomic_write_text(raw_path, raw_text)
    _atomic_write_text(wiki_path, wiki_text)
    _ensure_agents_file(agents_path)
    _update_index(index_path, entry)
    _append_log(log_path, entry)

    knowledge_refs = tuple(meeting_run.metadata.get("knowledge_refs") or ())
    updated_metadata = {
        **meeting_run.metadata,
        "knowledge_refs": list(dict.fromkeys((*knowledge_refs, entry.wiki_path))),
        "latest_knowledge_entry_id": entry.knowledge_id,
    }
    updated_run = replace(meeting_run, metadata=updated_metadata)
    MeetingRunStore(root).save_meeting_run(updated_run)

    return KnowledgeWriteResult(
        ok=True,
        entry=entry,
        meeting_run=updated_run,
        raw_path=raw_path,
        wiki_path=wiki_path,
        index_path=index_path,
        log_path=log_path,
        agents_path=agents_path,
    )


def retrieve_knowledge_context(
    *,
    root: str | Path,
    query: str,
    limit: int = 3,
) -> KnowledgeContextResult:
    """Retrieve relevant wiki notes with deterministic term scoring."""

    wiki_root = Path(root) / "knowledge" / "wiki"
    if not wiki_root.exists():
        return KnowledgeContextResult(
            ok=True, query=query, matches=(), context_markdown=""
        )
    terms = _query_terms(query)
    scored: list[tuple[int, str, Path, str]] = []
    for path in sorted(wiki_root.rglob("*.md")):
        if path.name in {"index.md", "log.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        score = sum(lower.count(term) for term in terms)
        if score > 0:
            scored.append((score, path.name, path, text))
    scored.sort(key=lambda item: (-item[0], item[1]))

    matches: list[dict[str, Any]] = []
    sections: list[str] = []
    for score, _name, path, text in scored[: max(limit, 0)]:
        knowledge_id = _extract_frontmatter_value(text, "knowledge_id")
        title = _first_heading(text) or path.stem
        snippet = _snippet(text, terms)
        rel = path.relative_to(Path(root))
        matches.append(
            {
                "knowledge_id": knowledge_id,
                "title": title,
                "path": str(rel),
                "score": score,
                "snippet": snippet,
            }
        )
        sections.append(f"## {title}\n\nSource: {rel}\n\n{snippet}")

    return KnowledgeContextResult(
        ok=True,
        query=query,
        matches=tuple(matches),
        context_markdown="\n\n".join(sections),
    )


def run_phase15_knowledge_loop_pilot(
    *,
    root: str | Path,
    mode: Literal["dry-run"] = "dry-run",
    query: str = "버추얼 아이돌 팬 참여 쇼츠 데뷔",
) -> dict[str, Any]:
    """Run a deterministic Phase 15 pilot using Phase 14 dry-run output."""

    if mode != "dry-run":
        return {
            "ok": False,
            "mode": mode,
            "error": "phase15_only_supports_dry_run",
        }
    phase14 = run_phase14_multi_bot_pilot(root=root, mode="dry-run")
    write_result = write_meeting_knowledge(
        root=root,
        meeting_run=phase14.meeting_run,
        session=phase14.session,
        phase="phase15",
    )
    context = retrieve_knowledge_context(root=root, query=query, limit=3)
    return {
        "ok": write_result.ok and context.ok and bool(context.matches),
        "pilot_id": "phase15_persistent_second_brain_knowledge_loop",
        "mode": mode,
        "meeting_run_id": phase14.meeting_run.meeting_run_id,
        "knowledge_entry_id": write_result.entry.knowledge_id,
        "raw_path": str(write_result.raw_path),
        "wiki_path": str(write_result.wiki_path),
        "index_path": str(write_result.index_path),
        "log_path": str(write_result.log_path),
        "retrieval_match_count": len(context.matches),
        "retrieval_matches": list(context.matches),
        "error": "" if context.matches else "knowledge_context_not_found",
    }


def _build_raw_note(
    entry: KnowledgeEntry,
    meeting_run: MeetingRun,
    session: MultiBotSession,
) -> str:
    trigger_text = sanitize_knowledge_text(str(meeting_run.trigger.get("text") or ""))
    payload = json.dumps(
        session.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    )
    payload = sanitize_knowledge_text(payload)
    return (
        entry.to_markdown()
        + "\n## Source Meeting\n\n"
        + f"- MeetingRun: `{meeting_run.meeting_run_id}`\n"
        + f"- State: `{meeting_run.state}`\n"
        + f"- Trigger: {trigger_text}\n\n"
        + "## Raw Session JSON\n\n"
        + "```json\n"
        + payload
        + "\n```\n"
    )


def _build_wiki_body(meeting_run: MeetingRun, session: MultiBotSession) -> str:
    participants = (
        ", ".join(sanitize_knowledge_text(p) for p in session.participants) or "none"
    )
    trigger_text = sanitize_knowledge_text(str(meeting_run.trigger.get("text") or ""))
    return (
        "\n## Meeting Facts\n\n"
        f"- Participants: {participants}\n"
        f"- Consensus reached: {str(session.consensus_reached).lower()}\n"
        f"- Escalation required: {str(session.escalation_required).lower()}\n"
        f"- Trigger: {trigger_text}\n"
    )


def _update_index(path: Path, entry: KnowledgeEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"- {entry.created_at[:10]} {entry.links[0]} — {entry.summary[:120]}"
    header = "# Knowledge Index\n\n## Meetings\n\n"
    lines = [ln for ln in existing.splitlines() if ln.strip()]
    if not existing:
        text = header + line + "\n"
    elif line in existing:
        text = existing if existing.endswith("\n") else existing + "\n"
    else:
        text = "\n".join(lines + [line]) + "\n"
        if "# Knowledge Index" not in text:
            text = header + line + "\n"
    _atomic_write_text(path, text)


def _append_log(path: Path, entry: KnowledgeEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write_text(path, "# Knowledge Log\n\n")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"- {entry.created_at} `{entry.knowledge_id}` "
            f"{entry.links[0]} kind={entry.kind}\n"
        )


def _ensure_agents_file(path: Path) -> None:
    if path.exists():
        return
    text = (
        "# AI_Agent Knowledge Base\n\n"
        "This directory is a repo-local plain markdown Second Brain.\n\n"
        "Rules:\n"
        "- Keep raw source records under `raw/`.\n"
        "- Keep curated Obsidian-compatible notes under `wiki/`.\n"
        "- Do not write secrets, tokens, or uncontrolled mentions.\n"
        "- Prefer stable wikilinks such as `[[meetings/<meeting_run_id>]]`.\n"
    )
    _atomic_write_text(path, text)


def sanitize_knowledge_text(text: str) -> str:
    """Redact credentials, including URL userinfo and encoded nested URLs."""

    urls: list[str] = []

    def protect_url(match: re.Match[str]) -> str:
        url, trailing = _split_url_trailing(match.group(0))
        placeholder = f"ORACLEURLPLACEHOLDER{len(urls)}END"
        urls.append(sanitize_url(url) + trailing)
        return placeholder

    protected = _URL_RE.sub(protect_url, text)

    def protect_encoded_url(match: re.Match[str]) -> str:
        decoded = _decode_percent_encoding(match.group(0))
        if not _URL_RE.search(decoded):
            return match.group(0)
        placeholder = f"ORACLEURLPLACEHOLDER{len(urls)}END"
        urls.append(sanitize_knowledge_text(decoded))
        return placeholder

    protected = _ENCODED_TOKEN_RE.sub(protect_encoded_url, protected)
    safe = _sanitize_non_url_text(protected)
    for index, url in enumerate(urls):
        safe = safe.replace(f"ORACLEURLPLACEHOLDER{index}END", url)
    return safe


def sanitize_url(value: str) -> str:
    """Remove URL userinfo and redact credential-like parameters."""

    without_userinfo = _remove_url_userinfo(value)
    try:
        parsed = urlsplit(without_userinfo)
    except ValueError:
        return _sanitize_non_url_text(without_userinfo)
    query = _sanitize_url_parameters(parsed.query)
    fragment = (
        _sanitize_url_parameters(parsed.fragment)
        if "=" in parsed.fragment
        else _sanitize_encoded_url_component(parsed.fragment)
    )
    return urlunsplit(
        (
            _sanitize_non_url_text(parsed.scheme),
            _sanitize_non_url_text(parsed.netloc),
            _sanitize_encoded_url_component(parsed.path),
            query,
            fragment,
        )
    )


def _sanitize_non_url_text(text: str) -> str:
    safe = text
    for pattern in _TOKEN_PATTERNS:
        safe = pattern.sub("[REDACTED_SECRET]", safe)
    return _MENTION_RE.sub("@[redacted-mention]", safe)


def _remove_url_userinfo(value: str) -> str:
    scheme_end = value.find("://")
    if scheme_end < 0:
        return value
    authority_start = scheme_end + 3
    boundary_positions = [
        position
        for delimiter in "/?#"
        if (position := value.find(delimiter, authority_start)) >= 0
    ]
    authority_end = min(boundary_positions, default=len(value))
    authority = value[authority_start:authority_end]
    decoded_authority = _decode_percent_encoding(authority)
    if "@" not in decoded_authority:
        return value
    host = decoded_authority.rsplit("@", 1)[1]
    return f"{value[:authority_start]}{host}{value[authority_end:]}"


def _split_url_trailing(value: str) -> tuple[str, str]:
    url = value.rstrip(".,;:!?)")
    return url, value[len(url) :]


def _sanitize_url_parameters(value: str) -> str:
    sanitized_pairs = []
    for key, parameter_value in parse_qsl(value, keep_blank_values=True):
        safe_key = _sanitize_non_url_text(key)
        safe_value = (
            "[REDACTED_SECRET]"
            if _is_secret_url_key(key)
            else _sanitize_encoded_url_component(parameter_value)
        )
        sanitized_pairs.append((safe_key, safe_value))
    return urlencode(sanitized_pairs, doseq=True, safe="[]/:+")


def _sanitize_encoded_url_component(value: str) -> str:
    decoded = _decode_percent_encoding(value)
    if _URL_RE.search(decoded):
        return sanitize_knowledge_text(decoded)
    return _sanitize_non_url_text(value)


def _decode_percent_encoding(value: str) -> str:
    decoded = value
    for _index in range(len(value) + 1):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    return decoded


def _is_secret_url_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return (
        normalized in _SECRET_URL_KEYS
        or normalized.endswith(("_auth", "_key", "_sig", "_signature", "_token"))
        or any(
            marker in normalized
            for marker in ("password", "passwd", "pwd", "secret", "credential")
        )
    )


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(match.group(0).lower() for match in _WORD_RE.finditer(query))
    )


def _snippet(text: str, terms: tuple[str, ...], max_len: int = 360) -> str:
    body = re.sub(r"---.*?---", "", text, count=1, flags=re.DOTALL).strip()
    lower = body.lower()
    positions = [lower.find(term) for term in terms if term in lower]
    start = max(min(positions) - 80, 0) if positions else 0
    snippet = body[start : start + max_len].strip()
    return snippet.replace("\n\n\n", "\n\n")


def _extract_frontmatter_value(text: str, key: str) -> str:
    prefix = f"{key}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _validate_safe_id(value: str, label: str) -> None:
    if not value or value in {".", ".."} or value.startswith("."):
        raise ValueError(f"unsafe {label}: {value}")
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value}")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
