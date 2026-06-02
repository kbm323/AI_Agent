import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import {
  buildHermesRequest,
  buildHermesRequestPostFailureMessage,
  buildFinalSynthesisMessage,
  buildFallbackContext,
  buildOpenClawDraftTimelineMessage,
  buildOrchestrationFailureMessage,
  buildOpenClawSynthesisInstruction,
  buildOpenClawDraft,
  isUsableOpenClawDraft,
  buildEscalationMessage,
  buildResumedFinalSynthesisMessage,
  buildReviewerModeSystemText,
  buildSynthesisSources,
  buildThreadCreationFailureMessage,
  detectOrchestrationIntent,
  evaluateEscalationReasons,
  evaluateHermesStandaloneBlock,
  evaluateStopCondition,
  extractMentionSignals,
  hasOrchestrationMarker,
  hasUnrelatedStaleSynthesisSource,
  reviewerMentionsOpenClawDraft,
  getReviewerReplyExclusionReason,
  isBotAuthor,
  isForbiddenHermesStandaloneMessage,
  isParentChannelLeakageContent,
  isParentChannelTargetId,
  isSimpleCommand,
  normalizeDiscordTargetId,
  prepareThreadOrchestrationFromFacts,
  parseReviewerVerdict,
  capturePendingOpenClawDraftSend,
  consumeParentAutoReplySuppression,
  consumeThreadAutoReplySuppression,
  recordHermesThreadViolation,
  resumeWaitingOrchestrationFromUserDecision,
  resolveConfig,
  runThreadReviewFromFacts,
  selectReviewerReply,
  shouldOrchestrate,
  violatesReviewerMode
} from "../index.js";

const OPENCLAW_ID = "1505917780577357928";
const HERMES_ID = "1505920161956499649";

const config = resolveConfig({
  reviewerName: "Hermes",
  reviewerRoleIds: ["1505923805422293105"],
  reviewerBotIds: [HERMES_ID],
  orchestratorBotIds: [OPENCLAW_ID],
  maxRounds: 1
});

test("detects orchestration marker", () => {
  assert.equal(hasOrchestrationMarker("[OC-IA:abc:round:1]", config.marker), true);
});

test("reviewer prompt forbids fictional participants and fake meetings", () => {
  const prompt = buildReviewerModeSystemText(config);
  assert.match(prompt, /single reviewer agent/i);
  assert.match(prompt, /Do not simulate/i);
  assert.match(prompt, /fictional participants/i);
  assert.match(prompt, /multi-persona dialogue/i);
});

test("Hermes request is strict reviewer-only", () => {
  const request = buildHermesRequest({
    config,
    correlationId: "m1",
    round: 1,
    request: "뮤직비디오 오프닝 장면을 토론해줘",
    openClawDraft: "OpenClaw draft: start with a strong silhouette and one visual hook."
  });
  assert.match(request, /ORCHESTRATION MODE/);
  assert.match(request, /strict reviewer-only/i);
  assert.match(request, /Never invent staff members/i);
  assert.match(request, /Art-house director/i);
  assert.match(request, /K-pop performance director/i);
  assert.match(request, /OpenClaw will post the final synthesis/i);
  assert.match(request, /OpenClaw draft to review:/);
  assert.match(request, /strong silhouette/);
  assert.doesNotMatch(request, /roleplay as a team/i);
});

test("flags fake internal meetings and multi-persona simulation", () => {
  assert.equal(violatesReviewerMode("감독: 폐허 컷으로 갑시다.\n촬영감독: 핸드헬드로 가죠."), true);
  assert.equal(violatesReviewerMode("가상의 내부 회의:\nA: 좋아요\nB: 반대요"), true);
  assert.equal(violatesReviewerMode("Art-house director: slow walk.\nK-pop performance director: add choreo."), true);
  assert.equal(violatesReviewerMode("Experimental visual artist: break the frame."), true);
  assert.equal(violatesReviewerMode("Reviewer view: strong opening, but reduce exposition."), false);
});

test("bad reviewer response cannot satisfy stop condition", () => {
  const stop = evaluateStopCondition({
    text: "A: 좋아요\nB: 반대요",
    round: 1,
    config
  });
  assert.equal(stop.stop, false);
  assert.equal(stop.reason, "reviewer_mode_violation");
});

test("normalizes Discord REST target ids", () => {
  assert.equal(normalizeDiscordTargetId("channel:1505600167221526621"), "1505600167221526621");
  assert.equal(normalizeDiscordTargetId("discord:channel:1505600167221526621"), "1505600167221526621");
  assert.equal(normalizeDiscordTargetId("1508500341937672343"), "1508500341937672343");
  assert.equal(isParentChannelTargetId("channel:1505600167221526621"), true);
  assert.equal(isParentChannelTargetId("discord:channel:1505600167221526621"), true);
  assert.equal(isParentChannelTargetId("1508500341937672343"), false);
});

test("exact NO_REPLY is left for OpenClaw core silent handling", () => {
  assert.equal(isParentChannelLeakageContent("NO_REPLY"), false);
  assert.equal(isParentChannelLeakageContent(" **Final synthesis**\ntext"), true);
  assert.equal(isParentChannelLeakageContent("**OpenClaw draft**\ntext"), true);
});

test("escalation policy ignores ordinary review tasks", () => {
  const reasons = evaluateEscalationReasons({
    request: "랜덤 테스트 요청: 후보를 만들고 리뷰해서 최종안을 정리해줘",
    openClawDraft: "OpenClaw draft: 후보 A, 후보 B, 후보 C.",
    review: "Hermes review: 후보 A/B/C를 비교했고 후보 B 보완을 추천한다."
  });

  assert.deepEqual(reasons, []);
  assert.equal(parseReviewerVerdict("Verdict: needs_user_decision"), "needs_user_decision");
  assert.match(buildEscalationMessage({ reasons: ["brand_or_public_release"] }), /User decision required/);
});

