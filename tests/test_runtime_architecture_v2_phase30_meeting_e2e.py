from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.runtime_architecture_v2.meeting_e2e import (
    DEFAULT_PHASE30_ROLES,
    DiscordRestProjectionAdapter,
    FakeMeetingThreadProjectionAdapter,
    InjectedRoleOutputProvider,
    MeetingAuditResult,
    MeetingValidationResult,
    OpenCodeGoRoleOutputProvider,
    inspect_phase30_meeting,
    normalize_phase30_gateway_input,
    run_phase30_meeting_e2e,
)
from src.runtime_architecture_v2.schemas import MeetingRunState


def _provider(*, blocker: bool = False) -> InjectedRoleOutputProvider:
    outputs = {
        role: {
            "opinion": f"{role} opinion: 최종 회의 시스템 구현에 동의합니다.",
            "rebuttal": f"{role} rebuttal: 다른 팀 의견을 반영합니다.",
        }
        for role in DEFAULT_PHASE30_ROLES
    }
    if blocker:
        outputs["quality_lead"]["opinion"] = (
            "BLOCKER: live evidence 누락으로 출시 불가."
        )
    return InjectedRoleOutputProvider(outputs)


def test_phase30_e2e_creates_completed_meeting_with_7_roles_and_artifacts(
    tmp_path: Path,
):
    adapter = FakeMeetingThreadProjectionAdapter()

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="신규 버추얼 아이돌 데뷔 전략 회의 열어줘",
        user_id="u-1",
        channel_id="ch-1",
        guild_id="g-1",
        role_output_provider=_provider(),
        projection_adapter=adapter,
    )

    assert result.ok is True
    assert result.meeting_run.state == MeetingRunState.COMPLETED
    assert result.roles == DEFAULT_PHASE30_ROLES
    assert len(result.rounds) == 2
    assert {message.role for message in result.rounds[0].messages} == set(
        DEFAULT_PHASE30_ROLES
    )
    assert {message.role for message in result.rounds[1].messages} == set(
        DEFAULT_PHASE30_ROLES
    )
    assert result.consensus.consensus_reached is True
    assert result.consensus.escalation_required is False
    assert result.projection.thread_status == "created"
    assert (
        result.projection.posted_count == 15
    )  # 7 opinions + 7 rebuttals + final report

    run_dir = tmp_path / "runtime" / "meeting_runs" / result.meeting_run.meeting_run_id
    phase_dir = run_dir / "phase30"
    expected_files = {
        "rounds.json",
        "role_outputs.json",
        "validation_packet.json",
        "consensus.json",
        "final_report.md",
        "evidence.json",
        "recovery_checkpoint.json",
    }
    assert expected_files <= {path.name for path in phase_dir.iterdir()}

    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["meeting_run_id"] == result.meeting_run.meeting_run_id
    assert evidence["thread_id"] == result.projection.thread_id
    assert len(evidence["message_ids"]) == 15
    assert "fake_worker_count" not in evidence


def test_phase30_consensus_detects_blocker_and_marks_degraded(tmp_path: Path):
    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="출시 전 리스크 검토 회의",
        role_output_provider=_provider(blocker=True),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
    )

    assert result.ok is False
    assert result.meeting_run.state == MeetingRunState.FAILED
    assert result.consensus.consensus_reached is False
    assert result.consensus.escalation_required is True
    assert result.consensus.blockers == (
        "quality_lead: live evidence 누락으로 출시 불가.",
    )
    assert result.error == "consensus_blocked"


def test_phase30_can_write_company_second_brain_from_e2e_result(tmp_path: Path):
    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="팬 참여형 쇼츠 캠페인 회의",
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        write_knowledge=True,
    )

    assert result.knowledge is not None
    assert result.knowledge.ok is True
    assert Path(result.knowledge.raw_path).exists()
    assert Path(result.knowledge.wiki_path).exists()
    assert (tmp_path / "knowledge" / "wiki" / "index.md").exists()
    assert "팬 참여형 쇼츠" in Path(result.knowledge.wiki_path).read_text(
        encoding="utf-8"
    )


