# Inter-Agent Orchestration Live Test Checklist

Run this after restarting the OpenClaw gateway so the local plugin is loaded.

## Command

```bash
openclaw gateway --port 18789
```

Then send a Discord request that mentions both OpenClaw and Hermes:

```text
@버추얼컴퍼니-OpenClaw
@버추얼컴퍼니-Hermes

뮤직비디오 오프닝 장면 아이디어를 서로 토론해줘.
```

## Expected Flow

1. OpenClaw creates or uses one active Discord thread for the user request.
2. OpenClaw posts a structured Hermes request in that thread with an `[OC-IA:...]` marker.
3. Hermes replies in that same thread as one reviewer only.
4. OpenClaw reads Hermes' reply from that same thread.
5. OpenClaw posts a `Final synthesis` message in that same thread.

## Fail Conditions

- Hermes invents named participants such as `Art-house director`, `K-pop performance director`, or `Experimental visual artist`.
- Hermes uses panel-style dialogue, for example `A:`, `B:`, `감독:`, `프로듀서:`, or any internal meeting transcript.
- OpenClaw does not post `Final synthesis`.
- The Hermes request, Hermes reply, and OpenClaw synthesis appear in different threads.
- OpenClaw silently fails instead of posting `Final synthesis unavailable` when Hermes cannot be read.

