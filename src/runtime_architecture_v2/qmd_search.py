"""Transport-neutral, fail-closed QMD command adapter."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from numbers import Real
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

_COLLECTION = "obsidian"
_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class QmdMatch:
    path: str
    snippet: str
    score: float


@dataclass(frozen=True)
class QmdSearchResult:
    ok: bool
    matches: tuple[QmdMatch, ...] = ()
    fallback: str = ""
    error: str = ""


@dataclass(frozen=True)
class QmdCommandResult:
    ok: bool
    error: str = ""


@dataclass(frozen=True)
class QmdRawResult:
    exit_code: int
    stdout: str
    stderr: str


class QmdRunner(Protocol):
    def __call__(self, argv: list[str], timeout_seconds: float) -> QmdRawResult: ...


class QmdClient:
    def __init__(self, *, runner: QmdRunner | None = None) -> None:
        self._runner = runner or _run_subprocess

    def update(self) -> QmdCommandResult:
        return self._run_command(["qmd", "update", "-c", _COLLECTION])

    def embed(self) -> QmdCommandResult:
        return self._run_command(["qmd", "embed", "-c", _COLLECTION])

    def query(self, query: str, *, limit: int = 5) -> QmdSearchResult:
        normalized = query.strip()
        if not normalized:
            raise ValueError("blank_query")
        if limit < 1:
            raise ValueError("invalid_limit")

        primary = self._query_command(
            ["qmd", "query", normalized, "--json", "-c", _COLLECTION, "-n", str(limit)],
            limit=limit,
        )
        if primary.ok:
            return primary
        return self._query_command(
            [
                "qmd",
                "search",
                normalized,
                "--json",
                "-c",
                _COLLECTION,
                "-n",
                str(limit),
            ],
            fallback="bm25",
            limit=limit,
        )

    def _run_command(self, argv: list[str]) -> QmdCommandResult:
        result = self._run(argv)
        if isinstance(result, QmdSearchResult):
            return QmdCommandResult(ok=False, error=result.error)
        if result.exit_code != 0:
            return QmdCommandResult(ok=False, error="command_failed")
        return QmdCommandResult(ok=True)

    def _query_command(
        self, argv: list[str], *, fallback: str = "", limit: int
    ) -> QmdSearchResult:
        result = self._run(argv)
        if isinstance(result, QmdSearchResult):
            return result
        return self._parse_raw(result, fallback=fallback, limit=limit)

    def _run(self, argv: list[str]) -> QmdRawResult | QmdSearchResult:
        try:
            return self._runner(argv, _TIMEOUT_SECONDS)
        except FileNotFoundError:
            return QmdSearchResult(ok=False, error="executable_not_found")
        except subprocess.TimeoutExpired:
            return QmdSearchResult(ok=False, error="timeout")
        except OSError:
            return QmdSearchResult(ok=False, error="command_failed")

    def _parse_raw(
        self, result: QmdRawResult, *, fallback: str = "", limit: int = 5
    ) -> QmdSearchResult:
        if result.exit_code != 0:
            return QmdSearchResult(ok=False, error="command_failed")
        return _parse_search_result(result.stdout, fallback=fallback, limit=limit)


def _run_subprocess(argv: list[str], timeout_seconds: float) -> QmdRawResult:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    return QmdRawResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_search_result(
    stdout: str, *, fallback: str = "", limit: int = 5
) -> QmdSearchResult:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return QmdSearchResult(ok=False, error="malformed_result")

    records = _records_from_payload(payload)
    if records is None:
        return QmdSearchResult(ok=False, error="malformed_result")

    matches: list[QmdMatch] = []
    try:
        for record in records[:limit]:
            if not isinstance(record, dict):
                raise ValueError
            path = _normalize_path(record.get("file"))
            snippet = record.get("snippet", "")
            if not isinstance(snippet, str):
                raise ValueError
            matches.append(
                QmdMatch(
                    path=path,
                    snippet=snippet,
                    score=_parse_score(record.get("score")),
                )
            )
    except (OverflowError, TypeError, ValueError):
        return QmdSearchResult(ok=False, error="unsafe_result")
    return QmdSearchResult(ok=True, matches=tuple(matches), fallback=fallback)


def _records_from_payload(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    return None


def _normalize_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    if value.startswith("qmd://"):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "qmd"
            or parsed.netloc != _COLLECTION
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ValueError
        value = unquote(parsed.path[1:])
    if "\\" in value or any(_is_control_character(character) for character in value):
        raise ValueError
    path = PurePosixPath(value)
    parts = path.parts
    if (
        path.is_absolute()
        or not parts
        or ":" in parts[0]
        or ".." in parts
    ):
        raise ValueError
    return path.as_posix()


def _parse_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError
    score = float(value)
    if not math.isfinite(score):
        raise ValueError
    return score


def _is_control_character(character: str) -> bool:
    codepoint = ord(character)
    return codepoint <= 0x1F or 0x7F <= codepoint <= 0x9F