def test_phase30_gateway_input_normalization_and_inspection(tmp_path: Path):
    trigger = normalize_phase30_gateway_input(
        {
            "content": "@대표 회의 열어줘: 7봇 최종 점검",
            "user_id": "u-2",
            "channel_id": "ch-ceo",
            "thread_id": "",
            "guild_id": "g-main",
            "profile": "aicompanyceo",
            "session_id": "s-1",
        }
    )
    assert trigger.trigger_text == "@대표 회의 열어줘: 7봇 최종 점검"
    assert trigger.profile == "aicompanyceo"

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger=trigger,
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
    )
    inspected = inspect_phase30_meeting(tmp_path, result.meeting_run.meeting_run_id)

    assert inspected["ok"] is True
    assert inspected["meeting_run_id"] == result.meeting_run.meeting_run_id
    assert inspected["state"] == "completed"
    assert inspected["missing_files"] == []
    assert "phase30/final_report.md" in inspected["artifact_paths"]


def test_phase30_cli_runs_dry_run_without_opencode(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase30_meeting_e2e.py",
            "--root",
            str(tmp_path),
            "--trigger-text",
            "Discord 요청 기반 7봇 회의 dry-run",
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["posted_count"] == 15
    assert payload["mode"] == "dry-run"
    assert payload["opencode_used"] is False


def test_phase31_cli_can_run_opencode_mode_with_fixture_runner(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase30_meeting_e2e.py",
            "--root",
            str(tmp_path),
            "--trigger-text",
            "OpenCode Go CLI fixture 회의",
            "--use-opencode-go",
            "--opencode-runner-fixture-output",
            "fixture live output",
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "opencode-go"
    assert payload["opencode_used"] is True
    assert payload["posted_count"] == 15
    assert payload["opencode_result_count"] == 14


def test_phase31_opencode_provider_uses_injected_runner_and_sanitizes_output():
    calls = []

    def runner(argv, *, input_text, timeout_sec, env):
        calls.append(
            {
                "argv": argv,
                "input_text": input_text,
                "timeout_sec": timeout_sec,
                "env": env,
            }
        )
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "content": (
                        "실제 역할 의견입니다. bearer SECRET_TOKEN_VALUE @everyone"
                    ),
                },
                ensure_ascii=False,
            ),
            "stderr": "",
            "duration_sec": 1.25,
            "timed_out": False,
        }

    provider = OpenCodeGoRoleOutputProvider(
        runner=runner,
        executable="opencode-go-test",
        model="glm-5.2",
        timeout_sec=17,
        env={"OPENCODE_GO_AUTH": "credential-value"},
    )

    output = provider.generate(
        role="content_lead",
        round_name="opinion",
        trigger=normalize_phase30_gateway_input({"content": "데뷔 전략 회의"}),
    )

    assert output == "실제 역할 의견입니다. bearer [redacted] @\u000beveryone"
    assert len(calls) == 1
    assert calls[0]["argv"][:3] == ["opencode-go-test", "--model", "glm-5.2"]
    assert "--context-file" in calls[0]["argv"]
    assert "--prompt" in calls[0]["argv"]
    assert "--timeout-seconds" in calls[0]["argv"]
    assert "glm-5.2" in calls[0]["argv"]
    assert "content_lead" in calls[0]["input_text"]
    assert "opinion" in calls[0]["input_text"]
    assert "credential-value" not in calls[0]["input_text"]
    assert calls[0]["timeout_sec"] == 17
    assert provider.last_results[0].status == "ok"
    assert provider.last_results[0].exit_code == 0
    assert provider.last_results[0].model == "glm-5.2"


def test_phase31_opencode_provider_fail_closed_on_nonzero_exit():
    def runner(argv, *, input_text, timeout_sec, env):
        del argv, input_text, timeout_sec, env
        return {
            "returncode": 2,
            "stdout": "raw failure bearer BADTOKEN",
            "stderr": "opencode failed credential-redacted",
            "duration_sec": 0.5,
            "timed_out": False,
        }

    provider = OpenCodeGoRoleOutputProvider(runner=runner)
    output = provider.generate(
        role="quality_lead",
        round_name="rebuttal",
        trigger=normalize_phase30_gateway_input({"content": "리스크 검토"}),
    )

    assert output.startswith("BLOCKER: opencode-go role output failed")
    assert "BADTOKEN" not in output
    assert "super-secret" not in output
    assert provider.last_results[0].status == "failed"
    assert provider.last_results[0].exit_code == 2


