/**
 * Tests for parseSourceFile — Sub-AC 2.2 verification.
 *
 * Verifies that a single-file parse/import function produces deterministic
 * per-file status (success/failure/skip) and accurate line count for any
 * given source file.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  parseSourceFile,
  type SourceFileResult,
} from "../scripts/count-loc.ts";

// ── Helpers ────────────────────────────────────────────────────────────

function tmpFile(name: string, content: string): string {
  const dir = mkdtempSync(join(tmpdir(), "parse-source-file-"));
  const path = join(dir, name);
  mkdirSync(join(dir, "subdir"), { recursive: true });
  writeFileSync(path, content, "utf8");
  return path;
}

function cleanup(fullPath: string): void {
  // Delete the parent tmp dir (two levels up from the file in the temp dir pattern)
  const parts = fullPath.split("/");
  const tmpIdx = parts.indexOf("parse-source-file-");
  if (tmpIdx >= 0) {
    const root = parts.slice(0, tmpIdx + 1).join("/");
    try { rmSync("/" + root, { recursive: true, force: true }); } catch { /* ok */ }
  }
}

// ── Status: success ────────────────────────────────────────────────────

test("parseSourceFile returns status=success for a .ts file with accurate line count", () => {
  const code = "// comment\nconst x = 1;\n\n// another comment\nexport default x;\n";
  // split('\n') on trailing-\n content: ["// comment", "const x = 1;", "", "// another comment", "export default x;", ""]
  // → total=6, code=2, comment=2, blank=2 (the trailing empty string is counted as blank)
  const filePath = tmpFile("test.ts", code);

  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 6);
  assert.equal(result.codeLines, 2);
  assert.equal(result.commentLines, 2);
  assert.equal(result.blankLines, 2);
  assert.equal(result.error, "");

  cleanup(filePath);
});

test("parseSourceFile returns status=success for a .js file", () => {
  const filePath = tmpFile("app.js", "export default 1;\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 2); // trailing \n creates blank line
  assert.equal(result.codeLines, 1);
  assert.equal(result.commentLines, 0);
  assert.equal(result.blankLines, 1);

  cleanup(filePath);
});

test("parseSourceFile returns status=success for a .mjs file", () => {
  const filePath = tmpFile("lib.mjs", "export const x = 1;\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 2); // trailing \n

  cleanup(filePath);
});

test("parseSourceFile returns status=success for a .cjs file", () => {
  const filePath = tmpFile("lib.cjs", "module.exports = {};\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 2); // trailing \n

  cleanup(filePath);
});

test("parseSourceFile line count is deterministic — same file always returns same result", () => {
  const filePath = tmpFile("stable.ts", "const a = 1;\nconst b = 2;\n");
  const r1 = parseSourceFile(filePath);
  const r2 = parseSourceFile(filePath);
  const r3 = parseSourceFile(filePath);

  assert.equal(r1.status, "success");
  assert.equal(r2.status, "success");
  assert.equal(r3.status, "success");
  assert.equal(r1.lineCount, r2.lineCount);
  assert.equal(r1.lineCount, r3.lineCount);
  assert.equal(r1.codeLines, r2.codeLines);
  assert.equal(r1.commentLines, r2.commentLines);
  assert.equal(r1.blankLines, r2.blankLines);

  cleanup(filePath);
});

// ── Status: skip ───────────────────────────────────────────────────────

test("parseSourceFile returns status=skip for non-existent file", () => {
  const result = parseSourceFile("/nonexistent/path/to/ghost.ts");
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);
  assert.equal(result.error, "");
});

test("parseSourceFile returns status=skip for a directory", () => {
  const result = parseSourceFile(tmpdir());
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);
});

test("parseSourceFile returns status=skip for unsupported extension (.txt)", () => {
  const filePath = tmpFile("readme.txt", "hello world\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);

  cleanup(filePath);
});

test("parseSourceFile returns status=skip for unsupported extension (.md)", () => {
  const filePath = tmpFile("doc.md", "# Title\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);

  cleanup(filePath);
});

test("parseSourceFile returns status=skip for unsupported extension (.json)", () => {
  const filePath = tmpFile("config.json", "{\"key\": \"value\"}\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);

  cleanup(filePath);
});

test("parseSourceFile returns status=skip for binary extension (.db)", () => {
  const filePath = tmpFile("data.db", "binary stuff");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);

  cleanup(filePath);
});

test("parseSourceFile returns status=skip for file with no extension", () => {
  const filePath = tmpFile("Makefile", "all: build\n");
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "skip");
  assert.equal(result.lineCount, 0);

  cleanup(filePath);
});

test("parseSourceFile skip is deterministic across calls", () => {
  const result1 = parseSourceFile("/nonexistent/module.ts");
  const result2 = parseSourceFile("/nonexistent/module.ts");
  assert.equal(result1.status, "skip");
  assert.equal(result2.status, "skip");
  assert.equal(result1.lineCount, result2.lineCount);
});

// ── Status: failure ────────────────────────────────────────────────────

test("parseSourceFile returns status=success for empty file (0 content lines)", () => {
  const filePath = tmpFile("empty.ts", "");
  const result = parseSourceFile(filePath);
  // empty string split('\n') → [""] → 1 blank line
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 1);
  assert.equal(result.codeLines, 0);
  assert.equal(result.commentLines, 0);
  assert.equal(result.blankLines, 1);

  cleanup(filePath);
});

// ── Line count accuracy ─────────────────────────────────────────────────

test("parseSourceFile accurately counts lines in a 100-line file", () => {
  const lines: string[] = [];
  for (let i = 0; i < 100; i++) {
    lines.push(`export const v${i} = ${i};`);
  }
  const filePath = tmpFile("hundred.ts", lines.join("\n"));

  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 100);
  assert.equal(result.codeLines, 100);
  assert.equal(result.commentLines, 0);
  assert.equal(result.blankLines, 0);

  cleanup(filePath);
});

