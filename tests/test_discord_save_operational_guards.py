import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "operations" / "discord-save-slash-command.md"
SECRET_SCAN = ROOT / "scripts" / "pre-commit-secret-scan.sh"


def _bash() -> str:
    candidate = shutil.which("bash")
    if candidate:
        return candidate
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parent.parent
        for adjacent in (
            git_root / "bin" / "bash.exe",
            git_root / "usr" / "bin" / "bash.exe",
        ):
            if adjacent.is_file():
                return str(adjacent)
    pytest.skip("Git Bash is unavailable")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "safe.txt").write_text("safe\n", encoding="utf-8")
    return repo, _commit(repo, "base")


def _scan(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(SECRET_SCAN), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_secret_scan_preserves_staged_mode_and_adds_tree_and_range_modes():
    script = SECRET_SCAN.read_text(encoding="utf-8")

    assert "--staged" in script
    assert "--tree" in script
    assert "--range" in script
    assert "git diff --cached" in script
    assert "git ls-tree" in script
    assert "git diff" in script
    assert "git rev-list --count" in script


def test_secret_scan_staged_mode_preserves_empty_pre_commit_success(tmp_path):
    repo, _base = _repo(tmp_path)

    assert _scan(repo).returncode == 0
    assert _scan(repo, "--staged").returncode == 0


def test_secret_scan_range_reads_committed_tree_and_blocks_secret(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "config.env").write_text(
        "DISCORD_BOT_" + "TOKEN=" + ("a" * 32) + "\n",
        encoding="utf-8",
    )
    head = _commit(repo, "secret")

    result = _scan(repo, "--range", f"{base}..{head}")

    assert result.returncode == 1
    assert "SECRET SCAN BLOCKED" in result.stdout
    assert "abcdefghijklmnopqrstuvwxyz123456" not in result.stdout


def test_secret_scan_range_rejects_vacuous_commit_range(tmp_path):
    repo, base = _repo(tmp_path)

    result = _scan(repo, "--range", f"{base}..{base}")

    assert result.returncode != 0
    assert "non-vacuous" in (result.stdout + result.stderr)


def test_secret_scan_staged_and_range_modes_scan_rename_destinations(tmp_path):
    repo, _base = _repo(tmp_path)
    original = repo / "safe.txt"
    original.write_text(
        "\n".join(f"safe line {index}" for index in range(40)),
        encoding="utf-8",
    )
    base = _commit(repo, "expand safe file")

    renamed = repo / "renamed.env"
    _git(repo, "mv", original.name, renamed.name)
    with renamed.open("a", encoding="utf-8") as handle:
        handle.write("\nDISCORD_BOT_TOKEN=" + ("a" * 32) + "\n")

    _git(repo, "add", renamed.name)
    staged = _scan(repo, "--staged")
    assert staged.returncode == 1
    head = _commit(repo, "rename with secret")
    assert _git(repo, "diff", "--name-status", "-M", f"{base}..{head}").startswith("R")

    ranged = _scan(repo, "--range", f"{base}..{head}")
    assert ranged.returncode == 1
    assert "renamed.env" in ranged.stdout


def test_secret_scan_tree_mode_scans_the_named_commit(tmp_path):
    repo, base = _repo(tmp_path)
    assert _scan(repo, "--tree", base).returncode == 0

    (repo / "config.env").write_text(
        "OPENAI_API_KEY=" + "sk-not-a-real-key\n",
        encoding="utf-8",
    )
    head = _commit(repo, "tree secret")

    assert _scan(repo, "--tree", head).returncode == 1


def test_runbook_uses_non_vacuous_reviewed_range_and_rejects_untracked_files():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "REVIEWED_BASE=c7d52c7fc6c3bb19ef048e16acd659a717dd6218" in runbook
    assert (
        "bash scripts/pre-commit-secret-scan.sh --range "
        '"$REVIEWED_BASE..$AI_AGENT_COMMIT"' in runbook
    )
    assert "git status --porcelain --untracked-files=all" in runbook
    assert "git ls-files --others --exclude-standard" in runbook


def test_runbook_documents_verified_cutoff_and_precise_dm_limitation():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "HERMES_SESSION_MESSAGE_ID" in runbook
    assert "HERMES_SESSION_START_MESSAGE_ID" in runbook
    assert "dm_boundary_unavailable" in runbook
    assert "lower 22 bit" in runbook
    assert "durable" in runbook


def test_rollback_stops_resyncs_restores_and_verifies_absence():
    rollback = RUNBOOK.read_text(encoding="utf-8").split("## 롤백", 1)[1]

    assert "tmux kill-session -t hermes-aicompanyassistant" in rollback
    assert "rollback-state" in rollback
    assert "plugins disable ai-agent-commands" in rollback
    assert "skills uninstall save" in rollback
    assert "gateway run" in rollback
    assert "save_discord_thread_to_obsidian" in rollback
    assert "/save" in rollback
    assert "picker" in rollback


def test_runbook_reloads_all_profiles_after_assistant_first_smoke():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assistant_smoke = runbook.index("tmux new-session -d -s hermes-aicompanyassistant")
    remaining_reload = runbook.index(
        'for profile in "${profiles[@]}"; do', assistant_smoke
    )

    assert assistant_smoke < remaining_reload
    assert (
        'test "$profile" = aicompanyassistant && continue' in runbook[remaining_reload:]
    )
    assert (
        'tmux kill-session -t "$session" 2>/dev/null || true'
        in runbook[remaining_reload:]
    )
    assert 'tmux new-session -d -s "$session"' in runbook[remaining_reload:]


def test_rollback_tracks_stops_restores_resyncs_and_verifies_all_profiles():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    rollback = runbook[runbook.index("## ", runbook.index("smoke", 1)) :]

    assert 'tmux has-session -t "$session"' in runbook
    assert ': > "$state_root/was-running"' in runbook
    assert 'tmux kill-session -t "$session" 2>/dev/null || true' in rollback
    assert 'if [ -f "$state_root/was-running" ]; then' in rollback
    assert 'tmux new-session -d -s "$session"' in rollback
    assert "save_discord_thread_to_obsidian" in rollback
    assert "/save" in rollback
    assert "picker" in rollback
