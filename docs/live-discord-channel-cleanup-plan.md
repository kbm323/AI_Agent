# Live Discord Channel Cleanup Plan

Status: applied on live Discord; names/order changed, permissions and channel IDs unchanged.

## Immediate answer

The current meeting/decision channel is:

- `#회의실-전략결정`
- ID: `1505600167221526621`
- Home profile: `aicompanyceo` / Discord bot `대표`
- Function: strategy decision room; final decisions, priorities, phase approvals/holds, meeting open/close summaries.

Related but not the same:

- `#프로젝트-허브` (`1507235292694974645`): project thread/index, MeetingRun links, phase artifact links.
- `#전체-공지` (`1505931688327381042`): cross-team announcements and handoff notices.
- `#전체-리뷰` (`1507063654397378561`): QA/risk/release gate verdicts.

## Why it was confusing

The previous name `#전략-회의실` was accurate for final decision meetings, but users looking for “the meeting channel” could not immediately identify the entrypoint. The project also has several adjacent surfaces with overlapping mental models:

- `#회의실-전략결정` is now the explicit discussion/decision entrypoint.
- `#프로젝트-허브` tracks MeetingRun/thread/artifact links.
- `#전체-공지` handles cross-team visibility.
- Team home channels such as `#기술-메인` handle department-specific work.

The functional split remains correct; the rename makes the meeting entrypoint explicit.

## Recommended final structure

Keep the 11-channel topology, but clarify names and ordering. Do not add more channels unless a real routing feature needs them.

### Category: 📋 경영

1. `#회의실-전략결정`
   - Current: `#회의실-전략결정`
   - ID remains: `1505600167221526621`
   - Purpose: primary meeting entrypoint, decisions, priorities, phase approvals/holds.
   - Bot: `대표` / `aicompanyceo`.

2. `#일일-브리핑`
   - Current: same
   - ID: `1507063720025522267`
   - Purpose: daily dashboard and personal assistant summary.
   - Bot: `비서` / `aicompanyassistant`.

### Category: 🔀 크로스팀

3. `#전체-공지`
   - Current: `#전체-공지`
   - ID remains: `1505931688327381042`
   - Purpose: cross-team announcements and handoffs, not deep discussion.

4. `#프로젝트-허브`
   - Current: same
   - ID: `1507235292694974645`
   - Purpose: project/thread/MeetingRun index and artifact links.

5. `#전체-리뷰`
   - Current: same
   - ID: `1507063654397378561`
   - Purpose: QA, risk, release gate, blocker list.
   - Bot: `품질관리팀장` / `aicompanyquality`.

### Team home channels

Keep these as-is:

- `#콘텐츠-메인` → content planning / `aicompanycontent`
- `#아트-메인` → art and visual review / `aicompanyart`
- `#기술-메인` → implementation, infra, tests, incidents / `aicompanytech`
- `#마케팅-메인` → audience, brand, launch / `aicompanymarketing`

### Category: ⚙️ 관리

Keep these as-is:

- `#마스터-컨트롤` → operator commands, quota, emergency stop, live smoke.
- `#시스템-로그` → sanitized gateway/job/test/deploy digests only.

## Operating rule

Use this routing rule in docs, prompts, and bot responses:

- “회의 열어줘 / 결정하자 / Phase 승인” → `#회의실-전략결정`.
- “이 프로젝트 어디까지 됐어 / 산출물 링크” → `#프로젝트-허브`.
- “전체 공유 / 팀 간 공지” → `#전체-공지`.
- “검토 / 위험 / 출시 가능 여부” → `#전체-리뷰`.
- “구현/테스트/장애” → `#기술-메인`.
- “아트/콘텐츠/마케팅 세부 논의” → each team home channel.
- “봇 상태/쿼터/재시작/스모크” → `#마스터-컨트롤`.
- “자동화 상태 요약 로그” → `#시스템-로그`.

## Applied live mutation set

Applied only these live Discord changes:

1. Rename `#회의실-전략결정` → `#회의실-전략결정`.
2. Rename `#전체-공지` → `#전체-공지`.
3. Reorder channels so categories and text channels appear in this order:
   - 📋 경영
     - `#회의실-전략결정`
     - `#일일-브리핑`
   - 🎬 콘텐츠제작팀
     - `#콘텐츠-메인`
   - 🎨 아트팀
     - `#아트-메인`
   - ⚙️ 기술팀
     - `#기술-메인`
   - 📣 마케팅팀
     - `#마케팅-메인`
   - 🔀 크로스팀
     - `#전체-공지`
     - `#프로젝트-허브`
     - `#전체-리뷰`
   - ⚙️ 관리
     - `#마스터-컨트롤`
     - `#시스템-로그`
4. Do not change permissions.
5. Do not add Administrator.
6. Do not add free-response channels.
7. Keep all 7 bot home-channel IDs unchanged; only names/order change.

## Repo updates required after live mutation

The live rename was applied; keep these files synchronized:

- `src/runtime_architecture_v2/discord_channels.py`
- `docs/discord-channel-function-matrix.md`
- `README.ko.md`

Then run:

```bash
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_discord_channel_matrix.py \
  tests/test_runtime_architecture_v2_projection.py \
  tests/test_runtime_architecture_v2_phase25_command_surface.py \
  tests/test_runtime_architecture_v2_phase26_worker_boundary_smoke.py \
  tests/test_runtime_architecture_v2_phase27_service_supervision.py \
  tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py \
  tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py
```

Policy spot-check:

```bash
PYTHONPATH=src python3 - <<'PY'
from runtime_architecture_v2.discord_channels import current_discord_channel_function_matrix, current_discord_home_channel_ids_by_profile
from runtime_architecture_v2.projection import DiscordLiveBoundaryPolicy
channels = current_discord_channel_function_matrix()
by_name = {c.name: c for c in channels}
assert '회의실-전략결정' in by_name
assert '전체-공지' in by_name
assert '전략-회의실' not in by_name
assert '전체-메인' not in by_name
home = current_discord_home_channel_ids_by_profile()
policy = DiscordLiveBoundaryPolicy.current_verified()
assert home['aicompanyceo'] == '1505600167221526621'
assert policy.allowed_channel_ids_by_profile['aicompanyceo'] == '1505600167221526621'
assert policy.evaluate(profile='aicompanyceo', guild_id='1505600166676271244', channel_id='1505600167221526621').allowed
assert not policy.evaluate(profile='aicompanyceo', guild_id='1505600166676271244', channel_id='1505931688327381042').allowed
print('policy-check: PASS')
PY
```
