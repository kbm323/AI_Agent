from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen


DISCORD_CURRENT_USER_URL = "https://discord.com/api/v10/users/@me"
PROFILE_ROLES: dict[str, str] = {
    "aicompanyassistant": "비서",
    "aicompanyceo": "대표",
    "aicompanycontent": "콘텐츠팀장",
    "aicompanyart": "아트팀장",
    "aicompanytech": "기술팀장",
    "aicompanymarketing": "마케팅팀장",
    "aicompanyquality": "품질관리팀장",
}

HttpGet = Callable[..., Mapping[str, str]]


def _load_discord_bot_token(env_path: Path) -> str:
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        key, separator, value = raw_line.partition("=")
        if separator and key.strip() == "DISCORD_BOT_TOKEN":
            return value.strip()
    return ""


def _default_http_get(url: str, *, headers: Mapping[str, str]) -> Mapping[str, str]:
    request = Request(url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=15) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary_file:
        json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, path)


def sync_bot_identities(
    *,
    output_path: Path = Path("runtime/discord_bot_identities.json"),
    profile_root: Path | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, bool | int | str]:
    profile_root = profile_root or Path.home() / ".hermes" / "profiles"
    http_get = http_get or _default_http_get
    identities: dict[str, dict[str, str]] = {}

    for profile, role in PROFILE_ROLES.items():
        token = _load_discord_bot_token(profile_root / profile / ".env")
        if not token:
            raise RuntimeError(f"missing Discord bot token for profile: {profile}")
        response = http_get(
            DISCORD_CURRENT_USER_URL,
            headers={"Authorization": f"Bot {token}"},
        )
        discord_user_id = str(response.get("id", "")).strip()
        if not discord_user_id:
            raise RuntimeError(f"Discord identity lookup failed for profile: {profile}")
        identities[discord_user_id] = {"role": role, "hermes_profile": profile}

    _atomic_write_json(output_path, identities)
    return {"ok": True, "identity_count": len(identities), "path": str(output_path)}


def main() -> int:
    print(json.dumps(sync_bot_identities(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
