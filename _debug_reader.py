"""Debug failing find_artifacts test"""
import sys
sys.path.insert(0, "/home/kbm/F:ai-projects/AI_Agent")

from src.gdrive_artifact_writer import (
    inject_drive_api, FOLDER_MIME_TYPE, DRIVE_API_BASE,
)
from src.gdrive_artifact_reader import find_artifacts
from src.gdrive_auth import GDriveToken
import time

class MockReadDriveAPI:
    def __init__(self):
        self._store = {}
        self._next_id = 10000
        self._call_log = []
        self._seed()

    def _new_id(self):
        self._next_id += 1
        return f"mock_file_{self._next_id}"

    def _add_entry(self, entry):
        fid = self._new_id()
        entry["id"] = fid
        self._store[fid] = {**entry}
        return entry

    def _add_file(self, entry, content=""):
        fid = self._new_id()
        entry["id"] = fid
        entry["_content"] = content
        self._store[fid] = {**entry}
        return entry

    def _seed(self):
        root = self._add_entry({"name": "Test_Meetings", "mimeType": FOLDER_MIME_TYPE, "parents": [], "trashed": False})
        root_id = root["id"]
        meeting = self._add_entry({"name": "meeting_20260610_abc123", "mimeType": FOLDER_MIME_TYPE, "parents": [root_id], "trashed": False})
        meeting_id = meeting["id"]
        tf = self._add_entry({"name": "transcripts", "mimeType": FOLDER_MIME_TYPE, "parents": [meeting_id], "trashed": False})
        t_id = tf["id"]
        self._add_file({"name": "round_1_transcript.md", "mimeType": "text/markdown", "parents": [t_id], "webViewLink": "", "trashed": False}, "#R1")
        self._add_file({"name": "round_1_ceo_transcript.md", "mimeType": "text/markdown", "parents": [t_id], "webViewLink": "", "trashed": False}, "#CEO")
        self._add_file({"name": "round_2_transcript.md", "mimeType": "text/markdown", "parents": [t_id], "webViewLink": "", "trashed": False}, "#R2")
        df = self._add_entry({"name": "decisions", "mimeType": FOLDER_MIME_TYPE, "parents": [meeting_id], "trashed": False})
        pf = self._add_entry({"name": "context_packets", "mimeType": FOLDER_MIME_TYPE, "parents": [meeting_id], "trashed": False})
        self._add_file({"name": "manifest.json", "mimeType": "application/json", "parents": [meeting_id], "trashed": False}, "{}")

    def _search(self, query_str):
        from urllib.parse import unquote
        q = unquote(query_str)
        print(f"  SEARCH decoded: {q}")
        results = list(self._store.values())
        parts = self._split_query(q)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if " in parents" in part:
                pid = part.split(" in parents")[0].strip().strip("'\"")
                results = [r for r in results if pid in r.get("parents", [])]
                continue
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key == "trashed":
                wt = val.lower() == "true"
                results = [r for r in results if bool(r.get("trashed", False)) == wt]
            else:
                results = [r for r in results if str(r.get(key, "")) == val]
        return results

    @staticmethod
    def _split_query(query_str):
        parts = []
        current = ""
        in_expr = 0
        i = 0
        while i < len(query_str):
            if in_expr == 0 and i + 4 < len(query_str) and query_str[i : i + 5] == " and ":
                parts.append(current)
                current = ""
                i += 5
                continue
            if query_str[i] == "(":
                in_expr += 1
            elif query_str[i] == ")":
                in_expr = max(0, in_expr - 1)
            current += query_str[i]
            i += 1
        if current:
            parts.append(current)
        return parts

    def handle(self, method, url, headers, body=None, timeout=10.0):
        self._call_log.append({"m": method, "u": url[:200]})
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return (401, {"error": {"message": "Unauthorized"}})
        if method == "GET" and "?alt=media" in url:
            file_id = url.split("/files/")[1].split("?")[0]
            entry = self._store.get(file_id)
            if entry is None:
                return (404, {"error": {"message": f"File not found: {file_id}"}})
            return (200, {"_content": entry.get("_content", "")})
        if method == "GET" and DRIVE_API_BASE in url and "/files" in url:
            query_str = ""
            if "?q=" in url:
                q_start = url.index("?q=") + 3
                q_end = url.index("&", q_start) if "&" in url[q_start:] else len(url)
                query_str = url[q_start:q_end]
            print(f"  LISTING - raw query: {query_str[:120]}")
            files = self._search(query_str)
            print(f"  Found {len(files)} files")
            return (200, {"files": files[:100]})
        msg = f"Unknown: {method} {url[:120]}"
        print(f"  FALLBACK: {msg}")
        return (404, {"error": {"message": msg}})


mock = MockReadDriveAPI()
inject_drive_api(mock.handle)

token = GDriveToken(
    access_token="t1", refresh_token="r1", token_type="Bearer",
    expires_at=time.time() + 3600, scope="test",
)

print("=== find_artifacts call ===")
results = find_artifacts(
    token=token,
    meeting_id="meeting_20260610_abc123",
    artifact_type="transcript",
)
print(f"\n=== Results: {len(results)} ===")
for r in results:
    print(f"  {r.file_name}")

print(f"\n=== Call log ({len(mock._call_log)} calls) ===")
for e in mock._call_log:
    print(f"  {e['m']} {e['u'][:130]}")

inject_drive_api(None)