test("reviewer verdict parser uses Phase 2-A partial_agree enum", () => {
  assert.equal(parseReviewerVerdict("Verdict: partial_agree\n보완 후 진행 추천"), "partial_agree");
  assert.equal(parseReviewerVerdict("Verdict: agree_with_changes\nlegacy plugin output"), "partial_agree");
  assert.equal(parseReviewerVerdict("수정 보완하면 동의하고 진행을 추천한다."), "partial_agree");
});

test("escalation policy pauses public release and reviewer decision requests", () => {
  const reasons = evaluateEscalationReasons({
    request: "완성본을 외부 공개하고 실제 게시해줘",
    openClawDraft: "OpenClaw draft: 게시 전 점검이 필요하다.",
    review: "Verdict: needs_user_decision\nHermes review: 공개 전 사용자 승인이 필요하다."
  });

  assert.ok(reasons.includes("brand_or_public_release"));
  assert.ok(reasons.includes("reviewer_requested_user_decision"));
});

test("invalid Discord target id prevents REST sends", async () => {
  let sendCount = 0;
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "channel:not-a-snowflake",
      threadId: "channel:not-a-snowflake",
      messageId: "m-invalid-target",
      currentRequest: "AI 이름 생성해줘"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async () => {
      sendCount += 1;
      return { id: "should-not-send" };
    }
  });
  assert.equal(sendCount, 0);
  assert.equal(result.failureReason, "invalid_discord_target_id");
  assert.ok(logs.find((entry) => entry.event === "orchestration skipped with explicit reason" && entry.details.reason === "invalid Discord channel target id"));
});

test("channel-prefixed parent target creates a real thread before sends", async () => {
  const channelIds = [];
  const sentContents = [];
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "discord:channel:1505600167221526621",
      threadId: "channel:1505600167221526621",
      messageId: "m-normalized-target",
      currentRequest: "캐릭터 컨셉 잡아줘",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ channelId, content }) => {
      channelIds.push(channelId);
      sentContents.push(content);
      if (/ORCHESTRATION MODE/.test(content)) return { id: "request-1", timestamp: "2026-05-26T00:00:05.000Z" };
      return { id: "final-1", timestamp: "2026-05-26T00:00:20.000Z" };
    },
    createThread: async () => ({ id: "1505600167999999999", name: "Agent discussion: AI 이름 생성해줘" }),
    waitForReview: async () => ({
      messageId: "review-1",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "Reviewer view: concise critique, alternatives, risks, and recommendation."
    })
  });
  assert.equal(result.finalMessageId, "final-1");
  assert.deepEqual(channelIds, ["1505600167221526621", "1505600167999999999", "1505600167999999999", "1505600167999999999"]);
  assert.match(sentContents[0], /Agent discussion started -> <#1505600167999999999>/);
  assert.doesNotMatch(sentContents[0], /OpenClaw draft|ORCHESTRATION MODE|Final synthesis/);
  assert.ok(logs.find((entry) => entry.event === "normalized discord target id" && entry.details.raw === "discord:channel:1505600167221526621"));
  assert.ok(logs.find((entry) => entry.event === "parent channel request detected"));
  assert.ok(logs.find((entry) => entry.event === "auto thread creation requested"));
  assert.ok(logs.find((entry) => entry.event === "auto thread created"));
  assert.ok(logs.find((entry) => entry.event === "orchestration target switched"));
  assert.ok(logs.find((entry) => entry.event === "orchestration started" && entry.details.threadId === "1505600167999999999"));
});

test("channel-prefixed parent target never starts orchestration before auto-thread request", async () => {
  const logs = [];
  await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1505600167221526621",
      threadId: "channel:1505600167221526621",
      messageId: "m-order",
      currentRequest: "AI 이름 생성해줘",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    createThread: async () => ({ id: "1505600167999999999", name: "Agent discussion: AI 이름 생성해줘" }),
    sendMessage: async ({ content }) => {
      if (/ORCHESTRATION MODE/.test(content)) return { id: "request-order", timestamp: "2026-05-26T00:00:05.000Z" };
      return { id: "final-order", timestamp: "2026-05-26T00:00:20.000Z" };
    },
    waitForReview: async () => ({
      messageId: "review-order",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "Reviewer view: 라온 is the strongest draft candidate, with concise critique, alternatives, risks, and recommendation."
    })
  });

  const autoRequestIndex = logs.findIndex((entry) => entry.event === "auto thread creation requested");
  const startedIndex = logs.findIndex((entry) => entry.event === "orchestration started");
  assert.ok(autoRequestIndex >= 0);
  assert.ok(startedIndex > autoRequestIndex);
});

test("thread creation failure stops before Hermes request and Final synthesis", async () => {
  const sent = [];
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1505600167221526621",
      threadId: "channel:1505600167221526621",
      messageId: "m-thread-fail",
      currentRequest: "AI 이름 생성해줘"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    createThread: async () => {
      throw new Error("Discord API 403: Missing Permissions");
    },
    sendMessage: async ({ channelId, content }) => {
      sent.push({ channelId, content });
      return { id: "failure-notice" };
    }
  });

  assert.equal(result.failureReason, "auto_thread_creation_failed");
  assert.deepEqual(sent, [{ channelId: "1505600167221526621", content: buildThreadCreationFailureMessage() }]);
  assert.equal(logs.some((entry) => entry.event === "orchestration started"), false);
  assert.equal(sent.some((entry) => /ORCHESTRATION MODE/.test(entry.content)), false);
  assert.equal(sent.some((entry) => /Final synthesis/.test(entry.content)), false);
});

test("orchestrator detects multi-mention discuss intent", () => {
  assert.equal(shouldOrchestrate(`<@${OPENCLAW_ID}> <@${HERMES_ID}> 서로 토론해줘`, config), true);
  assert.equal(shouldOrchestrate("그냥 정리해줘", config), true);
});

test("detects Hermes raw bot mention", () => {
  const signals = extractMentionSignals(`<@${OPENCLAW_ID}> <@${HERMES_ID}> 서로 검토해줘`, config);
  assert.deepEqual(signals.detectedIds, [OPENCLAW_ID, HERMES_ID]);
  assert.equal(signals.reviewerMention, true);
  assert.equal(signals.orchestratorMention, true);
});

