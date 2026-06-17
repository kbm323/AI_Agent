# Token-efficient AI_Agent remaining AC execution packets

Parent: `/home/kbm/F:ai-projects/AI_Agent/seeds/seed_remaining.yaml`

Rule: run one packet at a time; verify existing files first; never rerun full parent seed unless explicitly requested.

## packet_01_validation_conflict — Validation conflict resolution
ACs:
- AC8 Codex dual-validation resolves conflicts by domain
Target files:
- `src/conflict_detector.py`
- `src/resolution_decision.py`
- `src/validation_wrapper.py`
- `tests/test_conflict_detector.py`
- `tests/test_resolution_decision.py`
- `tests/test_validation_wrapper.py`
Verification:
- `pytest tests/test_conflict_detector.py tests/test_resolution_decision.py tests/test_validation_wrapper.py -q`

## packet_02_priority_lifecycle — Priority queue, pause/resume, cancel
ACs:
- AC9 P0-P1-P2-P3 priority queue FIFO max 2 concurrent meetings
- AC10 P0 pauses lower-priority meetings and resumes from manifest.completed_step
- AC11 Cancel command cancels meeting with cancelled state and preserved output
Target files:
- `src/priority_queue.py`
- `src/meeting_scheduler.py`
- `src/manifest_serializer.py`
- `src/cancelled_transition.py`
- `tests/test_priority_queue.py`
- `tests/test_meeting_scheduler.py`
- `tests/test_cancelled_transition.py`
Verification:
- `pytest tests/test_cancelled_transition.py -q`
- `pytest tests/test_priority_queue.py tests/test_meeting_scheduler.py -q`

## packet_03_failure_recovery — Worker failure, quorum, role completion, crash recovery
ACs:
- AC12 Worker retry/fallback/quorum/escalation/fail sequence
- AC13 Required roles complete with retry/fallback; optional roles may skip with degradation
- AC14 Coordinator crash recovery resumes from manifest.completed_step
Target files:
- `src/retry_executor.py`
- `src/fallback_activation_gate.py`
- `src/router_failure_classifier.py`
- `src/escalation_router.py`
- `src/crash_recovery.py`
- `src/recovery_handler.py`
- `tests/test_retry_executor.py`
- `tests/test_fallback_activation_gate.py`
- `tests/test_crash_recovery.py`
- `tests/test_recovery_handler.py`
Verification:
- `pytest tests/test_retry_executor.py tests/test_fallback_activation_gate.py tests/test_crash_recovery.py tests/test_recovery_handler.py -q`

## packet_04_openclaw_controls — OpenClaw execution controls
ACs:
- AC16 OpenClaw <=30s synchronous, >30s asynchronous
- AC17 High-risk actions require HITL approval
- AC18 Cancel-only intervention; semantic retune creates new execution_id
Target files:
- `src/openclaw_execution_mode.py`
- `src/action_descriptor_validator.py`
- `src/openclaw_approval.py`
- `src/openclaw_intervention.py`
- `tests/test_openclaw_execution_mode.py`
- `tests/test_action_descriptor_validator.py`
- `tests/test_openclaw_approval.py`
- `tests/test_openclaw_intervention.py`
Verification:
- `pytest tests/test_openclaw_execution_mode.py tests/test_action_descriptor_validator.py -q`
- `pytest tests/test_openclaw_approval.py tests/test_openclaw_intervention.py -q`

## packet_05_knowledge_records — Dynamic knowledge retrieval and append-only records
ACs:
- AC20 Coordinator retrieves relevant past knowledge, not blanket injection
- AC21 Meeting records/decision logs append-only; superseded via metadata
Target files:
- `src/context_relevance_validator.py`
- `src/knowledge_query_builder.py`
- `src/knowledge_retrieval_service.py`
- `src/append_only_log.py`
- `tests/test_context_relevance_validator.py`
- `tests/test_knowledge_query_builder.py`
- `tests/test_knowledge_retrieval_service.py`
- `tests/test_append_only_log.py`
Verification:
- `pytest tests/test_context_relevance_validator.py -q`
- `pytest tests/test_knowledge_query_builder.py tests/test_knowledge_retrieval_service.py tests/test_append_only_log.py -q`

## packet_06_reports_personas_discord — Reports, persona specs, Discord delivery/follow-up
ACs:
- AC22 Periodic summaries and self-reflection reports
- AC23 agent.yaml + persona.md role specs with Git versioning
- AC24 6-7 persistent team-leader Discord bots; internal specialists as workers only
- AC25 Original Discord thread delivery and result/briefing cross-post
- AC26 Same-thread follow-up extends existing meeting_id
Target files:
- `src/periodic_summary.py`
- `src/persona_spec_loader.py`
- `src/team_leader_registry.py`
- `src/discord_delivery.py`
- `src/followup_thread_router.py`
- `tests/test_periodic_summary.py`
- `tests/test_persona_spec_loader.py`
- `tests/test_team_leader_registry.py`
- `tests/test_discord_delivery.py`
- `tests/test_followup_thread_router.py`
Verification:
- `pytest tests/test_periodic_summary.py tests/test_persona_spec_loader.py tests/test_team_leader_registry.py tests/test_discord_delivery.py tests/test_followup_thread_router.py -q`