def test_phase31_e2e_records_opencode_provenance_when_provider_is_used(
    tmp_path: Path,
):
    def runner(argv, *, input_text, timeout_sec, env):
        del argv, timeout_sec, env
        role = next(
            line.split(": ", 1)[1]
            for line in input_text.splitlines()
            if line.startswith("Role: ")
        )
        round_name = next(
            line.split(": ", 1)[1]
            for line in input_text.splitlines()
            if line.startswith("Round: ")
        )
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {"content": f"{role} {round_name} live output"},
                ensure_ascii=False,
            ),
            "stderr": "",
            "duration_sec": 0.1,
            "timed_out": False,
        }

    provider = OpenCodeGoRoleOutputProvider(
        runner=runner,
        executable="opencode-go-test",
        model="glm-5.2",
    )

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="실제 7봇 회의 provider provenance 검증",
        role_output_provider=provider,
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
    )

    phase_dir = (
        tmp_path / "runtime" / "meeting_runs" / result.meeting_run_id / "phase30"
    )
    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))
    role_outputs = json.loads(
        (phase_dir / "role_outputs.json").read_text(encoding="utf-8")
    )
    validation_packet = json.loads(
        (phase_dir / "validation_packet.json").read_text(encoding="utf-8")
    )

    assert result.ok is True
    assert evidence["opencode_used"] is True
    assert len(evidence["opencode_results"]) == 14
    assert evidence["opencode_results"][0]["provider"] == "opencode-go"
    assert role_outputs["opencode_used"] is True
    assert len(role_outputs["opencode_results"]) == 14
    assert validation_packet["opencode_used"] is True


def test_phase31c_injected_glm_validation_is_persisted_and_reflected(
    tmp_path: Path,
):
    calls = []

    def validation_runner(packet):
        calls.append(packet)
        return MeetingValidationResult(
            status="ok",
            provider="glm-live",
            model="glm-5.2",
            verdict="pass",
            confidence=0.94,
            summary="GLM validator confirmed consensus evidence is sufficient.",
            blockers=(),
            raw_output="validator raw @everyone bearer SHOULD_NOT_LEAK",
        )

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="GLM validation fixture 회의",
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        validation_runner=validation_runner,
    )

    phase_dir = (
        tmp_path / "runtime" / "meeting_runs" / result.meeting_run_id / "phase30"
    )
    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))
    validation_packet = json.loads(
        (phase_dir / "validation_packet.json").read_text(encoding="utf-8")
    )
    final_report = (phase_dir / "final_report.md").read_text(encoding="utf-8")

    assert result.ok is True
    assert len(calls) == 1
    assert calls[0].meeting_run_id == result.meeting_run_id
    assert result.validation_packet.validator_model == "glm-5.2"
    assert result.validation_packet.validation_result is not None
    assert result.validation_packet.validation_result.provider == "glm-live"
    assert validation_packet["validation_result"]["provider"] == "glm-live"
    assert validation_packet["validation_result"]["verdict"] == "pass"
    assert evidence["validation_result"]["provider"] == "glm-live"
    assert "GLM validator confirmed" in final_report
    assert "SHOULD_NOT_LEAK" not in final_report


def test_phase31c_validation_blocker_fails_closed(tmp_path: Path):
    def validation_runner(packet):
        del packet
        return MeetingValidationResult(
            status="ok",
            provider="glm-live",
            model="glm-5.2",
            verdict="block",
            confidence=0.88,
            summary="GLM validator found unresolved launch risk.",
            blockers=("unresolved launch risk",),
        )

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="GLM validation blocker 회의",
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        validation_runner=validation_runner,
    )

    phase_dir = (
        tmp_path / "runtime" / "meeting_runs" / result.meeting_run_id / "phase30"
    )
    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))

    assert result.ok is False
    assert result.meeting_run.state == MeetingRunState.FAILED
    assert result.error == "validation_blocked"
    assert result.consensus.escalation_required is True
    assert evidence["error"] == "validation_blocked"
    assert evidence["validation_result"]["verdict"] == "block"