test("detects Hermes bang bot mention", () => {
  const intent = detectOrchestrationIntent(`<@!${OPENCLAW_ID}> <@!${HERMES_ID}> 서로 검토해줘`, config);
  assert.equal(intent.detected, true);
  assert.deepEqual(intent.detectedIds, [OPENCLAW_ID, HERMES_ID]);
});

test("detects Hermes display name mention", () => {
  const intent = detectOrchestrationIntent("@버추얼컴퍼니-OpenClaw @버추얼컴퍼니-Hermes 캐릭터 비주얼 컨셉아트 방향을 서로 검토해줘.", config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reviewerByName, true);
  assert.equal(intent.orchestratorByName, true);
});

test("detects reviewerBotIds from metadata mention arrays", () => {
  const intent = detectOrchestrationIntent({
    content: "캐릭터 비주얼 컨셉아트 방향을 서로 검토해줘.",
    metadata: {
      mentions: [
        { id: OPENCLAW_ID, username: "버추얼컴퍼니-OpenClaw" },
        { id: HERMES_ID, username: "버추얼컴퍼니-Hermes" }
      ]
    }
  }, config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reviewerByBotId, true);
  assert.equal(intent.orchestratorByBotId, true);
});

test("does not orchestrate when only OpenClaw is mentioned and allows direct OpenClaw", () => {
  const intent = detectOrchestrationIntent(`<@${OPENCLAW_ID}> 서로 검토해줘`, config);
  assert.equal(intent.detected, false);
  assert.equal(intent.reason, "single OpenClaw direct mention");
  assert.equal(intent.directMention, "openclaw");
});

test("does not orchestrate when only Hermes is mentioned and allows direct Hermes", () => {
  const intent = detectOrchestrationIntent(`<@${HERMES_ID}> 서로 검토해줘`, config);
  assert.equal(intent.detected, false);
  assert.equal(intent.reason, "single Hermes direct mention");
  assert.equal(intent.directMention, "hermes");
});

test("orchestrates when OpenClaw and Hermes are mentioned with review intent", () => {
  const intent = detectOrchestrationIntent(`<@${OPENCLAW_ID}> <@${HERMES_ID}> 캐릭터 비주얼 컨셉아트 방향을 서로 검토해줘.`, config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reason, "multi-bot discussion intent detected");
});

test("no mention character worldbuilding defaults to orchestration", () => {
  const intent = detectOrchestrationIntent("캐릭터 세계관 잡아줘", config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reason, "default orchestration enabled by channel policy");
  assert.equal(intent.defaultChannelPolicy, true);
});

test("no mention music video opening idea defaults to orchestration", () => {
  const intent = detectOrchestrationIntent("뮤직비디오 오프닝 아이디어 내줘", config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reason, "default orchestration enabled by channel policy");
});

test("no mention video idea recommendation defaults to orchestration", () => {
  const intent = detectOrchestrationIntent("영상 아이디어 추천해줘", config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reason, "default orchestration enabled by channel policy");
});

test("plain Hermes role name in a task request does not skip orchestration", () => {
  const intent = detectOrchestrationIntent("완성본을 외부 공개하고 실제 게시해줘. 후보를 만들고 Hermes가 리뷰한 뒤 최종 정리해줘", config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reason, "default orchestration enabled by channel policy");
  assert.equal(intent.reviewerByName, true);
});

test("ping simple command does not orchestrate", () => {
  const intent = detectOrchestrationIntent("ping", config);
  assert.equal(intent.detected, false);
  assert.equal(intent.reason, "simple command");
  assert.equal(intent.simpleCommand, true);
  assert.equal(isSimpleCommand("ping", config), true);
});

test("bot author message does not orchestrate", () => {
  const intent = detectOrchestrationIntent({
    content: "캐릭터 세계관 잡아줘",
    senderId: OPENCLAW_ID
  }, config);
  assert.equal(intent.detected, false);
  assert.equal(intent.reason, "bot author ignored");
  assert.equal(intent.botAuthor, true);
  assert.equal(isBotAuthor({ senderId: OPENCLAW_ID }, config), true);
});

test("no mention concept request starts OpenClaw orchestration and standalone clarify is blocked", () => {
  const intent = detectOrchestrationIntent("캐릭터 컨셉 잡아줘", config);
  assert.equal(intent.detected, true);
  assert.equal(intent.reason, "default orchestration enabled by channel policy");
  const block = evaluateHermesStandaloneBlock({
    text: "clarify: 어떤 스타일을 원하시나요? needs your input",
    messages: [],
    marker: config.marker
  });
  assert.equal(block.blocked, true);
  assert.match(block.reason, /Hermes standalone response blocked/);
});

test("Hermes needs-your-input standalone response is blocked", () => {
  assert.equal(isForbiddenHermesStandaloneMessage("This needs your input. Please choose one option."), true);
  const block = evaluateHermesStandaloneBlock({
    text: "This needs your input. Please choose one option.",
    messages: [],
    marker: config.marker
  });
  assert.equal(block.blocked, true);
  assert.match(block.instruction, /NO_REPLY/);
});

test("Hermes reviewer request marker allows reviewer response", () => {
  const block = evaluateHermesStandaloneBlock({
    text: "Reviewer view: stronger silhouette, fewer colors, one material contrast.",
    messages: [{ role: "user", content: "[OC-IA:m1:round:1] ORCHESTRATION MODE" }],
    marker: config.marker
  });
  assert.equal(block.blocked, false);
  assert.equal(block.allowedReviewerOnly, true);
});

test("OpenClaw final instruction labels synthesis", () => {
  const instruction = buildOpenClawSynthesisInstruction(config, {
    stop: { reason: "max_rounds", confidence: 0.5, converged: false }
  });
  assert.match(instruction, /Final synthesis/);
  assert.match(instruction, /OpenClaw is the orchestrator/);
});

