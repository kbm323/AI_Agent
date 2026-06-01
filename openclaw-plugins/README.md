# OpenClaw Plugins

This directory stores OpenClaw local plugin source that AI_Agent should manage
as repository source of truth.

## inter-agent-orchestration

Copied from WSL:

```text
/home/kbm/.openclaw/local-plugins/inter-agent-orchestration
```

Destination:

```text
openclaw-plugins/inter-agent-orchestration
```

Notes:

- `node_modules` is intentionally excluded.
- The plugin declares `openclaw` as a peer dependency.
- Run plugin tests from the WSL original or after installing/linking the
  OpenClaw SDK dependency for this repo copy.
- Current important entrypoints:
  - `prepareThreadOrchestrationFromFacts(...)`
  - `runThreadReviewFromFacts(...)`
  - `resumeWaitingOrchestrationFromUserDecision(...)`
  - `selectReviewerReply(...)`

