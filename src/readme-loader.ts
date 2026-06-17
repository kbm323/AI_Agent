import { readFileSync, existsSync, statSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Reads a README file from the given path and returns its raw text content.
 *
 * @param filePath — Absolute or relative path to the README file.
 * @returns The raw text content of the file.
 * @throws If the file does not exist or cannot be read.
 */
export function loadReadme(filePath: string): string {
  const resolvedPath = resolve(filePath);

  if (!existsSync(resolvedPath)) {
    throw new Error(`README file not found at: ${resolvedPath}`);
  }

  if (!statSync(resolvedPath).isFile()) {
    throw new Error(`README file not found at: ${resolvedPath}`);
  }

  try {
    return readFileSync(resolvedPath, "utf-8");
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`Failed to read README file at ${resolvedPath}: ${message}`);
  }
}