test("same-thread final synthesis message is generated after reviewer reply", () => {
  const final = buildFinalSynthesisMessage({
    config,
    request: "뮤직비디오 오프닝 장면을 토론해줘",
    openClawDraft: "OpenClaw draft: use a detail shot first, then reveal the character.",
    reviews: [{ text: "Reviewer view: start with a detail, then reveal the character." }],
    stop: { reason: "max_rounds", confidence: 0.5, converged: false }
  });
  assert.match(final, /\*\*Final synthesis\*\*/);
  assert.match(final, /Sources: OpenClaw draft \+ Hermes review stored in SQLite/);
  assert.match(final, /Hermes cue/);
  assert.match(final, /Final recommendation/);
});

test("compact OpenClaw draft timeline avoids full draft repetition", () => {
  const message = buildOpenClawDraftTimelineMessage({
    openClawDraft: `OpenClaw draft:\n${"긴 초안 내용 ".repeat(80)}끝부분`,
    compact: true
  });
  assert.match(message, /\*\*OpenClaw draft\*\*/);
  assert.match(message, /Full draft stored in SQLite/);
  assert.ok(message.length < 500);
  assert.doesNotMatch(message, /끝부분/);
});

test("request B final synthesis does not reuse request A stale template content", () => {
  const requestAFinal = buildFinalSynthesisMessage({
    config,
    request: "폐허도시 오프닝",
    openClawDraft: "OpenClaw draft: 폐허도시 오프닝 후보",
    reviews: [{ text: "Reviewer view: 폐허도시 오프닝은 롱샷보다 작은 디테일에서 시작하는 편이 좋다." }],
    stop: { reason: "max_rounds", confidence: 0.5, converged: false }
  });
  const requestBFinal = buildFinalSynthesisMessage({
    config,
    request: "AI 이름 생성",
    openClawDraft: "OpenClaw draft: Draft candidates: 네오, 라온, 제논",
    reviews: [{ text: "AI 이름 후보 리뷰: 네오, 라온, 제논 중에서는 라온이 친근하고 제논이 기술적이다." }],
    stop: { reason: "confidence_threshold", confidence: 0.8, converged: true }
  });

  assert.match(requestAFinal, /폐허도시/);
  assert.match(requestBFinal, /AI 이름 생성/);
  assert.match(requestBFinal, /네오|라온|제논/);
  assert.doesNotMatch(requestBFinal, /폐허도시|오프닝|롱샷/);
  assert.ok(requestBFinal.length <= 1900);
});

test("synthesis source validation blocks unrelated stale source terms", () => {
  const cleanSources = buildSynthesisSources({
    request: "AI 이름 생성",
    openClawDraft: "OpenClaw draft: Draft candidates: 라온, 제논",
    reviews: [{ text: "AI 이름 후보 리뷰: 라온과 제논을 비교한다." }]
  });
  const staleSources = buildSynthesisSources({
    request: "AI 이름 생성",
    openClawDraft: "OpenClaw draft: Draft candidates: 라온, 제논",
    reviews: [{ text: "폐허도시 롱샷으로 시작한다." }]
  });

  assert.equal(hasUnrelatedStaleSynthesisSource(cleanSources), false);
  assert.equal(hasUnrelatedStaleSynthesisSource(staleSources), true);
});

test("Hermes response must mention at least one OpenClaw draft candidate", () => {
  const draft = "OpenClaw draft:\nDraft candidates: 후보A, 후보B, 후보C";

  assert.equal(reviewerMentionsOpenClawDraft("전혀 다른 후보를 추천한다.", draft), false);
  assert.equal(reviewerMentionsOpenClawDraft("후보A는 기준이 분명하고 후보B는 리스크가 있다.", draft), true);
});

test("missing captured OpenClaw draft never creates dummy candidates", () => {
  const draft = buildOpenClawDraft({ request: "랜덤 테스트 요청" });

  assert.equal(draft, "");
  assert.equal(isUsableOpenClawDraft(draft, "랜덤 테스트 요청"), false);
  assert.equal(isUsableOpenClawDraft("랜덤 테스트 요청", "랜덤 테스트 요청"), false);
  assert.equal(isUsableOpenClawDraft("OpenClaw draft:\nDraft candidates: 라온, 제논", "랜덤 테스트 요청"), true);
});

test("reviewer response that ignores OpenClaw draft candidates is invalid", async () => {
  const sent = [];
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-random-invalid",
      currentRequest: "랜덤 테스트 요청",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ content }) => {
      sent.push(content);
      if (sent.length === 2) return { id: "request-random", timestamp: "2026-05-26T00:00:05.000Z" };
      return { id: "failure-random", timestamp: "2026-05-26T00:00:20.000Z" };
    },
    waitForReview: async () => ({
      messageId: "review-random",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "전혀 다른 내용을 추천"
    })
  });

  assert.equal(result.reviews.length, 0);
  assert.equal(result.failureReason, "review_did_not_reference_openclaw_draft");
  assert.ok(logs.find((entry) => entry.event === "orchestration skipped with explicit reason" && entry.details.reason === "Hermes response did not mention OpenClaw draft"));
  assert.match(sent[0], /라온, 제논/);
  assert.match(sent[1], /OpenClaw draft to review/);
  assert.match(sent[2], /Final synthesis unavailable/);
});

test("timeout/failure message is visible when reviewer reply is unavailable", () => {
  assert.match(buildOrchestrationFailureMessage({ reason: "reviewer_timeout" }), /Final synthesis unavailable/);
  assert.match(buildOrchestrationFailureMessage({ reason: "reviewer_mode_violation" }), /reviewer-only/);
});

test("intent path logs orchestration started before native hook relay failure", async () => {
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m1",
      currentRequest: `<@${OPENCLAW_ID}> <@${HERMES_ID}> 서로 검토해줘`,
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async () => {
      if (logs.some((entry) => entry.event === "OpenClaw draft posted")) {
        throw new Error("native hook relay not found");
      }
      return { id: "draft-1", timestamp: "2026-05-26T00:00:04.000Z" };
    }
  });
  assert.equal(result.failureReason, "hermes_request_post_failed");
  assert.ok(logs.find((entry) => entry.event === "orchestration started"));
  assert.ok(logs.find((entry) => entry.event === "orchestration failed" && entry.details.reason === "native hook relay not available"));
});

