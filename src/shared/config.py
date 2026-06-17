"""Configuration management for the AI_Agent shared infrastructure.

Provides dataclasses and default values for compression settings,
token budget parameters, and general shared configuration.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def load_config(path: Optional[str | Path] = None) -> "SharedConfig":
    """Load configuration from a JSON file or return the default configuration.

    When ``path`` is provided and the file exists, reads the JSON file and
    merges its values on top of the default configuration. Missing keys
    in the file fall back to the defaults.

    When ``path`` is omitted or the file does not exist, the default
    configuration is returned unchanged.

    Configuration file format::

        {
          "compression": {
            "max_visible_summary_chars": 1200,
            "max_compressed_summary_chars": 240,
            "max_feedback_summary_chars": 160
          },
          "token_budget": {
            "target_minimum_savings_percent": 40,
            "target_maximum_savings_percent": 50,
            "characters_per_token": 3.85
          },
          "schema_version": "shared-infrastructure.v1"
        }

    Args:
        path: Optional path to a JSON configuration file.
              If ``None`` (default), returns the default config immediately.

    Returns:
        A ``SharedConfig`` instance populated with values from the file
        (when present) or the built-in defaults otherwise.

    Raises:
        json.JSONDecodeError: If the file is not valid JSON.
        OSError: If the file cannot be read for reasons other than
                 non-existence.
    """
    if path is None:
        return default_config

    resolved = Path(os.path.expandvars(os.path.expanduser(str(path))))
    if not resolved.exists():
        return default_config

    with open(resolved, encoding="utf-8") as f:
        raw = json.load(f)

    compression_raw = raw.get("compression", {})
    token_budget_raw = raw.get("token_budget", {})

    return SharedConfig(
        compression=CompressionConfig(
            max_visible_summary_chars=compression_raw.get(
                "max_visible_summary_chars",
                default_config.compression.max_visible_summary_chars,
            ),
            max_compressed_summary_chars=compression_raw.get(
                "max_compressed_summary_chars",
                default_config.compression.max_compressed_summary_chars,
            ),
            max_feedback_summary_chars=compression_raw.get(
                "max_feedback_summary_chars",
                default_config.compression.max_feedback_summary_chars,
            ),
        ),
        token_budget=TokenBudgetConfig(
            target_minimum_savings_percent=token_budget_raw.get(
                "target_minimum_savings_percent",
                default_config.token_budget.target_minimum_savings_percent,
            ),
            target_maximum_savings_percent=token_budget_raw.get(
                "target_maximum_savings_percent",
                default_config.token_budget.target_maximum_savings_percent,
            ),
            characters_per_token=token_budget_raw.get(
                "characters_per_token",
                default_config.token_budget.characters_per_token,
            ),
        ),
        schema_version=raw.get(
            "schema_version", default_config.schema_version
        ),
    )


@dataclass(frozen=True)
class CompressionConfig:
    """Configuration for context compression behaviour."""

    max_visible_summary_chars: int = 1200
    max_compressed_summary_chars: int = 240
    max_feedback_summary_chars: int = 160
    compress_message_kinds: tuple[str, ...] = ("meeting_turn",)
    drop_message_kinds: tuple[str, ...] = ("raw_prompt_echo", "scratchpad")


@dataclass(frozen=True)
class TokenBudgetConfig:
    """Configuration for token budget management."""

    target_minimum_savings_percent: int = 40
    target_maximum_savings_percent: int = 50
    token_estimation_method: str = "deterministic-local-estimate-v1"
    characters_per_token: float = 3.85


@dataclass(frozen=True)
class SharedConfig:
    """Top-level configuration for the shared infrastructure module."""

    compression: CompressionConfig = field(default_factory=CompressionConfig)
    token_budget: TokenBudgetConfig = field(default_factory=TokenBudgetConfig)
    schema_version: str = "shared-infrastructure.v1"


default_config = SharedConfig()
