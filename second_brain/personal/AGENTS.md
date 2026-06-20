# Personal Second Brain Agent Guide

## Scope

This Second Brain stores private user-support knowledge for the Personal Assistant layer.

Use it for:
- schedules, reminders, and personal planning
- private notes and preferences that should not become company knowledge
- personal goals and recurring briefings
- action items extracted from company outputs for user support

Do not use it for:
- company strategy or market/tech/content research
- company MeetingRun source-of-truth artifacts
- public company decisions
- raw Hermes session dumps

## Layout

```text
raw/          immutable personal source material
wiki/         synthesized Obsidian-compatible LLM Wiki pages
wiki/index.md navigation index
wiki/log.md   append-only change log
```

## Rules

- Keep personal knowledge separate from company knowledge.
- Reference company outputs only when needed for user support.
- Use `[[wikilinks]]` between related wiki pages.
- Update `wiki/index.md` when adding meaningful pages.
- Append every ingest/update/lint action to `wiki/log.md`.
