# Environment And Dependency Verification

## Scope

These commands verify the local runtime and the lightweight project checks needed before running the AI_Agent MVP verification suite.

## Commands

| id | command | expected output | purpose |
| --- | --- | --- | --- |
| node_version | `node --version` | `v24.x or newer` | Confirms the runtime satisfies `package.json` engines. |
| npm_version | `npm --version` | `semver version string` | Confirms the npm command runner is available. |
| health_check | `npm run health-check --silent` | JSON with `schemaVersion: "health-check.v1"` and `status: "ok"` | Confirms the project health-check command is present and executable. |
| typecheck_check | `npm run check:typecheck --silent` | JSON with `schemaVersion: "typecheck-command-check.v1"` and `status: "passed"` | Confirms the configured typecheck verification command is present and executable. |

## Runnable Verification

Run the documented command checker:

```bash
npm run check:environment-dependencies --silent
```

Expected output:

```json
{
  "schemaVersion": "environment-dependency-check.v1",
  "command": "ai-agent check-environment-dependencies",
  "status": "passed"
}
```

The check fails if the command table drifts from the executable specification, an npm script referenced by the documentation is missing, or any documented command exits non-zero or returns an unexpected output shape.
