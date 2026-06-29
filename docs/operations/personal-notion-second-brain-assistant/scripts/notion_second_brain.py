#!/usr/bin/env python3
"""Safe Notion Second Brain helper for the aicompanyassistant profile.

Defaults to proposal/dry-run behavior. Live writes require --confirm.
Never prints token values.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

NOTION_VERSION = "2025-09-03"
HERMES_PROFILE = "aicompanyassistant"
CORE_DATA_SOURCES = {
    "areas": "영역 · 자원",
    "notes": "노트",
    "tasks": "할 일",
    "projects": "프로젝트",
    "goals": "목표",
}

DEFAULT_AREA_HINTS = {
    "assistant": ["나 매뉴얼", "가족", "재무관리", "운동", "건강", "자기 개발 · 계발", "노션"],
    "ceo": ["AI", "자동화", "세컨드 프로젝트"],
    "content": ["유튜브", "영상", "AI", "세컨드 프로젝트"],
    "art": ["3D", "영상", "AI"],
    "tech": ["AI", "자동화", "컴퓨터", "노션"],
    "marketing": ["유튜브", "영상", "세컨드 프로젝트", "자기 개발 · 계발"],
    "quality": ["AI", "자동화", "컴퓨터", "나 매뉴얼"],
}

KEYWORD_AREAS = {
    "AI": ["ai", "llm", "agent", "에이전트", "모델", "gpt", "qwen", "glm", "codex", "ai_agent"],
    "자동화": ["자동화", "봇", "bot", "workflow", "스크립트", "cron", "gateway", "hermes"],
    "컴퓨터": ["개발", "코드", "서버", "로그", "오류", "버그", "python", "node", "wsl", "환경"],
    "노션": ["notion", "노션", "second brain", "세컨드 브레인", "할 일", "노트"],
    "유튜브": ["youtube", "유튜브", "쇼츠", "shorts", "채널"],
    "영상": ["영상", "편집", "촬영", "렌더", "썸네일", "vfx"],
    "3D": ["3d", "블렌더", "unreal", "언리얼", "모델링", "캐릭터", "텍스처"],
    "재무관리": ["돈", "재무", "수입", "지출", "투자", "예산"],
    "운동": ["운동", "헬스", "러닝", "루틴"],
    "건강": ["건강", "병원", "검진", "약", "증상"],
    "가족": ["가족", "부모", "동생", "친척"],
    "여행": ["여행", "일본", "후쿠오카", "항공", "숙소"],
    "세컨드 프로젝트": ["부업", "사이드", "사업", "프로젝트", "런칭", "출시"],
    "나 매뉴얼": ["선호", "성향", "매뉴얼", "원칙", "기준", "습관"],
}


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in [Path.home() / ".hermes" / ".env", Path.home() / ".hermes" / "profiles" / HERMES_PROFILE / ".env"]:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


@dataclass
class NotionClient:
    token: str

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.notion.com/v1{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                payload["_http_status"] = resp.status
                return payload
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw}
            payload["_http_status"] = exc.code
            payload["_error"] = True
            return payload


def plain_text(rich: list[dict[str, Any]] | None) -> str:
    return "".join(item.get("plain_text", "") for item in rich or []).strip()


def page_title(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return plain_text(prop.get("title", []))
    return ""


def data_source_title(ds: dict[str, Any]) -> str:
    return plain_text(ds.get("title", []))


def get_client() -> NotionClient:
    env = load_env()
    token = env.get("NOTION_API_KEY") or env.get("NOTION_API_TOKEN")
    if not token:
        raise SystemExit("ERROR: NOTION_API_KEY / NOTION_API_TOKEN not found")
    return NotionClient(token=token)


def discover_data_sources(client: NotionClient) -> dict[str, dict[str, Any]]:
    response = client.request("POST", "/search", {"filter": {"property": "object", "value": "data_source"}, "page_size": 100})
    if response.get("_error"):
        raise SystemExit(f"ERROR: data source search failed status={response.get('_http_status')}")
    by_title = {data_source_title(ds): ds for ds in response.get("results", [])}
    resolved: dict[str, dict[str, Any]] = {}
    for alias, title in CORE_DATA_SOURCES.items():
        ds = by_title.get(title)
        if not ds:
            raise SystemExit(f"ERROR: required data source not found: {title}")
        full = client.request("GET", f"/data_sources/{ds['id']}")
        if full.get("_error"):
            raise SystemExit(f"ERROR: data source retrieve failed: {title} status={full.get('_http_status')}")
        resolved[alias] = full
    return resolved


def database_id_for(ds: dict[str, Any]) -> str:
    parent = ds.get("parent", {})
    if parent.get("type") == "database_id" and parent.get("database_id"):
        return parent["database_id"]
    raise SystemExit(f"ERROR: no database_id parent for data source {data_source_title(ds)}")


def query_all(client: NotionClient, data_source_id: str, body: dict[str, Any] | None = None, limit_pages: int = 20) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor = None
    for _ in range(limit_pages):
        payload = {"page_size": 100}
        if body:
            payload.update(body)
        if cursor:
            payload["start_cursor"] = cursor
        response = client.request("POST", f"/data_sources/{data_source_id}/query", payload)
        if response.get("_error"):
            raise SystemExit(f"ERROR: query failed status={response.get('_http_status')} body={response.get('message', '')}")
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return results


def title_filter(title: str) -> dict[str, Any]:
    return {"filter": {"property": "이름", "title": {"contains": title[:80]}}}


def relation_property(ds: dict[str, Any], target_title: str) -> str | None:
    for name, prop in ds.get("properties", {}).items():
        if prop.get("type") == "relation" and name == target_title:
            return name
    return None


def areas_by_title(client: NotionClient, sources: dict[str, dict[str, Any]]) -> dict[str, str]:
    rows = query_all(client, sources["areas"]["id"])
    return {page_title(row): row["id"] for row in rows if page_title(row)}


def suggest_area_titles(text: str, explicit: list[str], bot: str | None, known: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for title in explicit:
        if title in known and title not in candidates:
            candidates.append(title)
    lower = text.lower()
    for title, keywords in KEYWORD_AREAS.items():
        if title in known and any(keyword.lower() in lower for keyword in keywords) and title not in candidates:
            candidates.append(title)
    # Bot defaults are flexible priors, not hard relations. Only use a small
    # fallback when no explicit or keyword match exists.
    if not candidates and bot and bot in DEFAULT_AREA_HINTS:
        for title in DEFAULT_AREA_HINTS[bot][:3]:
            if title in known and title not in candidates:
                candidates.append(title)
    return candidates[:8]


def title_for_text(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    clean = strip_command_prefix(clean)
    if len(clean) <= 22:
        return clean or "무제 노트"
    return clean[:22].rstrip() + "…"


def strip_command_prefix(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    patterns = [
        r"^(비서야\s*)?노션에\s*(저장|추가|기록)해줘\s*[:：-]?\s*",
        r"^(비서야\s*)?노트로\s*(정리|저장|기록)해줘\s*[:：-]?\s*",
        r"^(비서야\s*)?아이디어로\s*(넣어|저장|기록)줘\s*[:：-]?\s*",
        r"^(비서야\s*)?할\s*일\s*(추가|기록)해줘\s*[:：-]?\s*",
        r"^(비서야\s*)?일정\s*(기록|추가)해줘\s*[:：-]?\s*",
        r"^(비서야\s*)?캘린더에\s*(넣어|추가|기록)줘\s*[:：-]?\s*",
        r"^(저장|추가|기록)해줘\s*[:：-]?\s*",
    ]
    for pat in patterns:
        clean = re.sub(pat, "", clean, flags=re.IGNORECASE)
    return clean.strip()


def build_note_body(text: str, areas: list[str], web_search: str | None = None, next_actions: list[str] | None = None) -> str:
    body_text = strip_command_prefix(text) or text.strip()
    lines = ["핵심", f"- {body_text}"]
    if areas:
        lines += ["", "연결", f"- 영역/자원: {', '.join(areas)}"]
    if next_actions:
        lines += ["", "다음 행동 후보"] + [f"- {item}" for item in next_actions]
    if web_search:
        lines += ["", "웹서치 필요", f"- {web_search}"]
    return "\n".join(lines).strip()


def similar_candidates(client: NotionClient, ds: dict[str, Any], title: str) -> list[dict[str, str]]:
    rows = query_all(client, ds["id"], title_filter(title), limit_pages=1)
    out = []
    for row in rows[:8]:
        out.append({"title": page_title(row), "id": row.get("id", ""), "url": row.get("url", "")})
    return out


def rich_text(content: str) -> list[dict[str, Any]]:
    # Notion rich_text content chunks max around 2000 chars.
    return [{"type": "text", "text": {"content": content[:1900]}}]


def note_properties(ds: dict[str, Any], title: str, body: str, area_ids: list[str], project_ids: list[str] | None = None, category: str | None = None, url: str | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {"이름": {"title": rich_text(title)}}
    if body and "아래부터 시스템" in ds.get("properties", {}):
        props["아래부터 시스템"] = {"rich_text": rich_text(body)}
    if area_ids and "영역 · 자원" in ds.get("properties", {}):
        props["영역 · 자원"] = {"relation": [{"id": rid} for rid in area_ids]}
    if project_ids and "프로젝트" in ds.get("properties", {}):
        props["프로젝트"] = {"relation": [{"id": rid} for rid in project_ids]}
    if category and "분류" in ds.get("properties", {}):
        props["분류"] = {"select": {"name": category}}
    if url and "URL" in ds.get("properties", {}):
        props["URL"] = {"url": url}
    return props


def task_properties(ds: dict[str, Any], title: str, clarify: str | None, date: str | None, contexts: list[str], project_ids: list[str] | None = None, note_ids: list[str] | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {"이름": {"title": rich_text(title)}}
    if clarify and "명료화" in ds.get("properties", {}):
        props["명료화"] = {"select": {"name": clarify}}
    if date and "날짜" in ds.get("properties", {}):
        props["날짜"] = {"date": {"start": date}}
    if contexts and "다음 행동 상황" in ds.get("properties", {}):
        props["다음 행동 상황"] = {"multi_select": [{"name": item} for item in contexts]}
    if project_ids and "프로젝트" in ds.get("properties", {}):
        props["프로젝트"] = {"relation": [{"id": rid} for rid in project_ids]}
    if note_ids and "노트" in ds.get("properties", {}):
        props["노트"] = {"relation": [{"id": rid} for rid in note_ids]}
    return props


def create_page(client: NotionClient, database_id: str, properties: dict[str, Any], confirm: bool) -> dict[str, Any]:
    payload = {"parent": {"database_id": database_id}, "properties": properties}
    if not confirm:
        return {"dry_run": True, "payload": payload}
    response = client.request("POST", "/pages", payload)
    return response


def cmd_inventory(args: argparse.Namespace) -> int:
    client = get_client()
    sources = discover_data_sources(client)
    known = areas_by_title(client, sources)
    summary = {alias: {"title": data_source_title(ds), "id_present": bool(ds.get("id")), "database_id_present": bool(ds.get("parent", {}).get("database_id"))} for alias, ds in sources.items()}
    print(json.dumps({"ok": True, "data_sources": summary, "area_resource_titles": sorted(known)}, ensure_ascii=False, indent=2))
    return 0


def cmd_propose_note(args: argparse.Namespace) -> int:
    client = get_client()
    sources = discover_data_sources(client)
    known = areas_by_title(client, sources)
    title = args.title or title_for_text(args.text)
    areas = suggest_area_titles(args.text + " " + title, args.areas or [], args.bot, known)
    dup = similar_candidates(client, sources["notes"], title)
    web = args.web_search or ("확인 후 실행 — 시장성/사례/구현 방법이 외부 정보에 의존하면 필요" if any(k in args.text.lower() for k in ["시장", "사례", "트렌드", "도구", "경쟁", "가능성"]) else "아니오")
    proposal = {
        "type": "note_proposal",
        "title": title,
        "areas": areas,
        "body": build_note_body(args.text, areas, web_search=web),
        "duplicate_candidates": dup,
        "write_requires": "create-note --confirm",
    }
    print(json.dumps(proposal, ensure_ascii=False, indent=2))
    return 0


def cmd_create_note(args: argparse.Namespace) -> int:
    client = get_client()
    sources = discover_data_sources(client)
    known = areas_by_title(client, sources)
    title = args.title or title_for_text(args.body or args.text or "")
    text = args.body or args.text or title
    areas = suggest_area_titles(text + " " + title, args.areas or [], args.bot, known)
    area_ids = [known[name] for name in areas if name in known]
    props = note_properties(sources["notes"], title, text, area_ids, category=args.category, url=args.url)
    result = create_page(client, database_id_for(sources["notes"]), props, args.confirm)
    print(json.dumps({"ok": not result.get("_error"), "result": result if result.get("dry_run") else {"http_status": result.get("_http_status"), "id": result.get("id"), "url": result.get("url")}}, ensure_ascii=False, indent=2))
    return 1 if result.get("_error") else 0


def cmd_propose_task(args: argparse.Namespace) -> int:
    client = get_client()
    sources = discover_data_sources(client)
    title = args.title or title_for_text(args.text)
    dup = similar_candidates(client, sources["tasks"], title)
    clarify = args.clarify or infer_clarify(args.text, args.date)
    proposal = {
        "type": "task_proposal",
        "title": title,
        "clarify": clarify,
        "date": args.date,
        "contexts": args.contexts or [],
        "duplicate_candidates": dup,
        "write_requires": "create-task --confirm",
    }
    print(json.dumps(proposal, ensure_ascii=False, indent=2))
    return 0


def infer_clarify(text: str, date: str | None) -> str | None:
    lower = text.lower()
    if any(word in lower for word in ["언젠가", "나중", "언젠가는"]):
        return "언젠가"
    if date and re.search(r"\d{1,2}:\d{2}|\d{1,2}시", text):
        return "일정"
    if date:
        return "다음행동"
    return None


def cmd_create_task(args: argparse.Namespace) -> int:
    client = get_client()
    sources = discover_data_sources(client)
    title = args.title or title_for_text(args.text or "")
    clarify = args.clarify or infer_clarify(args.text or title, args.date)
    props = task_properties(sources["tasks"], title, clarify, args.date, args.contexts or [])
    result = create_page(client, database_id_for(sources["tasks"]), props, args.confirm)
    print(json.dumps({"ok": not result.get("_error"), "result": result if result.get("dry_run") else {"http_status": result.get("_http_status"), "id": result.get("id"), "url": result.get("url")}}, ensure_ascii=False, indent=2))
    return 1 if result.get("_error") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe Notion Second Brain helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inventory", help="Verify data sources and list area/resource titles")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("propose-note", help="Plan a note without writing")
    p.add_argument("--text", required=True)
    p.add_argument("--title")
    p.add_argument("--areas", nargs="*")
    p.add_argument("--bot", choices=sorted(DEFAULT_AREA_HINTS))
    p.add_argument("--web-search")
    p.set_defaults(func=cmd_propose_note)

    p = sub.add_parser("create-note", help="Create a note; dry-run unless --confirm")
    p.add_argument("--text")
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--areas", nargs="*")
    p.add_argument("--bot", choices=sorted(DEFAULT_AREA_HINTS))
    p.add_argument("--category", choices=["중간 작업물", "나중에 보기", "레퍼런스"])
    p.add_argument("--url")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_create_note)

    p = sub.add_parser("propose-task", help="Plan a task without writing")
    p.add_argument("--text", required=True)
    p.add_argument("--title")
    p.add_argument("--clarify", choices=["다음행동", "일정", "언젠가", "위임"])
    p.add_argument("--date")
    p.add_argument("--contexts", nargs="*")
    p.set_defaults(func=cmd_propose_task)

    p = sub.add_parser("create-task", help="Create a task; dry-run unless --confirm")
    p.add_argument("--text")
    p.add_argument("--title")
    p.add_argument("--clarify", choices=["다음행동", "일정", "언젠가", "위임"])
    p.add_argument("--date")
    p.add_argument("--contexts", nargs="*")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_create_task)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
