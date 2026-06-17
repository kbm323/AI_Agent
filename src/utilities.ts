/**
 * Shared utility functions for the AI_Agent meeting system.
 *
 * This module mirrors the Python ``shared.utilities`` module and provides
 * TypeScript-equivalent utilities that are importable from the documented
 * ``ai-agent`` package path.
 */

// ---------------------------------------------------------------------------
// validate_token
// ---------------------------------------------------------------------------

export interface TokenValidationResult {
  valid: boolean;
  reason: string;
}

/**
 * Validate a token string meets basic security requirements.
 *
 * Checks that the token is a non-empty string, meets the minimum
 * length, and does not contain whitespace-only content.
 */
export function validate_token(
  token: string,
  minLength: number = 8,
): TokenValidationResult {
  if (typeof token !== "string") {
    return { valid: false, reason: "token must be a string" };
  }
  if (!token.trim()) {
    return { valid: false, reason: "token must not be empty or whitespace-only" };
  }
  if (token.length < minLength) {
    return {
      valid: false,
      reason: `token must be at least ${minLength} characters (got ${token.length})`,
    };
  }
  return { valid: true, reason: "ok" };
}

// ---------------------------------------------------------------------------
// format_message
// ---------------------------------------------------------------------------

/**
 * Format a meeting message with metadata for display or logging.
 *
 * Produces a normalized multi-line string suitable for thread display,
 * log output, or storage. The output includes the role, optional round
 * number, kind, and content.
 */
export function format_message(
  role: string,
  content: string,
  kind: string = "meeting_turn",
  roundNum?: number,
): string {
  if (typeof role !== "string" || typeof content !== "string") {
    throw new TypeError("role and content must be strings");
  }
  if (!role.trim()) {
    throw new Error("role must not be empty or whitespace-only");
  }

  const header =
    roundNum !== undefined
      ? `[Round ${roundNum}] [${role}]`
      : `[${role}]`;
  return `${header} (${kind})\n${content}`;
}