def test_phase31d_audit_runner_is_called_for_blocked_validation(tmp_path: Path):
    audit_calls = []

    def validation_runner(packet):
        del packet
        return MeetingValidationResult(
            status="ok",
            provider="glm-live",
            model="glm-5.2",
            verdict="block",
            confidence=0.91,
            summary="GLM validator requires final audit.",
            blockers=("needs final audit",),
        )

    def audit_runner(packet, *, validation_result):
        audit_calls.append((packet, validation_result))
        return MeetingAuditResult(
            status="ok",
            provider="codex-gpt-audit",
            model="gpt-5.5",
            verdict="requires_changes",
            summary="Final audit requires launch-risk remediation.",
            findings=("launch risk remains",),
            raw_output="audit raw @everyone bearer SHOULD_NOT_LEAK",
        )

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="Codex/GPT audit escalation 회의",
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        validation_runner=validation_runner,
        audit_runner=audit_runner,
    )

    phase_dir = (
        tmp_path / "runtime" / "meeting_runs" / result.meeting_run_id / "phase30"
    )
    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))
    final_report = (phase_dir / "final_report.md").read_text(encoding="utf-8")

    assert result.ok is False
    assert len(audit_calls) == 1
    assert audit_calls[0][1].verdict == "block"
    assert result.audit_result is not None
    assert result.audit_result.provider == "codex-gpt-audit"
    assert evidence["audit_result"]["provider"] == "codex-gpt-audit"
    assert evidence["audit_result"]["verdict"] == "requires_changes"
    assert "Final audit requires" in final_report
    assert "SHOULD_NOT_LEAK" not in final_report


def test_phase31d_audit_runner_not_called_when_no_escalation(tmp_path: Path):
    calls = []

    def audit_runner(packet, *, validation_result):
        calls.append((packet, validation_result))
        return MeetingAuditResult(
            status="ok",
            provider="codex-gpt-audit",
            model="gpt-5.5",
            verdict="pass",
            summary="not expected",
        )

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="Audit not required 회의",
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        audit_runner=audit_runner,
    )

    assert result.ok is True
    assert calls == []
    assert result.audit_result is None


def test_phase31e_discord_rest_projection_adapter_uses_injected_http_headers():
    calls = []

    def http_client(method, url, *, headers, json_body, timeout_sec):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_sec": timeout_sec,
            }
        )
        if url.endswith("/threads"):
            return {"status": 201, "json": {"id": "thread-live-1"}}
        return {"status": 200, "json": {"id": f"message-live-{len(calls)}"}}

    adapter = DiscordRestProjectionAdapter(
        bot_token="token-value",
        channel_id="channel-1",
        http_client=http_client,
        timeout_sec=11,
    )
    trigger = normalize_phase30_gateway_input(
        {"content": "live projection", "channel_id": "channel-1"}
    )

    thread_id = adapter.create_thread(meeting_run_id="meeting-1", trigger=trigger)
    message_id = adapter.post_message(
        thread_id=thread_id,
        role="quality_lead",
        content="hello @everyone bearer SHOULD_NOT_LEAK",
    )

    assert thread_id == "thread-live-1"
    assert message_id == "message-live-2"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/channels/channel-1/threads")
    assert calls[0]["headers"]["Authorization"] == "Bot token-value"
    assert "User-Agent" in calls[0]["headers"]
    assert calls[1]["url"].endswith("/channels/thread-live-1/messages")
    assert calls[1]["json_body"]["content"] == "hello @\u000beveryone bearer [redacted]"
    assert calls[1]["timeout_sec"] == 11


def test_phase31e_discord_rest_projection_adapter_fails_closed_on_non_2xx():
    def http_client(method, url, *, headers, json_body, timeout_sec):
        del method, url, headers, json_body, timeout_sec
        return {"status": 403, "json": {"message": "forbidden"}}

    adapter = DiscordRestProjectionAdapter(
        bot_token="token-value",
        channel_id="channel-1",
        http_client=http_client,
    )
    trigger = normalize_phase30_gateway_input({"content": "live projection"})

    assert adapter.create_thread(meeting_run_id="meeting-1", trigger=trigger) == ""
    assert adapter.last_error == "discord_thread_create_failed"


