# Automated Test And Static Validation Commands

## Scope

These commands are the recorded automated test and static validation entrypoints for the current MVP verification layer. The table keeps each command tied to the expected observable result and the concrete artifact used as that recorded result.

## Command Matrix

| id | command | expected result | result artifact | kind |
| --- | --- | --- | --- | --- |
| automated_tests | `npm test` | TAP output exits 0 with all test files passing | `tests/*.test.ts` | automated_test |
| mvp_tests | `npm run test:mvp --silent` | MVP test suite exits 0 after discovering MVP-related \`*.test.ts\` artifacts | `scripts/run-mvp-tests.ts` | automated_test |
| verification_workflow_smoke | `npm run run-verification-workflow --silent` | JSON reports \`status: "passed"\` and writes independent reproduced workflow evidence to \`docs/generated/verification-workflow-result.json\` | `docs/generated/verification-workflow-result.json` | automated_test |
| typecheck | `npm run check:typecheck --silent` | JSON with \`schemaVersion: "typecheck-command-check.v1"\` and \`status: "passed"\` | `docs/generated/typecheck-check-result.json` | static_validation |
| verification_output | `npm run check:verification-output --silent` | JSON with \`schemaVersion: "verification-output-check-result.v1"\` and \`status: "passed"\` | `docs/generated/verification-output.json` | static_validation |
| environment_dependencies | `npm run check:environment-dependencies --silent` | JSON with \`schemaVersion: "environment-dependency-check.v1"\` and \`status: "passed"\` | `docs/environment-dependency-verification.md` | static_validation |

## Reproduced Workflow Evidence Smoke Check

Run:

```bash
npm run run-verification-workflow --silent
```

Expected output:

```json
{
  "command": "ai-agent run-verification-workflow",
  "status": "passed",
  "artifact": {
    "path": "/absolute/project/path/docs/generated/verification-workflow-result.json",
    "schemaVersion": "verification-workflow-runner.v1",
    "caseCount": 2,
    "passedCaseCount": 2
  }
}
```

The command independently reproduces a deterministic finalized meeting loop and an ambiguous-request escalation case, then records the concrete evidence in `docs/generated/verification-workflow-result.json`. The documentation mapping check validates that artifact's schema, successful status, required case names, MVP workflow execution, escalation execution, and raw-storage/loop-summary separation.

## Typecheck Command Resolution

`npm run check:typecheck --silent` verifies `package.json` `scripts.typecheck` when that script is configured. If a project fixture has no configured `scripts.typecheck`, the checker uses the documented faithful fallback `node --check src/*.ts && node --check scripts/*.ts && node --check tests/*.ts` and records that selected command in `docs/generated/typecheck-check-result.json`.

## Runnable Mapping Check

Run:

```bash
npm run check:validation-command-documentation --silent
```

Expected output:

```json
{
  "schemaVersion": "validation-command-documentation-check.v1",
  "command": "ai-agent check:validation-command-documentation",
  "status": "passed"
}
```

The check fails if the table drifts from the executable specification, a referenced package script is missing, a recorded result artifact cannot be found, or the reproduced workflow evidence artifact is malformed.
