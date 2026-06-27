# Discord Multi-Bot Profiles

토큰은 이 문서에 저장하지 않는다. 각 Hermes profile `.env`에만 둔다.

| Profile | Bot | Role | Home Channel | Channel ID |
|---|---|---|---|---|
| `aicompanyceo` | 버추얼컴퍼니-대표 | CEO/Coordinator | #회의실-전략결정 | `1505600167221526621` |
| `aicompanyassistant` | 개인비서-Hermes | Personal Assistant | #일일-브리핑 | `1507063720025522267` |
| `aicompanycontent` | 버추얼컴퍼니-콘텐츠팀장 | Content Lead | #콘텐츠-메인 | `1505927982722580500` |
| `aicompanyart` | 버추얼컴퍼니-아트팀장 | Art Lead | #아트-메인 | `1505928014800752671` |
| `aicompanytech` | 버추얼컴퍼니-기술팀장 | Tech Lead | #기술-메인 | `1505928578016219247` |
| `aicompanymarketing` | 버추얼컴퍼니-마케팅팀장 | Marketing Lead | #마케팅-메인 | `1505931658426060970` |
| `aicompanyquality` | 버추얼컴퍼니-품질관리팀장 | Validation/Audit Lead | #전체-리뷰 | `1507063654397378561` |


## Safety

- `DISCORD_REQUIRE_MENTION=true`
- `DISCORD_THREAD_REQUIRE_MENTION=true`
- free response channel은 초기에는 비워둔다.
- 서버 관리 권한은 사람 계정이 갖고, 봇은 최소 권한 원칙을 따른다.
- 비서봇의 최종 UX target은 `#개인-비서`이지만, 현재 서버에 해당 채널이 없으므로 `#일일-브리핑`을 유지한다. 자세한 결정은 `docs/phase12-assistant-ux.md`를 본다.
