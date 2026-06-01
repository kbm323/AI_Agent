# Executor Investigation

Date: 2026-05-28

## OpenClaw

Result: direct internal executor is available.

Observed commands:

```bash
openclaw agents list
openclaw gateway call health --json --timeout 5000
openclaw agent --agent main --message "Return exactly: OPENCLAW_OK" --json --timeout 90
```

Findings:

- Installed CLI: `openclaw`
- Version observed: `OpenClaw 2026.5.19`
- Gateway service is running on `ws://127.0.0.1:18789`
- Default agent exists: `main`
- Discord bot id observed from gateway health: `1505917780577357928`
- Direct agent call returned `OPENCLAW_OK`
- The first gateway agent attempt may require a scope upgrade approval, but OpenClaw can fall back to embedded execution and still return JSON.

Decision:

- Prefer `openclaw agent --agent main --message <prompt> --json --timeout <seconds>` for Phase 1 real executor.
- Do not capture OpenClaw output from Discord.
- Parse JSON payloads from stdout and store the extracted text as `draft` / `final_synthesis`.

## Hermes

Result: direct CLI executor is available.

Observed command:

```bash
hermes -z "Return exactly: HERMES_OK"
```

Findings:

- Installed CLI: `hermes`
- `-z/--oneshot` is documented as script-friendly one-shot mode.
- Direct oneshot returned `HERMES_OK`.
- In sandbox, Hermes needs write access to `~/.hermes/logs`; normal runtime execution should run outside the Codex file sandbox.

Decision:

- Prefer `hermes -z <review prompt>` for Phase 1 real reviewer executor.
- Require Hermes output to include `Verdict: agree | agree_with_changes | disagree | needs_user_decision`.
- Fall back to `agree_with_changes` if the verdict line is missing.

## Discord Adapter Boundary

The Discord adapter remains a delivery layer only:

- parent channel detects user request
- parent receives launcher notice only: `Agent discussion started -> <thread>`
- thread receives timeline messages
- executor outputs are created internally through CLI, then stored in SQLite and posted to thread

Discord mention + polling remains a fallback option for Hermes only if direct CLI/API becomes unavailable.
