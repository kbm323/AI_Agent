"""Shared utility functions for text processing and token management.

These utilities are used across the shared infrastructure module and
mirror the TypeScript implementations in policies.ts and related files.
"""


def summarize_for_thread(content: str, max_chars: int = 1200) -> str:
    """Truncate and normalize content for thread display.

    Normalizes consecutive newlines and truncates to max_chars
    when the content exceeds the limit.

    Args:
        content: The text to summarize.
        max_chars: Maximum character count before truncation.

    Returns:
        Summarized string suitable for thread display.
    """
    import re

    normalized = re.sub(r"\n{3,}", "\n\n", content.strip())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 1]}\u2026"


def contains_exact_text(haystack: str, needle: str) -> bool:
    """Check if needle is a non-empty substring of haystack.

    Args:
        haystack: The text to search within.
        needle: The text to search for.

    Returns:
        True if needle is non-empty and found in haystack.
    """
    return len(needle) > 0 and needle in haystack


def fingerprint_text(text: str) -> dict[str, object]:
    """Create a lightweight fingerprint of text content.

    Returns a dict with length and prefix for identification
    without exposing the full content.

    Args:
        text: The text to fingerprint.

    Returns:
        Dict with 'length' (int) and 'prefix' (str, first 24 chars).
    """
    return {
        "length": len(text),
        "prefix": text[:24],
    }


def format_list(values: list[str]) -> str:
    """Format a list of strings as a pipe-separated display value.

    Args:
        values: List of strings to format.

    Returns:
        Pipe-separated string or 'none' if the list is empty.
    """
    if not values:
        return "none"
    return " | ".join(values)


def resolve_token_value(value: str | list[str], label: str = "value") -> int:
    """Resolve a token accounting value (string or string list) to a character count.

    Args:
        value: A single string or list of strings.
        label: Label for error messages.

    Returns:
        Total character count.

    Raises:
        TypeError: If the value type is not string or list of strings.
    """
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return sum(len(item) for item in value)
    raise TypeError(
        f"{label} must be a string or list of strings, got {type(value).__name__}"
    )


def summarize_list(values: list[str], max_chars: int = 160) -> list[str]:
    """Summarize each string in a list, filtering out empty results.

    Args:
        values: List of strings to summarize.
        max_chars: Maximum characters per summary.

    Returns:
        List of non-empty summarized strings.
    """
    return [
        summary
        for value in values
        if (summary := summarize_for_thread(value, max_chars))
    ]


def validate_token(token: str, min_length: int = 8) -> dict[str, object]:
    """Validate a token string meets basic security requirements.

    Checks that the token is a non-empty string, meets the minimum
    length, and does not contain whitespace-only content.

    Args:
        token: The token string to validate.
        min_length: Minimum acceptable token length (default: 8).

    Returns:
        Dict with 'valid' (bool) and 'reason' (str) keys.
        When ``valid`` is ``False``, ``reason`` explains why.
    """
    if not isinstance(token, str):
        return {"valid": False, "reason": "token must be a string"}
    if not token.strip():
        return {"valid": False, "reason": "token must not be empty or whitespace-only"}
    if len(token) < min_length:
        return {
            "valid": False,
            "reason": f"token must be at least {min_length} characters (got {len(token)})",
        }
    return {"valid": True, "reason": "ok"}


def format_message(
    role: str,
    content: str,
    kind: str = "meeting_turn",
    round_num: int | None = None,
) -> str:
    """Format a meeting message with metadata for display or logging.

    Produces a normalized multi-line string suitable for thread display,
    log output, or storage. The output includes the role, optional round
    number, kind, and content.

    Args:
        role: The agent role (e.g., 'openclaw-owner', 'hermes-reviewer').
        content: The message body text.
        kind: The turn kind (default: 'meeting_turn').
        round_num: Optional meeting round number.

    Returns:
        A formatted message string with role, kind, round (when provided),
        and content separated by newlines.

    Raises:
        TypeError: If ``role`` or ``content`` is not a string.
        ValueError: If ``role`` is empty or whitespace-only.
    """
    if not isinstance(role, str) or not isinstance(content, str):
        raise TypeError("role and content must be strings")
    if not role.strip():
        raise ValueError("role must not be empty or whitespace-only")

    header = f"[{role}]" if round_num is None else f"[Round {round_num}] [{role}]"
    return f"{header} ({kind})\n{content}"