test("Hermes request post failure leaves visible failure when fallback send works", async () => {
  const sent = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m2",
      currentRequest: `<@${OPENCLAW_ID}> <@${HERMES_ID}> 서로 검토해줘`,
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: () => {},
    sendMessage: async ({ content }) => {
      sent.push(content);
      if (sent.length === 2) throw new Error("Discord API 403");
      return { id: "visible-failure-message" };
    }
  });
  assert.equal(result.failureReason, "hermes_request_post_failed");
  assert.equal(result.finalMessageId, "visible-failure-message");
  assert.equal(sent[2], buildHermesRequestPostFailureMessage());
});

test("Hermes request post failure creates model fallback context when visible send also fails", async () => {
  let sentCount = 0;
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m3",
      currentRequest: `<@${OPENCLAW_ID}> <@${HERMES_ID}> 서로 검토해줘`,
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: () => {},
    sendMessage: async () => {
      sentCount += 1;
      if (sentCount === 1) return { id: "draft-1" };
      throw new Error("Discord API unavailable");
    }
  });
  assert.equal(result.failureReason, "hermes_request_post_failed");
  assert.match(result.fallbackContext, /Hermes could not be called directly/);
  assert.match(result.fallbackContext, /Final synthesis/);
});

test("fallback context separates temporary reviewer view and final synthesis instruction", () => {
  const fallback = buildFallbackContext({
    config,
    reason: "Hermes reviewer request could not be posted.",
    threadId: "t1",
    messageId: "m4"
  });
  assert.match(fallback, /Hermes could not be called directly/);
  assert.match(fallback, /reviewer perspective/);
  assert.match(fallback, /Final synthesis/);
});

function hermesMessage(id, content, timestamp = "2026-05-26T00:00:10.000Z") {
  return {
    id,
    content,
    timestamp,
    author: { id: HERMES_ID, username: "Hermes", bot: true }
  };
}

function openClawMessage(id, content, timestamp = "2026-05-26T00:00:10.000Z") {
  return {
    id,
    content,
    timestamp,
    author: { id: OPENCLAW_ID, username: "OpenClaw", bot: true }
  };
}

const reviewerSelectParams = {
  requestMessageId: "request-1",
  requestCreatedAtMs: Date.parse("2026-05-26T00:00:05.000Z"),
  marker: "[OC-IA:m1:round:1]",
  config
};

test("excludes Stopped reviewer system message", () => {
  assert.equal(getReviewerReplyExclusionReason(hermesMessage("h1", "⚡ Stopped. You can continue this session."), reviewerSelectParams), "system/control message");
});

test("excludes needs your input reviewer system message", () => {
  assert.equal(getReviewerReplyExclusionReason(hermesMessage("h1", "This session needs your input before continuing."), reviewerSelectParams), "system/control message");
});

test("excludes session_search reviewer tool/control message", () => {
  assert.equal(getReviewerReplyExclusionReason(hermesMessage("h1", "session_search: previous context lookup"), reviewerSelectParams), "system/control message");
});

test("excludes Hermes skill_view control message", () => {
  assert.equal(getReviewerReplyExclusionReason(hermesMessage("h1", '📚 skill_view: "plan"'), reviewerSelectParams), "system/control message");
});

test("excludes Hermes available-skills control message", () => {
  assert.equal(getReviewerReplyExclusionReason(
    hermesMessage("h1", "Looking at the available skills, I don't see anything directly relevant to reviewing creative illustration concept recommendations — this is a pure review task."),
    reviewerSelectParams
  ), "system/control message");
});

test("selects only actual review after request", () => {
  const logs = [];
  const selected = selectReviewerReply([
    hermesMessage("old", "Reviewer view: old message should not count.", "2026-05-26T00:00:01.000Z"),
    openClawMessage("oc", "OpenClaw message should not count.", "2026-05-26T00:00:06.000Z"),
    hermesMessage("request-1", "[OC-IA:m1:round:1] ORCHESTRATION MODE", "2026-05-26T00:00:05.000Z"),
    hermesMessage("stopped", "⚡ Stopped. You can continue this session.", "2026-05-26T00:00:08.000Z"),
    hermesMessage("review", "Reviewer view: focus the concept art on silhouette, material contrast, and one strong color accent.", "2026-05-26T00:00:12.000Z")
  ], {
    ...reviewerSelectParams,
    logger: (event, details) => logs.push({ event, details })
  });
  assert.equal(selected.messageId, "review");
  assert.match(selected.text, /silhouette/);
  assert.ok(logs.find((entry) => entry.event === "Hermes candidate excluded" && entry.details.messageId === "stopped"));
  assert.ok(logs.find((entry) => entry.event === "Hermes reviewer reply selected" && entry.details.messageId === "review"));
});

test("excludes previous Hermes messages before request", () => {
  const selected = selectReviewerReply([
    hermesMessage("old", "Reviewer view: old but plausible review.", "2026-05-26T00:00:01.000Z")
  ], reviewerSelectParams);
  assert.equal(selected, null);
});

test("selects Hermes reply by snowflake order even when timestamp appears early", () => {
  const selected = selectReviewerReply([
    hermesMessage("1509246188145213511", "Reviewer view: 라온 후보를 OpenClaw draft 기준으로 검토한다.", "2026-05-26T00:00:04.000Z")
  ], {
    ...reviewerSelectParams,
    requestMessageId: "1509246188145213510",
    requestCreatedAtMs: Date.parse("2026-05-26T00:00:05.000Z")
  });
  assert.equal(selected.messageId, "1509246188145213511");
});

test("posts unavailable when no valid reviewer reply is found", async () => {
  const sent = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-no-review",
      currentRequest: `<@${OPENCLAW_ID}> <@${HERMES_ID}> 서로 검토해줘`,
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config: { ...config, waitMs: 5, pollMs: 1000 },
    logger: () => {},
    sendMessage: async ({ content }) => {
      sent.push(content);
      if (sent.length === 2) return { id: "request-1", timestamp: "2026-05-26T00:00:05.000Z" };
      return { id: "failure-1", timestamp: "2026-05-26T00:00:20.000Z" };
    },
    waitForReview: async () => null,
    runCliReview: async () => ""
  });
  assert.equal(result.reviews.length, 0);
  assert.equal(result.stop.reason, "reviewer_timeout");
  assert.match(sent[2], /Final synthesis unavailable/);
});

