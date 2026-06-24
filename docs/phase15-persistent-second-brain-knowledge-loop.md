# Phase 15 Persistent Second Brain / Knowledge Loop Result

## Status

```text
IMPLEMENTED — pending final QA/review/commit gate
```

Phase 15 adds a repo-local plain markdown Second Brain that persists meeting knowledge and retrieves it for future meeting context.

## What was implemented

```text
src/runtime_architecture_v2/knowledge.py
scripts/run_phase15_knowledge_loop_pilot.py
tests/test_runtime_architecture_v2_phase15_knowledge_loop.py
docs/phase15-persistent-second-brain-knowledge-loop-plan.md
docs/phase15-persistent-second-brain-knowledge-loop.md
```

## Knowledge layout

```text
knowledge/
  AGENTS.md
  raw/
    YYYY-MM-DD_<meeting_run_id>.md
  wiki/
    index.md
    log.md
    meetings/
      <meeting_run_id>.md
```

## Design decision

```text
Plain markdown first, Obsidian-compatible later.
```

The implementation uses YAML frontmatter-compatible markdown and Obsidian-style wikilinks such as `[[meetings/<meeting_run_id>]]`, but does not depend on Obsidian plugins, Dataview, Canvas, or a vault setup.

## What is proven

```text
KnowledgeEntry schema serializes to dict and markdown frontmatter.
Meeting knowledge writes raw and wiki markdown artifacts.
wiki/index.md is updated deterministically.
wiki/log.md records append-only knowledge events.
MeetingRun metadata stores knowledge_refs and latest_knowledge_entry_id.
Secret-like text and @everyone/@here mentions are redacted before writing.
Deterministic retrieval finds relevant wiki notes by query terms.
Phase 15 CLI emits machine-readable JSON in dry-run mode.
Phase 15 dry-run reuses Phase 14 dry-run output and performs no live worker or Discord calls.
```

## Dry-run command

```bash
python3 scripts/run_phase15_knowledge_loop_pilot.py --mode dry-run
```

Expected result shape:

```json
{
  "ok": true,
  "pilot_id": "phase15_persistent_second_brain_knowledge_loop",
  "mode": "dry-run",
  "knowledge_entry_id": "kb_<meeting_run_id>_meeting_summary",
  "retrieval_match_count": 1
}
```

## Guardrails

```text
Hermes Core untouched.
No Obsidian dependency.
No vector DB / external DB.
No live Discord mutation.
No live worker execution in Phase 15.
No secrets or uncontrolled mentions written to knowledge artifacts.
Path traversal through meeting_run_id is rejected.
```

## What remains

```text
Obsidian vault/app integration remains future optional work.
Semantic/vector retrieval remains out of scope.
Autonomous scheduling/Kanban operations remain Phase 16.
Production monitoring/recovery remains Phase 17.
```
