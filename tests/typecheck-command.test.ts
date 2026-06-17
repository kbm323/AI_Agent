import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  checkTypecheckCommand,
  executeCheckTypecheckCommand,
  TYPECHECK_CHECK_ARTIFACT_PATH,
  TYPECHECK_DOCUMENTED_FALLBACK_COMMAND,
} from "../scripts/check-typecheck.ts";

test("typecheck command check executes the configured typecheck script and reports exit code 0", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-command-"));
  try {
    writeTypecheckFixture(root, "node --check src/index.ts", "export const ok = true;\n");

    const result = checkTypecheckCommand(root);

    assert.deepEqual(result, {
      command: "ai-agent check:typecheck",
      status: "passed",
      typecheck: {
        schemaVersion: "typecheck-command-check.v1",
        scriptName: "typecheck",
        configuredCommand: "node --check src/index.ts",
        invokedCommand: "node --check src/index.ts",
        exitCode: 0,
        exitsWithCodeZero: true,
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command entry returns stable observable JSON output", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-entry-"));
  try {
    writeTypecheckFixture(root, "node --check src/index.ts", "export function stable() { return 1; }\n");

    const result = executeCheckTypecheckCommand(root);
    const output = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(output.command, "ai-agent check:typecheck");
    assert.equal(output.status, "passed");
    assert.equal(output.typecheck.configuredCommand, "node --check src/index.ts");
    assert.equal(output.typecheck.invokedCommand, "node --check src/index.ts");
    assert.equal(output.typecheck.exitCode, 0);
    assert.equal(output.typecheck.exitsWithCodeZero, true);
    assert.equal(output.typecheck.artifactPath, join(root, TYPECHECK_CHECK_ARTIFACT_PATH));

    const artifact = JSON.parse(readFileSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH), "utf8"));
    assert.deepEqual(artifact, {
      schemaVersion: "typecheck-proof-artifact.v1",
      command: "ai-agent check:typecheck",
      status: "passed",
      typecheck: {
        scriptName: "typecheck",
        configuredCommand: "node --check src/index.ts",
        invokedCommand: "node --check src/index.ts",
        exitCode: 0,
        exitsWithCodeZero: true,
      },
      capturedResult: {
        exitCode: 0,
        stdout: {
          parseableJson: true,
          json: {
            command: "ai-agent check:typecheck",
            status: "passed",
            typecheck: {
              artifactPath: "<path>",
              configuredCommand: "node --check src/index.ts",
              exitCode: 0,
              exitsWithCodeZero: true,
              invokedCommand: "node --check src/index.ts",
              schemaVersion: "typecheck-command-check.v1",
              scriptName: "typecheck",
            },
          },
        },
        stderr: {
          parseableJson: false,
          text: "",
        },
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command resolution selects package scripts.typecheck when present", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-resolution-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          private: true,
          type: "module",
          scripts: {
            "check:typecheck": "node --check src/decoy.ts",
            typecheck: "node --check src/configured.ts",
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "configured.ts"), "export const selected = true;\n");
    writeFileSync(join(root, "src", "decoy.ts"), "const broken =\n");

    const result = executeCheckTypecheckCommand(root);
    const output = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(output.typecheck.configuredCommand, "node --check src/configured.ts");
    assert.equal(output.typecheck.invokedCommand, "node --check src/configured.ts");

    const artifact = JSON.parse(readFileSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH), "utf8"));
    assert.equal(artifact.typecheck.configuredCommand, "node --check src/configured.ts");
    assert.equal(artifact.typecheck.exitsWithCodeZero, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command entry reports a stable non-zero failure when configured typecheck fails", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-failing-entry-"));
  try {
    writeTypecheckFixture(root, "node --check src/index.js", "export const ok = true;\n");
    writeFileSync(join(root, "src", "index.js"), "const x =\n");

    const result = executeCheckTypecheckCommand(root);
    const output = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    assert.equal(output.command, "ai-agent check:typecheck");
    assert.equal(output.status, "failed");
    assert.equal(output.typecheck.configuredCommand, "node --check src/index.js");
    assert.equal(output.typecheck.invokedCommand, "node --check src/index.js");
    assert.equal(output.typecheck.exitCode, 1);
    assert.equal(output.typecheck.exitsWithCodeZero, false);
    assert.equal(output.typecheck.artifactPath, join(root, TYPECHECK_CHECK_ARTIFACT_PATH));

    const artifact = JSON.parse(readFileSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH), "utf8"));
    assert.equal(artifact.schemaVersion, "typecheck-proof-artifact.v1");
    assert.equal(artifact.command, "ai-agent check:typecheck");
    assert.equal(artifact.status, "failed");
    assert.equal(artifact.typecheck.exitCode, 1);
    assert.equal(artifact.typecheck.exitsWithCodeZero, false);
    assert.equal(artifact.capturedResult.exitCode, 1);
    assert.equal(artifact.capturedResult.stdout.parseableJson, true);
    assert.equal(artifact.capturedResult.stdout.json.status, "failed");
    assert.equal(artifact.capturedResult.stdout.json.typecheck.artifactPath, "<path>");
    assert.equal(artifact.capturedResult.stderr.text, "");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command resolution selects the documented fallback when package scripts.typecheck is missing", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-fallback-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "ignored"), { recursive: true });
    writeFileSync(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          private: true,
          type: "module",
          scripts: {
            "check:typecheck": "node --check ignored/decoy.ts",
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "index.ts"), "export const ok = true;\n");
    writeFileSync(join(root, "scripts", "check.ts"), "export const scriptOk = true;\n");
    writeFileSync(join(root, "tests", "check.test.ts"), "export const testOk = true;\n");
    writeFileSync(join(root, "ignored", "decoy.ts"), "const broken =\n");

    const result = executeCheckTypecheckCommand(root);
    const output = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(output.command, "ai-agent check:typecheck");
    assert.equal(output.status, "passed");
    assert.equal(output.typecheck.configuredCommand, TYPECHECK_DOCUMENTED_FALLBACK_COMMAND);
    assert.equal(output.typecheck.invokedCommand, TYPECHECK_DOCUMENTED_FALLBACK_COMMAND);
    assert.equal(output.typecheck.exitCode, 0);
    assert.equal(output.typecheck.exitsWithCodeZero, true);

    const artifact = JSON.parse(readFileSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH), "utf8"));
    assert.equal(artifact.typecheck.configuredCommand, TYPECHECK_DOCUMENTED_FALLBACK_COMMAND);
    assert.equal(artifact.typecheck.exitsWithCodeZero, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command entry executes a controlled configured command stub", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-stub-success-"));
  try {
    mkdirSync(join(root, "scripts"), { recursive: true });
    writeFileSync(
      join(root, "scripts", "typecheck-pass.mjs"),
      [
        'import { writeFileSync } from "node:fs";',
        'writeFileSync("typecheck-sentinel.txt", "executed\\n");',
        "",
      ].join("\n"),
    );
    writeTypecheckFixture(root, "node scripts/typecheck-pass.mjs", "export const ok = true;\n");

    const result = executeCheckTypecheckCommand(root);
    const output = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(readFileSync(join(root, "typecheck-sentinel.txt"), "utf8"), "executed\n");
    assert.equal(output.status, "passed");
    assert.equal(output.typecheck.configuredCommand, "node scripts/typecheck-pass.mjs");
    assert.equal(output.typecheck.invokedCommand, "node scripts/typecheck-pass.mjs");
    assert.equal(output.typecheck.exitCode, 0);

    const artifact = JSON.parse(readFileSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH), "utf8"));
    assert.equal(artifact.typecheck.invokedCommand, "node scripts/typecheck-pass.mjs");
    assert.equal(artifact.typecheck.exitsWithCodeZero, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command entry propagates a controlled configured command stub failure", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-stub-failure-"));
  try {
    mkdirSync(join(root, "scripts"), { recursive: true });
    writeFileSync(
      join(root, "scripts", "typecheck-fail.mjs"),
      [
        'import { writeFileSync } from "node:fs";',
        'writeFileSync("typecheck-failure-sentinel.txt", "executed\\n");',
        "process.exitCode = 7;",
        "",
      ].join("\n"),
    );
    writeTypecheckFixture(root, "node scripts/typecheck-fail.mjs", "export const ok = true;\n");

    const result = executeCheckTypecheckCommand(root);
    const output = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    assert.equal(readFileSync(join(root, "typecheck-failure-sentinel.txt"), "utf8"), "executed\n");
    assert.equal(output.status, "failed");
    assert.equal(output.typecheck.configuredCommand, "node scripts/typecheck-fail.mjs");
    assert.equal(output.typecheck.invokedCommand, "node scripts/typecheck-fail.mjs");
    assert.equal(output.typecheck.exitCode, 7);
    assert.equal(output.typecheck.exitsWithCodeZero, false);

    const artifact = JSON.parse(readFileSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH), "utf8"));
    assert.equal(artifact.status, "failed");
    assert.equal(artifact.typecheck.exitCode, 7);
    assert.equal(artifact.capturedResult.exitCode, 1);
    assert.equal(existsSync(join(root, "typecheck-failure-sentinel.txt")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command entry rejects unsupported command forms with a clear error", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-unsupported-entry-"));
  try {
    writeTypecheckFixture(root, "echo silently-passing", "export const ok = true;\n");

    const result = executeCheckTypecheckCommand(root);
    const error = JSON.parse(result.stderr);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stdout, "");
    assert.equal(error.error, "typecheck_command_check_failed");
    assert.equal(error.configuredCommand, "echo silently-passing");
    assert.match(error.message, /unsupported_typecheck_command_form/);
    assert.match(error.message, /must start with node/);
    assert.equal(existsSync(join(root, TYPECHECK_CHECK_ARTIFACT_PATH)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("typecheck command check throws instead of executing unsupported shell syntax", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-unsupported-shell-"));
  try {
    mkdirSync(join(root, "scripts"), { recursive: true });
    writeFileSync(
      join(root, "scripts", "typecheck-pass.mjs"),
      [
        'import { writeFileSync } from "node:fs";',
        'writeFileSync("unsupported-typecheck-sentinel.txt", "executed\\n");',
        "",
      ].join("\n"),
    );
    writeTypecheckFixture(
      root,
      "node scripts/typecheck-pass.mjs; echo unsupported-shell-continuation",
      "export const ok = true;\n",
    );

    assert.throws(
      () => checkTypecheckCommand(root),
      /unsupported_typecheck_command_form: scripts\.typecheck segment uses unsupported shell syntax/,
    );
    assert.equal(existsSync(join(root, "unsupported-typecheck-sentinel.txt")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function writeTypecheckFixture(root: string, typecheckScript: string, source: string): void {
  mkdirSync(join(root, "src"), { recursive: true });
  writeFileSync(
    join(root, "package.json"),
    `${JSON.stringify(
      {
        private: true,
        type: "module",
        scripts: {
          typecheck: typecheckScript,
        },
      },
      null,
      2,
    )}\n`,
  );
  writeFileSync(join(root, "src", "index.ts"), source);
}
