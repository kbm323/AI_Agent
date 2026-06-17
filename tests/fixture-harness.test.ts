import test from "node:test";
import assert from "node:assert/strict";
import { executeCheckFixtureHarnessCommand } from "../scripts/check-fixture-harness.ts";

test("fixture harness repeatedly captures deterministic observable dry-run output", async () => {
  const result = await executeCheckFixtureHarnessCommand();

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.command, "ai-agent check-fixture-harness");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.harness.schemaVersion, "fixture-harness.v1");
  assert.equal(parsed.harness.fixturePath, "tests/fixtures/dry-run-harness-fixtures.json");
  assert.deepEqual(
    parsed.harness.cases.map((fixtureCase: { name: string }) => fixtureCase.name),
    ["clear_request_from_file", "ambiguous_request_inline", "invalid_input_inline"],
  );

  for (const fixtureCase of parsed.harness.cases) {
    assert.equal(fixtureCase.invocation, "api");
    assert.equal(fixtureCase.deterministic, true);
    assert.equal(fixtureCase.normalizedEqual, true);
    assert.equal(fixtureCase.stdoutEqual, true);
    assert.equal(fixtureCase.stderrEqual, true);
    assert.equal(fixtureCase.exitCodesEqual, true);
    assert.equal(fixtureCase.captures.length, fixtureCase.repetitions);
    assert.equal(
      new Set(fixtureCase.captures.map((capture: { normalizedSha256: string }) => capture.normalizedSha256)).size,
      1,
    );
    assert.equal(new Set(fixtureCase.captures.map((capture: { stdoutSha256: string }) => capture.stdoutSha256)).size, 1);
    assert.equal(new Set(fixtureCase.captures.map((capture: { stderrSha256: string }) => capture.stderrSha256)).size, 1);
  }

  const clearCase = parsed.harness.cases[0];
  assert.equal(clearCase.expected.jsonStatus, "finalized");
  assert.equal(clearCase.captures[0].jsonStatus, "finalized");
  assert.equal(clearCase.captures[0].stream, "stdout");
  assert.equal(clearCase.captures[0].exitCode, 0);

  const ambiguousCase = parsed.harness.cases[1];
  assert.equal(ambiguousCase.expected.jsonStatus, "waiting_for_user");
  assert.equal(ambiguousCase.captures[0].jsonStatus, "waiting_for_user");

  const invalidCase = parsed.harness.cases[2];
  assert.equal(invalidCase.expected.jsonError, "invalid_input");
  assert.equal(invalidCase.captures[0].jsonError, "invalid_input");
  assert.equal(invalidCase.captures[0].stream, "stderr");
  assert.equal(invalidCase.captures[0].exitCode, 2);
});
