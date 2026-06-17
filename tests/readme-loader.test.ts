import test from "node:test";
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadReadme } from "../src/readme-loader.ts";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const fixturePath = resolve(__dirname, "fixtures", "sample-readme.md");
const missingPath = resolve(__dirname, "fixtures", "does-not-exist.md");

test("loadReadme returns raw text content from a valid README file", () => {
  const content = loadReadme(fixturePath);

  assert.equal(typeof content, "string");
  assert.ok(content.length > 0, "content should not be empty");
  assert.ok(
    content.includes("# Sample README — Test Fixture"),
    "content should contain the fixture title",
  );
  assert.ok(
    content.includes("## Features"),
    "content should contain the Features section",
  );
});

test("loadReadme preserves exact fixture content", () => {
  const content = loadReadme(fixturePath);

  // Verify the full known content is present
  assert.ok(content.includes("Feature A: Request analysis and task decomposition"));
  assert.ok(content.includes("Feature B: Role-based routing to specialized agents"));
  assert.ok(content.includes("Feature C: Meeting loop with execution and review personas"));
  assert.ok(content.includes("npm test"));
  assert.ok(content.includes("npm run build"));
});

test("loadReadme returns content with original whitespace and line breaks", () => {
  const content = loadReadme(fixturePath);
  const lines = content.split("\n");

  assert.ok(lines.length >= 10, "fixture should have at least 10 lines");
  assert.equal(lines[0], "# Sample README — Test Fixture");
});

test("loadReadme throws when file does not exist", () => {
  assert.throws(
    () => loadReadme(missingPath),
    /README file not found/,
  );
});

test("loadReadme throws with stable non-zero failure for invalid input", () => {
  // Empty string path should fail
  assert.throws(
    () => loadReadme(""),
    /README file not found/,
  );

  // Path to a directory should fail (statSync.isFile() returns false)
  assert.throws(
    () => loadReadme(__dirname),
    /README file not found/,
  );
});
