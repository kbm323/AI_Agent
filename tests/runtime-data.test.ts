import test from "node:test";
import assert from "node:assert/strict";
import {
  createRuntimeIdentifier,
  normalizeRuntimeIdentifier,
  normalizeRuntimeIdentifierField,
} from "../src/runtime-data.ts";

test("runtime identifiers are controlled by one factory helper", () => {
  const generated = createRuntimeIdentifier(() => "controlled-runtime-id-1");

  assert.equal(generated, "controlled-runtime-id-1");
  assert.throws(
    () => createRuntimeIdentifier(() => "   "),
    /runtime identifier factory must return a non-empty string/,
  );
});

test("random ID-dependent runtime values are normalized by runtime-data helpers", () => {
  assert.equal(normalizeRuntimeIdentifier("run:0123456789abcdef"), "<execution-id>");
  assert.equal(normalizeRuntimeIdentifier("request:fedcba9876543210"), "<request-id>");
  assert.equal(normalizeRuntimeIdentifier("stable-task-id"), "stable-task-id");
  assert.equal(normalizeRuntimeIdentifierField("executionId", "run-a"), "<execution-id>");
  assert.equal(normalizeRuntimeIdentifierField("inputIdentifier", "capture-a"), "<input-identifier>");
  assert.equal(normalizeRuntimeIdentifierField("threadId", "thread-a"), "<thread-id>");
  assert.equal(normalizeRuntimeIdentifierField("taskId", "task-a"), "task-a");
});