test("Hermes CLI fallback is used when Discord polling times out", async () => {
  const sent = [];
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-cli-fallback",
      currentRequest: "랜덤 테스트 요청",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ content }) => {
      sent.push(content);
      return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
    },
    waitForReview: async () => null,
    runCliReview: async () => "Reviewer view: 라온 후보는 명확하고 제논 후보는 리스크가 있다."
  });

  assert.equal(result.failureReason, undefined);
  assert.equal(result.reviews.length, 1);
  assert.ok(logs.find((entry) => entry.event === "Hermes Discord polling timed out; trying CLI fallback"));
  assert.ok(logs.find((entry) => entry.event === "Hermes reply detected" && entry.details.route === "cli-fallback"));
  assert.match(sent[2], /\*\*Hermes review\*\*/);
  assert.match(sent[3], /\*\*Final synthesis\*\*/);
});

test("Hermes CLI fallback can post review through reviewer visual identity adapter", async () => {
  const sent = [];
  const reviewerPosts = [];
  const logs = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "openclaw-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-reviewer-post",
      currentRequest: "랜덤 테스트 요청",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ content }) => {
      sent.push(content);
      return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
    },
    sendReviewerMessage: async ({ token, channelId, content }) => {
      reviewerPosts.push({ token, channelId, content });
      return { id: "hermes-visible-1", timestamp: "2026-05-26T00:00:06.000Z" };
    },
    waitForReview: async () => null,
    runCliReview: async () => "Reviewer view: 라온 후보는 명확하고 제논 후보는 리스크가 있다."
  });

  assert.equal(result.failureReason, undefined);
  assert.equal(result.reviews.length, 1);
  assert.equal(result.reviews[0].messageId, "hermes-visible-1");
  assert.equal(reviewerPosts.length, 1);
  assert.equal(reviewerPosts[0].token, "openclaw-token");
  assert.match(reviewerPosts[0].content, /\*\*Hermes review\*\*/);
  assert.match(sent[2], /\*\*Final synthesis\*\*/);
  assert.ok(logs.find((entry) => entry.event === "Hermes reply detected" && entry.details.route === "cli-fallback"));
});

test("long Hermes request uses internal executor instead of failing Discord length limit", async () => {
  const sent = [];
  const logs = [];
  const longDraft = `OpenClaw draft:\nDraft candidates: 라온, 제논\n${"긴 초안 내용 ".repeat(190)}끝부분`;
  let cliPrompt = "";
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-long-cli",
      currentRequest: "긴 초안을 리뷰해줘",
      openClawDraft: longDraft
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ content }) => {
      sent.push(content);
      assert.ok(content.length <= 1900, `Discord message too long: ${content.length}`);
      return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
    },
    waitForReview: async () => {
      throw new Error("Discord polling should not run for oversized reviewer request");
    },
    runCliReview: async (prompt) => {
      cliPrompt = prompt;
      return "Reviewer view: 라온 후보는 명확하고 제논 후보는 리스크가 있다.";
    }
  });

  assert.equal(result.failureReason, undefined);
  assert.equal(result.reviews.length, 1);
  assert.match(cliPrompt, /OpenClaw draft to review:/);
  assert.match(cliPrompt, /끝부분/);
  assert.ok(logs.find((entry) => entry.event === "reviewer request includes captured draft"));
  assert.ok(logs.find((entry) => entry.event === "Hermes reviewer request exceeds Discord limit; using internal executor"));
  assert.ok(logs.find((entry) => entry.event === "Hermes reply detected" && entry.details.route === "internal-executor"));
  assert.match(sent[1], /internal CLI\/API/);
  assert.match(sent[2], /\*\*Hermes review\*\*/);
  assert.match(sent[3], /\*\*Final synthesis\*\*/);
});

test("successful orchestration logs OpenClaw owner and creates final synthesis", async () => {
  const logs = [];
  const sent = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-success",
      currentRequest: "랜덤 테스트 요청",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ content }) => {
      sent.push(content);
      if (sent.length === 2) return { id: "request-1", timestamp: "2026-05-26T00:00:05.000Z" };
      return { id: "final-1", timestamp: "2026-05-26T00:00:20.000Z" };
    },
    waitForReview: async () => ({
      messageId: "review-1",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "Reviewer view: 라온 is the strongest OpenClaw draft candidate; 제논 has a clear risk."
    })
  });
  assert.equal(result.finalMessageId, "final-1");
  assert.equal(result.reviews.length, 1);
  assert.ok(logs.find((entry) => entry.event === "orchestration owner=OpenClaw"));
  assert.match(sent[0], /\*\*OpenClaw draft\*\*/);
  assert.match(sent[0], /Full draft stored in SQLite/);
  assert.match(sent[1], /OpenClaw draft to review:/);
  assert.match(sent[2], /\*\*Final synthesis\*\*/);
});

test("escalation pauses orchestration before final synthesis", async () => {
  const logs = [];
  const sent = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1508500341937672343",
      threadId: "1508500341937672343",
      messageId: "m-escalation",
      currentRequest: "완성본을 외부 공개하고 실제 게시해줘",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
    },
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    sendMessage: async ({ content }) => {
      sent.push(content);
      return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
    },
    waitForReview: async () => ({
      messageId: "review-escalation",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "Verdict: needs_user_decision\nHermes review: 라온 후보와 제논 후보 모두 실제 게시 전 사용자 승인이 필요하다."
    })
  });

  assert.equal(result.stop.reason, "waiting_for_user");
  assert.ok(result.escalationReasons.includes("brand_or_public_release"));
  assert.ok(result.escalationReasons.includes("reviewer_requested_user_decision"));
  assert.match(sent[2], /\*\*User decision required\*\*/);
  assert.doesNotMatch(sent.join("\n"), /\*\*Final synthesis\*\*/);
  assert.ok(logs.find((entry) => entry.event === "User decision required"));
});

