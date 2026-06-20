# Company Second Brain Agent Guide

## Scope

This Second Brain stores long-term company knowledge for the AI Virtual Entertainment Company.

Use it for:
- company strategy and operating decisions
- meeting decisions and validated outputs
- market, tech, content, art, business, and validation research
- reusable context for future MeetingRun packets

Do not use it for:
- personal schedules or private user notes
- Hermes session dumps
- raw worker-output dumps without synthesis
- temporary task state

## Layout

```text
raw/          immutable source material
wiki/         synthesized Obsidian-compatible LLM Wiki pages
wiki/index.md navigation index
wiki/log.md   append-only change log
```

## Rules

- Preserve raw sources under `raw/`; do not edit raw files after ingest.
- Write synthesized knowledge under `wiki/`.
- Use `[[wikilinks]]` between related wiki pages.
- Update `wiki/index.md` when adding meaningful pages.
- Append every ingest/update/lint action to `wiki/log.md`.
- Keep Hermes memory limited to compact durable operating facts; store long-form knowledge here.