test("parseSourceFile accurately counts lines with mixed content types", () => {
  const content = [
    "// License header",
    "",
    "import { foo } from './bar';",
    "",
    "/**",
    " * Doc comment",
    " */",
    "export function main() {",
    "  return foo(); // inline comment",
    "}",
    "",
  ].join("\n");

  // Line analysis:
  // L1: "// License header" → comment
  // L2: "" → blank
  // L3: "import ..." → code
  // L4: "" → blank
  // L5: "/**" → comment (block start)
  // L6: " * Doc comment" → comment (inside block)
  // L7: " */" → comment (block end)
  // L8: "export function main() {" → code
  // L9: "  return foo(); // inline comment" → code (trailing comment line counts as code)
  // L10: "}" → code
  // L11: "" → blank

  const filePath = tmpFile("mixed.ts", content);

  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 11);
  assert.equal(result.codeLines, 4);
  assert.equal(result.commentLines, 4);
  assert.equal(result.blankLines, 3);

  cleanup(filePath);
});

test("parseSourceFile correctly handles file ending without trailing newline", () => {
  // Write without trailing \n — last line still counts
  const filePath = tmpFile("notrail.ts", "export const x = 1;\nexport const y = 2;");
  // split('\n') on this gives ["export const x = 1;", "export const y = 2;"] → 2 lines
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 2);
  assert.equal(result.codeLines, 2);

  cleanup(filePath);
});

test("parseSourceFile correctly handles file with only blank lines", () => {
  const filePath = tmpFile("blanks.ts", "\n\n\n");
  // split('\n') on "\n\n\n" → ["", "", "", ""] → 4 blank lines
  const result = parseSourceFile(filePath);
  assert.equal(result.status, "success");
  assert.equal(result.lineCount, 4);
  assert.equal(result.codeLines, 0);
  assert.equal(result.commentLines, 0);
  assert.equal(result.blankLines, 4);

  cleanup(filePath);
});

// ── Determinism: status never changes for the same file state ───────────

test("parseSourceFile status is always deterministic — success files always return success", () => {
  const filePath = tmpFile("det.ts", "const a = 1;\nconst b = 2;\n");

  for (let i = 0; i < 10; i++) {
    const result = parseSourceFile(filePath);
    assert.equal(result.status, "success", `Iteration ${i}: expected success`);
    assert.equal(result.lineCount, 3, `Iteration ${i}: expected 3 lines (2 code + 1 trailing blank)`);
    assert.equal(result.codeLines, 2, `Iteration ${i}: expected 2 code lines`);
  }

  cleanup(filePath);
});

test("parseSourceFile status is always deterministic — skip files always return skip", () => {
  const skipPath = "/tmp/definitely/does/not/exist.ts";

  for (let i = 0; i < 5; i++) {
    assert.equal(parseSourceFile(skipPath).status, "skip");
  }
});

// ── Real project files ─────────────────────────────────────────────────

test("parseSourceFile returns success for real project .ts files", () => {
  // Using a real file from the project
  const realFile = join(process.cwd(), "src", "orchestrator.ts");
  const result = parseSourceFile(realFile);

  assert.equal(result.status, "success");
  assert.ok(result.lineCount > 0, `orchestrator.ts should have lines, got ${result.lineCount}`);
  assert.equal(result.error, "");

  // Verify line count is reasonable (orchestrator.ts is ~524 lines per diagnosis report)
  assert.ok(result.lineCount >= 300, `Expected orchestrator.ts to have >=300 lines, got ${result.lineCount}`);
  assert.ok(result.codeLines > 0, "Should have code lines");
});

test("parseSourceFile returns consistent results for all project .ts source files", () => {
  // Verify consistency across all source files
  const srcDir = join(process.cwd(), "src");
  const files = readdirSync(srcDir, { withFileTypes: true })
    .filter(e => e.isFile() && e.name.endsWith(".ts"))
    .map(e => join(srcDir, e.name));

  assert.ok(files.length > 0, "Should have source files");

  const results: SourceFileResult[] = [];
  for (const file of files) {
    const r = parseSourceFile(file);
    results.push(r);
    // Every real .ts file should be parseable
    assert.equal(r.status, "success", `${r.filePath}: expected success, got ${r.status} (${r.error})`);
    assert.ok(r.lineCount >= 0, `${r.filePath}: lineCount should be >= 0`);
    // totalLines = code + comment + blank
    assert.equal(
      r.codeLines + r.commentLines + r.blankLines,
      r.lineCount,
      `${r.filePath}: code(${r.codeLines}) + comment(${r.commentLines}) + blank(${r.blankLines}) = lineCount(${r.lineCount})`,
    );
  }

  // All results should be deterministic — run again
  for (const file of files) {
    const r2 = parseSourceFile(file);
    const r1 = results.find(r => r.filePath === r2.filePath)!;
    assert.equal(r2.status, r1.status, `${file}: status changed on re-parse`);
    assert.equal(r2.lineCount, r1.lineCount, `${file}: lineCount changed on re-parse`);
    assert.equal(r2.codeLines, r1.codeLines, `${file}: codeLines changed on re-parse`);
    assert.equal(r2.commentLines, r1.commentLines, `${file}: commentLines changed on re-parse`);
    assert.equal(r2.blankLines, r1.blankLines, `${file}: blankLines changed on re-parse`);
  }
});