test("user decision in waiting thread resumes final synthesis", async () => {
  const dir = mkdtempSync(join(tmpdir(), "iao-resume-"));
  const dbPath = join(dir, "state.sqlite");
  try {
    const persistentConfig = resolveConfig({
      reviewerName: "Hermes",
      reviewerRoleIds: ["1505923805422293105"],
      reviewerBotIds: [HERMES_ID],
      orchestratorBotIds: [OPENCLAW_ID],
      maxRounds: 1,
      stateDbPath: dbPath
    });
    const sent = [];
    const logs = [];
    await runThreadReviewFromFacts({
      api: { config: { channels: { discord: { token: "test-token" } } } },
      facts: {
        channelId: "1508500341937672343",
        threadId: "1508500341937672343",
        messageId: "m-resume",
        currentRequest: "완성본을 외부 공개하고 실제 게시해줘",
        openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
      },
      config: persistentConfig,
      logger: (event, details = {}) => logs.push({ event, details }),
      sendMessage: async ({ content }) => {
        sent.push(content);
        return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
      },
      waitForReview: async () => ({
        messageId: "review-resume",
        authorId: HERMES_ID,
        authorName: "Hermes",
        text: "Hermes review: 라온 후보와 제논 후보 모두 공개 전 승인 후 진행해야 한다."
      })
    });

    const resumed = await resumeWaitingOrchestrationFromUserDecision({
      api: { config: { channels: { discord: { token: "test-token" } } } },
      config: persistentConfig,
      logger: (event, details = {}) => logs.push({ event, details }),
      event: {
        threadId: "channel:1508500341937672343",
        messageId: "m-user-decision",
        senderId: "307374050282307584",
        content: "승인합니다. 라온 방향으로 최종 정리해줘."
      },
      sendMessage: async ({ content }) => {
        sent.push(content);
        return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:01:05.000Z" };
      }
    });

    assert.equal(resumed.status, "completed");
    assert.match(sent.at(-1), /\*\*Final synthesis\*\*/);
    assert.match(sent.at(-1), /User decision applied:/);
    assert.ok(logs.find((entry) => entry.event === "User decision received"));
    const suppression = consumeThreadAutoReplySuppression("channel:1508500341937672343");
    assert.equal(suppression.reason, "resumed_user_decision");
    assert.equal(consumeThreadAutoReplySuppression("1508500341937672343"), undefined);

    const db = new DatabaseSync(dbPath);
    try {
      const task = db.prepare("SELECT status, final_message_id FROM orchestration_tasks WHERE id = ?").get("m-resume");
      assert.equal(task.status, "completed");
      const turns = db.prepare("SELECT kind FROM orchestration_turns WHERE task_id = ? ORDER BY rowid ASC").all("m-resume");
      assert.deepEqual(turns.map((turn) => turn.kind), ["owner_draft", "review_request", "review", "escalation", "user_decision", "final_synthesis"]);
    } finally {
      db.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("Hermes same-thread violation waits for user and can resume", async () => {
  const dir = mkdtempSync(join(tmpdir(), "iao-thread-violation-"));
  const dbPath = join(dir, "state.sqlite");
  try {
    const persistentConfig = resolveConfig({
      reviewerName: "Hermes",
      reviewerRoleIds: ["1505923805422293105"],
      reviewerBotIds: [HERMES_ID],
      orchestratorBotIds: [OPENCLAW_ID],
      maxRounds: 1,
      stateDbPath: dbPath
    });
    const sent = [];
    const logs = [];

    const violation = await recordHermesThreadViolation({
      api: { config: { channels: { discord: { token: "test-token" } } } },
      config: persistentConfig,
      logger: (event, details = {}) => logs.push({ event, details }),
      task: {
        id: "m-thread-violation",
        parentChannelId: "1508500341937672343",
        threadId: "1508500341937672999",
        messageId: "m-thread-violation",
        userRequest: "같은 thread에서 Hermes 리뷰를 받아 최종안을 정리해줘",
        correlationId: "m-thread-violation-correlation"
      },
      observedThreadId: "1508500341937672888",
      openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논",
      sendMessage: async ({ channelId, content }) => {
        sent.push({ channelId, content });
        return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
      }
    });

    assert.equal(violation.status, "waiting_for_user");
    assert.equal(violation.threadId, "1508500341937672999");
    assert.deepEqual(sent.map((message) => message.channelId), ["1508500341937672999"]);
    assert.match(sent[0].content, /Hermes replied outside the task thread/);
    assert.ok(logs.find((entry) => entry.event === "User decision required"));

    let db = new DatabaseSync(dbPath);
    try {
      const task = db.prepare("SELECT status, failure_reason FROM orchestration_tasks WHERE id = ?").get("m-thread-violation");
      assert.deepEqual({ ...task }, {
        status: "waiting_for_user",
        failure_reason: "hermes_wrong_thread"
      });
      const turns = db.prepare("SELECT kind FROM orchestration_turns WHERE task_id = ? ORDER BY rowid ASC").all("m-thread-violation");
      assert.deepEqual(turns.map((turn) => turn.kind), ["owner_draft", "escalation"]);
    } finally {
      db.close();
    }

    const resumed = await resumeWaitingOrchestrationFromUserDecision({
      api: { config: { channels: { discord: { token: "test-token" } } } },
      config: persistentConfig,
      logger: (event, details = {}) => logs.push({ event, details }),
      event: {
        threadId: "1508500341937672999",
        messageId: "m-user-decision-after-violation",
        senderId: "307374050282307584",
        content: "Hermes는 잘못된 thread에 답했으니 OpenClaw draft 기준으로 최종 정리해줘."
      },
      sendMessage: async ({ channelId, content }) => {
        sent.push({ channelId, content });
        return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:01:05.000Z" };
      }
    });

    assert.equal(resumed.status, "completed");
    assert.deepEqual(sent.map((message) => message.channelId), ["1508500341937672999", "1508500341937672999"]);
    assert.match(sent.at(-1).content, /\*\*Final synthesis\*\*/);

    db = new DatabaseSync(dbPath);
    try {
      const task = db.prepare("SELECT status, failure_reason FROM orchestration_tasks WHERE id = ?").get("m-thread-violation");
      assert.deepEqual({ ...task }, {
        status: "completed",
        failure_reason: null
      });
      const turns = db.prepare("SELECT kind FROM orchestration_turns WHERE task_id = ? ORDER BY rowid ASC").all("m-thread-violation");
      assert.deepEqual(turns.map((turn) => turn.kind), ["owner_draft", "escalation", "user_decision", "final_synthesis"]);
    } finally {
      db.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("orchestration state is persisted to SQLite", async () => {
  const dir = mkdtempSync(join(tmpdir(), "iao-state-"));
  const dbPath = join(dir, "state.sqlite");
  try {
    const persistentConfig = resolveConfig({
      reviewerName: "Hermes",
      reviewerRoleIds: ["1505923805422293105"],
      reviewerBotIds: [HERMES_ID],
      orchestratorBotIds: [OPENCLAW_ID],
      maxRounds: 1,
      stateDbPath: dbPath
    });
    const result = await runThreadReviewFromFacts({
      api: { config: { channels: { discord: { token: "test-token" } } } },
      facts: {
        channelId: "1508500341937672343",
        threadId: "1508500341937672343",
        messageId: "m-persist",
        currentRequest: "랜덤 테스트 요청",
        openClawDraft: "OpenClaw draft:\nDraft candidates: 라온, 제논"
      },
      config: persistentConfig,
      logger: () => {},
      sendMessage: async ({ content }) => ({ id: content.includes("Final synthesis") ? "final-persist" : `sent-${content.length}`, timestamp: "2026-05-26T00:00:05.000Z" }),
      waitForReview: async () => ({
        messageId: "review-persist",
        authorId: HERMES_ID,
        authorName: "Hermes",
        text: "Reviewer view: 라온 후보는 명확하고 제논 후보는 리스크가 있다."
      })
    });
    assert.equal(result.failureReason, undefined);

    const db = new DatabaseSync(dbPath);
    try {
      const task = db.prepare("SELECT status, thread_id, final_message_id FROM orchestration_tasks WHERE id = ?").get("m-persist");
      assert.deepEqual({ ...task }, {
        status: "completed",
        thread_id: "1508500341937672343",
        final_message_id: "final-persist"
      });
      const turns = db.prepare("SELECT kind, role FROM orchestration_turns WHERE task_id = ? ORDER BY rowid ASC").all("m-persist");
      assert.deepEqual(turns.map((turn) => turn.kind), ["owner_draft", "review_request", "review", "final_synthesis"]);
      assert.deepEqual(turns.map((turn) => turn.role), ["openclaw-owner", "openclaw-owner", "hermes-reviewer", "openclaw-finalizer"]);
    } finally {
      db.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("parent OpenClaw reply is captured, suppressed, and used as real thread draft", async () => {
  const sent = [];
  const logs = [];
  const api = { config: { channels: { discord: { token: "test-token" } } } };
  const facts = {
    channelId: "1505600167221526621",
    threadId: "channel:1505600167221526621",
    messageId: "m-capture",
    currentRequest: "랜덤 테스트 요청"
  };

  const prepared = await prepareThreadOrchestrationFromFacts({
    api,
    facts,
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    createThread: async () => ({ id: "1505600167999999999", name: "Agent discussion: 랜덤 테스트 요청" }),
    waitForReview: async () => ({
      messageId: "review-capture",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "Reviewer view: 라온 후보와 제논 후보를 OpenClaw draft 기준으로 비교한다."
    }),
    sendMessage: async ({ channelId, content }) => {
      sent.push({ channelId, content });
      return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
    }
  });

  assert.equal(prepared.status, "awaiting_draft_capture");
  assert.deepEqual(sent, [{ channelId: "1505600167221526621", content: "Agent discussion started -> <#1505600167999999999>" }]);

  const captured = await capturePendingOpenClawDraftSend({
    api,
    config,
    logger: (event, details = {}) => logs.push({ event, details }),
    event: {
      to: "1505600167221526621",
      content: "실제 OpenClaw draft: 라온과 제논 후보를 비교한다."
    }
  });

  assert.equal(captured.content, "NO_REPLY");
  assert.equal(captured.metadata.interAgentOrchestrationSuppressed, true);
  assert.ok(logs.find((entry) => entry.event === "OpenClaw parent reply intercepted"));
  assert.ok(logs.find((entry) => entry.event === "parent reply suppressed"));
  assert.ok(logs.find((entry) => entry.event === "OpenClaw draft captured"));
});

test("completed parent launcher auto reply is suppressed even without English markers", async () => {
  const sent = [];
  const result = await runThreadReviewFromFacts({
    api: { config: { channels: { discord: { token: "test-token" } } } },
    facts: {
      channelId: "1505600167221526621",
      threadId: "channel:1505600167221526621",
      messageId: "m-parent-suppress",
      currentRequest: "후보 A/B/C를 만들고 Hermes 리뷰를 받아 최종안을 정리해줘",
      openClawDraft: "OpenClaw draft:\n후보 A는 단순 확인, 후보 B는 근거 있는 결론, 후보 C는 분석형 설명입니다."
    },
    config,
    logger: () => {},
    createThread: async () => ({ id: "1505600167999999999", name: "Agent discussion" }),
    sendMessage: async ({ channelId, content }) => {
      sent.push({ channelId, content });
      return { id: `sent-${sent.length}`, timestamp: "2026-05-26T00:00:05.000Z" };
    },
    waitForReview: async () => ({
      messageId: "review-parent-suppress",
      authorId: HERMES_ID,
      authorName: "Hermes",
      text: "Reviewer view: 후보 B가 OpenClaw draft의 후보 중 가장 적합하다."
    })
  });

  assert.equal(Boolean(result.failureReason), false);
  assert.equal(sent.at(-1).channelId, "1505600167999999999");
  const suppression = consumeParentAutoReplySuppression("channel:1505600167221526621");
  assert.equal(suppression.reason, "thread_result_already_posted");
  assert.equal(suppression.threadId, "1505600167999999999");
  assert.equal(consumeParentAutoReplySuppression("1505600167221526621"), undefined);
});
