# AI_Agent

Discord 기반 Virtual AI Company orchestration core.

이 프로젝트는 OpenClaw/Hermes를 Discord 작업실 안의 역할 에이전트로 운영하기 위한 새 코어다. 기존 진단 repo와 분리해서, task/turn/state 중심으로 다시 설계한다.

## MVP 목표

```text
parent channel user request
  -> task 생성
  -> Discord thread 생성
  -> parent에는 "Agent discussion started -> <thread>"만 게시
  -> OpenClaw owner draft
  -> Hermes reviewer request
  -> Hermes review
  -> OpenClaw final synthesis
  -> thread timeline 게시
```

## 운영 원칙

- Channel = project
- Thread = task
- OpenClaw = orchestrator / owner / finalizer
- Hermes = reviewer-only, mention/reply when requested
- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.
- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.

## 실행

```bash
npm test
npm run dry-run -- --request "뮤직비디오 오프닝 아이디어를 회의해줘."
```

## Public API

```ts
import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";
import {
  analyzeUserRequest,
  buildCompressedLoopContextArtifact,
  buildDefaultTokenStrategy,
  buildReviewerRequest,
  buildRoleRoutes,
  buildTaskGraph,
  decomposeUserRequest,
  format_message,
  load_config,
  serializeEscalationResult,
  summarizeForThread,
  validate_token,
} from "ai-agent";
import type { TaskGraph, TokenValidationResult } from "ai-agent";
import { ExecPersona, run_execution } from "ai-agent/execution-persona";
import { ReviewPersona, run_review } from "ai-agent/review-persona";
import {
  CompanyOrchestrator,
  buildReviewerRequest,
  buildThreadName,
  serializeEscalationResult,
} from "ai-agent/meeting-loop";
import { connect, disconnect, get_client } from "ai-agent/discord";
import { create_thread, archive_thread, get_thread } from "ai-agent/threads";
import { send_message, on_message, register_handler } from "ai-agent/messages";
```

현재 단계는 fake executor 기반 core 검증이다. 실제 OpenClaw/Hermes 연결은 executor adapter로 붙인다.