def test_phase31f_gateway_intake_produces_meeting_e2e_result_with_trace(
    tmp_path: Path,
):
    gateway_payload = {
        "content": "@AI_Company 최종 회의 시스템 검증",
        "user_id": "u-discord-1",
        "channel_id": "ch-discord-1",
        "guild_id": "g-discord-1",
        "profile": "aicompanyceo",
        "session_id": "s-hermes-1",
        "priority": "P1",
    }
    trigger = normalize_phase30_gateway_input(gateway_payload)
    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger=trigger,
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
    )

    assert result.ok is True
    assert result.meeting_run_id.startswith("phase30_meeting_e2e_")
    assert result.projection.thread_status == "created"
    assert result.projection.posted_count == 15
    assert result.consensus.consensus_reached is True
    assert "최종 회의 시스템 검증" in result.meeting_run.trigger["text"]

    phase_dir = (
        tmp_path / "runtime" / "meeting_runs" / result.meeting_run_id / "phase30"
    )
    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["ok"] is True
    assert evidence["meeting_run_id"] == result.meeting_run_id


def test_phase31g_second_brain_live_result_persists_knowledge_and_sanitizes(
    tmp_path: Path,
):
    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="Phase 31G Second Brain 검증 회의 bearer @everyone",
        role_output_provider=_provider(),
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        write_knowledge=True,
    )

    assert result.knowledge is not None
    assert result.knowledge.ok is True
    raw_path = Path(result.knowledge.raw_path)
    wiki_path = Path(result.knowledge.wiki_path)
    assert raw_path.exists()
    assert wiki_path.exists()

    wiki_text = wiki_path.read_text(encoding="utf-8")
    assert "Phase 31G Second Brain" in wiki_text
    assert "@everyone" not in wiki_text
    assert "phase30" in wiki_text.lower()

    index = tmp_path / "knowledge" / "wiki" / "index.md"
    assert index.exists()


def test_phase31h_evidence_bundle_includes_all_boundary_results(tmp_path: Path):
    audit_calls = []

    def validation_runner(packet):
        del packet
        return MeetingValidationResult(
            status="ok",
            provider="glm-live",
            model="glm-5.2",
            verdict="block",
            confidence=0.93,
            summary="GLM validator requires final audit for live operator review.",
            blockers=("live operator review needed",),
        )

    def audit_runner(packet, *, validation_result):
        audit_calls.append((packet, validation_result))
        return MeetingAuditResult(
            status="ok",
            provider="codex-gpt-audit",
            model="gpt-5.5",
            verdict="requires_changes",
            summary="Final audit: resolve launch risk before live Discord.",
            findings=("launch risk",),
        )

    provider = OpenCodeGoRoleOutputProvider(
        runner=lambda argv, *, input_text, timeout_sec, env: {
            "returncode": 0,
            "stdout": json.dumps({"content": "live role output"}, ensure_ascii=False),
            "stderr": "",
            "duration_sec": 0.1,
            "timed_out": False,
        },
        executable="opencode-go-test",
        model="glm-5.2",
    )

    result = run_phase30_meeting_e2e(
        root=tmp_path,
        trigger_text="Phase 31H supervised smoke evidence bundle 회의",
        role_output_provider=provider,
        projection_adapter=FakeMeetingThreadProjectionAdapter(),
        validation_runner=validation_runner,
        audit_runner=audit_runner,
        write_knowledge=True,
    )

    assert result.ok is False
    assert result.error == "validation_blocked"

    phase_dir = (
        tmp_path / "runtime" / "meeting_runs" / result.meeting_run_id / "phase30"
    )
    required = [
        "rounds.json",
        "role_outputs.json",
        "validation_packet.json",
        "consensus.json",
        "final_report.md",
        "evidence.json",
        "recovery_checkpoint.json",
    ]
    for name in required:
        assert (phase_dir / name).exists(), f"missing {name}"

    evidence = json.loads((phase_dir / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["opencode_used"] is True
    assert len(evidence["opencode_results"]) == 14
    assert evidence["validation_result"] is not None
    assert evidence["validation_result"]["verdict"] == "block"
    assert evidence["audit_result"] is not None
    assert evidence["audit_result"]["verdict"] == "requires_changes"
    assert evidence["error"] == "validation_blocked"

    final_report = (phase_dir / "final_report.md").read_text(encoding="utf-8")
    assert "Validation provider: glm-live" in final_report
    assert "Audit provider: codex-gpt-audit" in final_report
    assert "live operator review needed" not in final_report

    assert result.knowledge is not None
    assert result.knowledge.ok is True
