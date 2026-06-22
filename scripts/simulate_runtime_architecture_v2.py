#!/usr/bin/env python3
"""Run Runtime Architecture v2 deterministic Phase 9 simulation scenarios.

This is the plan-required Phase 9 CLI:

    python scripts/simulate_runtime_architecture_v2.py --scenario fast_qa
    python scripts/simulate_runtime_architecture_v2.py --scenario meeting
    python scripts/simulate_runtime_architecture_v2.py --scenario worker_failure
    python scripts/simulate_runtime_architecture_v2.py --scenario all

It uses fake/injected boundaries only. It never calls Discord, external models,
provider dashboards, opencode-go, or Hermes runtime APIs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime_architecture_v2.orchestrator import RuntimeOrchestrator  # noqa: E402
from src.runtime_architecture_v2.schemas import (  # noqa: E402
    MeetingRunState,
    ValidationVerdict,
    WorkerTask,
)
from src.runtime_architecture_v2.simulation_cli import result_to_report  # noqa: E402
from src.runtime_architecture_v2.workers import (  # noqa: E402
    FakeWorkerRunner,
    WorkerRunError,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    trigger_text: str
    expected_state: MeetingRunState
    user_id: str = "simulation-user"
    channel_id: str = "simulation-channel"
    thread_id: str = "simulation-thread"
    guild_id: str = "simulation-guild"
    hermes_session_id: str = "simulation-session"
    priority: str = "P2"
    simulation: bool = True


class DispatchExplodingRunner(FakeWorkerRunner):
    """Fake runner that simulates a crash during worker dispatch."""

    def dispatch(self, task: WorkerTask) -> WorkerTask:
        raise WorkerRunError(
            code="dispatch_failed",
            message="simulated dispatch exception",
            worker_task_id=task.worker_task_id,
        )


class RevisingOrchestrator(RuntimeOrchestrator):
    """Fake orchestrator variant that simulates validator correction feedback."""

    @staticmethod
    def _build_validation_verdicts(
        *,
        meeting_run_id: str,
        validators: tuple[str, ...],
        worker_tasks: tuple[WorkerTask, ...],
    ) -> tuple[ValidationVerdict, ...]:
        del worker_tasks
        validator_roles = validators or ("glm_validator",)
        return tuple(
            ValidationVerdict(
                validation_id=f"val_{meeting_run_id}_{index}",
                meeting_run_id=meeting_run_id,
                validator_role=validator_role,
                validator_model=(
                    "codex" if validator_role == "codex_auditor" else "glm-5.1"
                ),
                verdict="revise",
                confidence=0.74,
                findings=("deterministic correction loop requested",),
                required_actions=("revise fake worker output",),
            )
            for index, validator_role in enumerate(validator_roles, start=1)
        )


SCENARIOS: dict[str, Scenario] = {
    "fast_qa": Scenario(
        name="fast_qa",
        trigger_text="대표 캐릭터 이름을 한 문장으로 추천해줘",
        expected_state=MeetingRunState.COMPLETED,
    ),
    "meeting": Scenario(
        name="meeting",
        trigger_text="신규 버추얼 아이돌 뮤비 콘셉트 기획 회의를 열어줘",
        expected_state=MeetingRunState.COMPLETED,
    ),
    "worker_execution": Scenario(
        name="worker_execution",
        trigger_text="런타임 어댑터 코드 구현과 테스트 실행해줘",
        expected_state=MeetingRunState.COMPLETED,
    ),
    "dual_validation_pass": Scenario(
        name="dual_validation_pass",
        trigger_text="콘셉트 기획과 코드 구현, 마케팅 전략까지 같이 회의해줘",
        expected_state=MeetingRunState.COMPLETED,
    ),
    "validation_correction_loop": Scenario(
        name="validation_correction_loop",
        trigger_text="코드 구현 결과를 검증하고 수정 루프까지 시뮬레이션해줘",
        expected_state=MeetingRunState.ACTIVE,
    ),
    "crash_recovery": Scenario(
        name="crash_recovery",
        trigger_text="코드 구현 중 worker dispatch crash recovery를 검증해줘",
        expected_state=MeetingRunState.FAILED,
    ),
    "worker_failure": Scenario(
        name="worker_failure",
        trigger_text="코드 구현 중 worker timeout failure를 검증해줘",
        expected_state=MeetingRunState.FAILED,
    ),
}
SCENARIO_ORDER = tuple(SCENARIOS)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    selected = SCENARIO_ORDER if args.scenario == "all" else (args.scenario,)
    results = [
        _run_scenario(SCENARIOS[name], root=Path(args.root)) for name in selected
    ]
    report = {
        "ok": all(item["scenario_ok"] for item in results),
        "mode": "simulation",
        "scenario": args.scenario,
        "scenario_count": len(results),
        "used_live_adapters": False,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Runtime Architecture v2 Phase 9 fake simulation scenarios.",
    )
    parser.add_argument("--root", default="runtime/phase9-simulation")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=(*SCENARIO_ORDER, "all"),
        help="Scenario to run. Use 'all' for the full Phase 9 smoke suite.",
    )
    return parser.parse_args(argv)


def _run_scenario(scenario: Scenario, *, root: Path) -> dict[str, Any]:
    orchestrator = _orchestrator_for_scenario(scenario.name, root=root)
    meeting_run_id = f"mr_sim_{scenario.name}"
    result = orchestrator.run(
        meeting_run_id=meeting_run_id,
        trigger_text=scenario.trigger_text,
        user_id=scenario.user_id,
        channel_id=scenario.channel_id,
        thread_id=scenario.thread_id,
        guild_id=scenario.guild_id,
        hermes_session_id=scenario.hermes_session_id,
        priority=scenario.priority,
        simulation=scenario.simulation,
    )
    item = result_to_report(result)
    state = MeetingRunState(str(result.meeting_run.state))
    item.update(
        {
            "scenario": scenario.name,
            "scenario_ok": state == scenario.expected_state,
            "expected_state": scenario.expected_state.value,
            "artifact_dir": str(
                root / "runtime" / "meeting_runs" / result.meeting_run.meeting_run_id
            ),
            "worker_task_states": [str(task.state) for task in result.worker_tasks],
            "checkpoint_state": str(result.checkpoint.state),
        }
    )
    return item


def _orchestrator_for_scenario(
    scenario_name: str,
    *,
    root: Path,
) -> RuntimeOrchestrator:
    if scenario_name == "validation_correction_loop":
        return RevisingOrchestrator(root=root, worker_runner=FakeWorkerRunner())
    if scenario_name == "crash_recovery":
        return RuntimeOrchestrator(root=root, worker_runner=DispatchExplodingRunner())
    if scenario_name == "worker_failure":
        return RuntimeOrchestrator(
            root=root,
            worker_runner=FakeWorkerRunner(timeout=True),
        )
    return RuntimeOrchestrator(root=root, worker_runner=FakeWorkerRunner())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
