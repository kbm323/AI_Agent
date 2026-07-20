import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "operations" / "discord-save-slash-command.md"
SECRET_SCAN = ROOT / "scripts" / "pre-commit-secret-scan.sh"
ROLLBACK_SCRIPT = ROOT / "scripts" / "rollback_discord_save_profiles.sh"
PROFILES = (
    "aicompanyassistant",
    "aicompanyceo",
    "aicompanycontent",
    "aicompanyart",
    "aicompanytech",
    "aicompanymarketing",
    "aicompanyquality",
)


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


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        relative = resolved.relative_to(temporary_root)
    except ValueError:
        pass
    else:
        return f"/tmp/{relative.as_posix()}"
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix().split(":", 1)[-1]
    return f"/{drive}{tail}"


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


def _write_fake_gateway_commands(fake_bin: Path) -> None:
    fake_bin.mkdir()
    (fake_bin / "tmux").write_text(
        """#!/bin/bash
set -euo pipefail
command="$1"
shift
session=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -t|-s) session="$2"; shift 2 ;;
    *) shift ;;
  esac
done
case "$command" in
  has-session) test -f "$FAKE_TMUX_STATE/$session" ;;
  kill-session)
    printf 'kill %s\\n' "$session" >> "$FAKE_TMUX_LOG"
    rm -f "$FAKE_TMUX_STATE/$session"
    ;;
  new-session)
    test ! -f "$FAKE_TMUX_STATE/$session" || {
      printf 'collision %s\\n' "$session" >> "$FAKE_TMUX_LOG"
      exit 1
    }
    printf 'start %s\\n' "$session" >> "$FAKE_TMUX_LOG"
    : > "$FAKE_TMUX_STATE/$session"
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    (fake_bin / "hermes").write_text(
        """#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_HERMES_LOG"
printf 'ok\\n'
""",
        encoding="utf-8",
    )
    (fake_bin / "tmux").chmod(0o755)
    (fake_bin / "hermes").chmod(0o755)


@pytest.mark.parametrize("failure_point", ["before_assistant_smoke", "mid_reload"])
def test_rollback_script_resyncs_all_profiles_without_session_collisions(
    tmp_path,
    failure_point,
):
    work_root = tmp_path
    fake_bin = work_root / "bin"
    _write_fake_gateway_commands(fake_bin)
    tmux_state = work_root / "tmux"
    tmux_state.mkdir()
    tmux_log = work_root / "tmux.log"
    hermes_log = work_root / "hermes.log"
    profile_root = work_root / "profiles"
    rollback_state = work_root / "rollback-state"
    deploy_record = work_root / "deploy-record"
    deploy_record.mkdir()
    prior_running = set(PROFILES[:4])

    for profile in PROFILES:
        state_root = rollback_state / profile
        current_root = profile_root / profile
        state_root.mkdir(parents=True)
        (current_root / "plugins" / "ai-agent-commands").mkdir(parents=True)
        (current_root / "skills" / "save").mkdir(parents=True)
        (current_root / "config.yaml").write_text("candidate\n", encoding="utf-8")
        marker = "was-running" if profile in prior_running else "was-stopped"
        (state_root / marker).touch()
        if profile in prior_running:
            (state_root / "config.yaml").write_text("prior\n", encoding="utf-8")
        else:
            (state_root / "config-was-absent").touch()

    existing_profiles = set(prior_running)
    if failure_point == "mid_reload":
        for profile in PROFILES[:2]:
            (rollback_state / profile / "loaded-by-deployment").touch()
    for profile in existing_profiles:
        (tmux_state / f"hermes-{profile}").touch()

    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{_bash_path(fake_bin)}:{environment['PATH']}",
            "FAKE_TMUX_STATE": _bash_path(tmux_state),
            "FAKE_TMUX_LOG": _bash_path(tmux_log),
            "FAKE_HERMES_LOG": _bash_path(hermes_log),
            "HERMES_PROFILE_ROOT": _bash_path(profile_root),
            "ROLLBACK_STATE_DIR": _bash_path(rollback_state),
            "DEPLOY_RECORD_DIR": _bash_path(deploy_record),
        }
    )

    prepared = subprocess.run(
        [_bash(), str(ROLLBACK_SCRIPT), "prepare"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert {path.name for path in tmux_state.iterdir()} == {
        f"hermes-{profile}" for profile in PROFILES
    }
    tmux_actions = tmux_log.read_text(encoding="utf-8").splitlines()
    assert tmux_actions[:7] == [f"kill hermes-{profile}" for profile in PROFILES]
    assert not any(action.startswith("collision ") for action in tmux_actions)

    for profile in PROFILES:
        (deploy_record / f"{profile}.rollback-absence.txt").write_text(
            "tool absent: save_discord_thread_to_obsidian\npicker absent: /archive\n",
            encoding="utf-8",
        )

    finalized = subprocess.run(
        [_bash(), str(ROLLBACK_SCRIPT), "finalize"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert finalized.returncode == 0, finalized.stdout + finalized.stderr
    assert {path.name for path in tmux_state.iterdir()} == {
        f"hermes-{profile}" for profile in prior_running
    }
    for profile in PROFILES:
        current_root = profile_root / profile
        assert not (current_root / "plugins" / "ai-agent-commands").exists()
        assert not (current_root / "skills" / "save").exists()
        if profile in prior_running:
            assert (current_root / "config.yaml").read_text(
                encoding="utf-8"
            ) == "prior\n"
        else:
            assert not (current_root / "config.yaml").exists()


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
    script = ROLLBACK_SCRIPT.read_text(encoding="utf-8")

    assert "rollback_discord_save_profiles.sh prepare" in rollback
    assert 'tmux kill-session -t "$session" 2>/dev/null || true' in script
    assert "rollback-state" in rollback
    assert "plugins disable ai-agent-commands" in script
    assert "skills uninstall save" in script
    assert "gateway run" in script
    assert "save_discord_thread_to_obsidian" in rollback
    assert "/archive" in rollback
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
    script = ROLLBACK_SCRIPT.read_text(encoding="utf-8")

    assert 'tmux has-session -t "$session"' in runbook
    assert ': > "$state_root/was-running"' in runbook
    assert 'tmux kill-session -t "$session" 2>/dev/null || true' in script
    assert 'if [ -f "$state_root/was-running" ]; then' in script
    assert 'tmux new-session -d -s "$session"' in script
    assert "save_discord_thread_to_obsidian" in rollback
    assert "/archive" in rollback
    assert "picker" in rollback


def test_runbook_pins_single_qmd_collection_and_korean_model():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "npm install -g @tobilu/qmd@2.5.3" in runbook
    assert "QMD_FORCE_CPU=1" in runbook
    assert "QMD_LLAMA_GPU=false" in runbook
    assert (
        "Environment=PATH=/home/ubuntu/.local/bin:/home/ubuntu/.hermes/bin:"
        in runbook
    )
    assert "node --version" in runbook
    assert "qmd collection add /home/ubuntu/Obsidian --name obsidian" in runbook
    assert 'QMD_EMBED_MODEL="hf:Qwen/Qwen3-Embedding-0.6B-GGUF/' in runbook
    assert "Qwen3-Embedding-0.6B-Q8_0.gguf" in runbook
    assert 'qmd query "회의 결정" --json -c obsidian' in runbook


def test_runbook_pins_abxdl_and_no_install_arm64_probes():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "uv tool install abx-dl==1.11.235" in runbook
    assert "abx-dl version" in runbook
    assert "abx-dl plugins" in runbook
    assert "--no-install" in runbook
    assert "generic article" in runbook
    assert "YouTube video" in runbook
    assert "public Instagram post" in runbook
    assert "public Threads post" in runbook
    assert "uname -m" in runbook


def test_runbook_defines_one_five_minute_reconcile_timer_and_rollout_gate():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "ai-agent-qmd-reconcile.service" in runbook
    assert "ai-agent-qmd-reconcile.timer" in runbook
    assert "OnUnitActiveSec=5min" in runbook
    assert "python -m scripts.run_qmd_reconcile" in runbook
    assert "/llmwiki-ingest" in runbook
    assert "/llmwiki-note" in runbook
    assert "/llmwiki-find" in runbook
    assistant = runbook.index("aicompanyassistant")
    remaining = runbook.index("remaining six profiles", assistant)
    assert assistant < remaining
