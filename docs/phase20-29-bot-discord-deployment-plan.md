# Phase 20: 29-role Org Chart Registry — Plan

## Goal

기존 8개 페르소나(multi_bot.py BOT_PERSONAS)를 **29개 직무 Bot**으로 확장.
Bot registry, profile 정의, deployment manifest, dry-run 검증까지.

## Design Principle

```
Phase 14: Multi-bot protocol (8 roles)
Phase 20: Full 29-role org chart registry + deployment manifest
```

- **Registry = source of truth**: 29개 역할 정의 (id, name, department, permissions, mention_gate)
- **Dry-run default**: 실제 Discord bot 생성 없이 manifest만 출력
- **Mention-gated**: 모든 bot은 mention 없이 응답하지 않음 (기존 정책 유지)
- **No Hermes Core mutation**: Bot metadata만 project-local artifact로 관리

## 29 Role Map

### Executive (3)
1. ceo_coordinator — 대표 — P0 권한
2. coo — 운영총괄 — P1
3. cfo — 재무총괄 — P2

### Content Production (6)
4. content_lead — 콘텐츠 팀장
5. producer — 프로듀서
6. writer — 작가
7. editor — 편집자
8. script_director — 대본감독
9. storyboard_artist — 스토리보드

### Art & Visual (5)
10. art_lead — 아트 팀장
11. character_designer — 캐릭터 디자이너
12. background_artist — 배경 아티스트
13. animator — 애니메이터
14. vfx_artist — VFX 아티스트

### Technology (5)
15. tech_lead — 기술 팀장
16. engine_developer — 엔진 개발자
17. backend_developer — 백엔드
18. ai_engineer — AI 엔지니어
19. devops_engineer — 데브옵스

### Marketing & Business (5)
20. marketing_lead — 마케팅 팀장
21. sns_manager — SNS 매니저
22. community_manager — 커뮤니티 매니저
23. business_support_lead — 사업지원 팀장
24. partnership_manager — 파트너십 매니저

### Quality & Validation (3)
25. validation_audit — 검증 팀장
26. quality_lead — QA 리드
27. legal_compliance — 법무/컴플라이언스

### Production Support (2)
28. project_manager — 프로젝트 매니저
29. hr_lead — 인사/문화

## Data Structures

```python
@dataclass(frozen=True)
class BotProfile:
    role_id: str              # internal id (e.g., "content_lead")
    display_name: str         # Discord display name (e.g., "콘텐츠 팀장")
    department: str           # Executive/Content/Art/Tech/Marketing/Quality/Support
    permissions: tuple[str]   # Discord permission set (mention_gated, read_history, etc.)
    mention_gated: bool=True  # 모든 bot 기본 mention-gated
    priority: str="P2"        # P0(ceo) / P1(leads) / P2(rest)

@dataclass(frozen=True)
class BotRegistry:
    profiles: tuple[BotProfile, ...]  # 29 profiles
    → get(role_id) → BotProfile
    → by_department(name) → tuple
    → to_manifest() → dict (JSON-serializable deployment manifest)
```

## Acceptance Criteria

1. **AC-1**: 29개 role 전부 등록, 중복 role_id 없음
2. **AC-2**: BotRegistry에서 role_id/department로 조회 가능
3. **AC-3**: to_manifest()가 JSON 직렬화 가능한 deployment manifest 생성
4. **AC-4**: 모든 bot mention_gated=True 기본값
5. **AC-5**: 기존 multi_bot.py BOT_PERSONAS와 충돌 없음 (확장만)
6. **AC-6**: multi_bot.py BOT_PERSONAS에 29개 role 반영
7. **AC-7**: dry-run CLI → manifest 출력, artifact 저장
