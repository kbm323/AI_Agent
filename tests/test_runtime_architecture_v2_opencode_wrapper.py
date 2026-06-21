from __future__ import annotations

import json
from pathlib import Path

from src.runtime_architecture_v2.schemas import WorkerTask, WorkerTaskRunner
from src.runtime_architecture_v2.workers import OpenCodeGoPacketWrapper


def _task(tmp_path: Path, role: str = "glm_validator") -> WorkerTask:
    return WorkerTask(
        worker_task_id="wt_glm",
        meeting_run_id="mr_001",
        role=role,
        runner=WorkerTaskRunner.OPENCODE_GO,
        packet_path=str(tmp_path / "packets" / "wt_glm.json"),
        output_path=str(tmp_path / "outputs" / "wt_glm.json"),
        model_policy={
            "preferred": "glm-5.1",
            "execution_role": "validator",
            "model_family": "glm",
        },
    )


def test_opencode_go_wrapper_writes_json_packet_without_live_execution(tmp_path: Path):
    wrapper = OpenCodeGoPacketWrapper(binary="opencode-go")
    task = _task(tmp_path)

    packet_path = wrapper.write_packet(
        task,
        prompt="검증해줘",
        context={"goal": "phase4 dry run"},
    )

    assert packet_path == Path(task.packet_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["worker_task"]["worker_task_id"] == "wt_glm"
    assert packet["prompt"] == "검증해줘"
    assert packet["context"] == {"goal": "phase4 dry run"}
    assert packet["dry_run"] is True
    assert "openclaw" not in json.dumps(packet).lower()


def test_opencode_go_wrapper_builds_safe_command_from_packet_path(tmp_path: Path):
    wrapper = OpenCodeGoPacketWrapper(binary="/opt/bin/opencode-go")
    task = _task(tmp_path)
    packet_path = wrapper.write_packet(task, prompt="검증해줘", context={})

    command = wrapper.build_command(
        task,
        packet_path=packet_path,
        timeout_seconds=90,
        output_format="json",
    )

    assert command == [
        "/opt/bin/opencode-go",
        "--model",
        "glm-5.1",
        "--context-file",
        str(packet_path),
        "--timeout-seconds",
        "90",
        "--prompt",
        "검증해줘",
        "--format",
        "json",
    ]


def test_opencode_go_wrapper_defaults_codex_model_from_policy(tmp_path: Path):
    wrapper = OpenCodeGoPacketWrapper(binary="opencode-go")
    task = WorkerTask(
        worker_task_id="wt_codex",
        meeting_run_id="mr_001",
        role="codex_auditor",
        runner=WorkerTaskRunner.OPENCODE_GO,
        packet_path=str(tmp_path / "wt_codex.json"),
        output_path=str(tmp_path / "wt_codex.out"),
        model_policy={"preferred": "codex", "execution_role": "auditor"},
    )

    command = wrapper.build_command(
        task,
        packet_path=wrapper.write_packet(task, prompt="audit", context={}),
        timeout_seconds=120,
    )

    assert command[command.index("--model") + 1] == "codex"
    assert "--context-file" in command
