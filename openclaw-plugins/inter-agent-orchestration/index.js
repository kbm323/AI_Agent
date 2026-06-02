import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { execFile } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const DEFAULT_MARKER = "OC-IA";
export const DEFAULT_ORCHESTRATOR_BOT_IDS = ["1505917780577357928"];
export const DEFAULT_REVIEWER_BOT_IDS = ["1505920161956499649"];
export const SIMPLE_COMMAND_BLACKLIST = ["ping", "status", "help", "stop", "reset", "안녕", "테스트", "uptime", "/status", "/help"];
const LIVE_PREFIX = "[IAO-LIVE]";
const LIVE_CODE_VERSION = "thread-canonical-draft-v5";
const DISCORD_SAFE_CONTENT_LIMIT = 1900;

const inFlight = new Map();
const orchestrationResults = new Map();
const orchestrationMessageIds = new Map();
const pendingDraftCaptures = new Map();
const threadAutoReplySuppressions = new Map();
const parentAutoReplySuppressions = new Map();
const stateDbByPath = new Map();

function liveLog(event, details = {}) {
  const suffix = Object.entries(details)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(" ");
  console.log(`${LIVE_PREFIX} ${event}${suffix ? ` ${suffix}` : ""}`);
}

liveLog("plugin module loaded", { version: LIVE_CODE_VERSION });

export function normalizeStringList(value) {
  if (Array.isArray(value)) {
    return value.map((entry) => (typeof entry === "string" ? entry.trim() : "")).filter(Boolean);
  }
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

export function resolveConfig(raw = {}) {
  const reviewerRoleIds = normalizeStringList(raw.reviewerRoleIds ?? raw.reviewerRoleId);
  const reviewerBotIds = uniqueStrings([
    ...DEFAULT_REVIEWER_BOT_IDS,
    ...normalizeStringList(raw.reviewerBotIds ?? raw.reviewerBotId),
    ...normalizeStringList(raw.reviewerUserIds ?? raw.reviewerUserId)
  ]);
  const orchestratorBotIds = uniqueStrings([
    ...DEFAULT_ORCHESTRATOR_BOT_IDS,
    ...normalizeStringList(raw.orchestratorBotIds ?? raw.orchestratorBotId)
  ]);
  const reviewerMentionNames = uniqueStrings([
    "버추얼컴퍼니-Hermes",
    "Hermes",
    ...normalizeStringList(raw.reviewerMentionNames ?? raw.reviewerMentionName)
  ]);
  const orchestratorMentionNames = uniqueStrings([
    "버추얼컴퍼니-OpenClaw",
    "OpenClaw",
    ...normalizeStringList(raw.orchestratorMentionNames ?? raw.orchestratorMentionName)
  ]);
  const reviewerMention =
    typeof raw.reviewerMention === "string" && raw.reviewerMention.trim()
      ? raw.reviewerMention.trim()
      : reviewerBotIds[0]
        ? `<@${reviewerBotIds[0]}>`
        : reviewerRoleIds[0]
          ? `<@&${reviewerRoleIds[0]}>`
          : undefined;
  return {
    enabled: raw.enabled !== false,
    marker: typeof raw.marker === "string" && raw.marker.trim() ? raw.marker.trim() : DEFAULT_MARKER,
    reviewerName: typeof raw.reviewerName === "string" && raw.reviewerName.trim() ? raw.reviewerName.trim() : "Hermes",
    reviewerRoleIds,
    reviewerBotIds,
    reviewerUserIds: reviewerBotIds,
    orchestratorBotIds,
    reviewerMentionNames,
    orchestratorMentionNames,
    reviewerMention,
    reviewerPostTokenEnv: typeof raw.reviewerPostTokenEnv === "string" && raw.reviewerPostTokenEnv.trim() ? raw.reviewerPostTokenEnv.trim() : "HERMES_DISCORD_BOT_TOKEN",
    compactTimeline: raw.compactTimeline !== false,
    statePersistenceEnabled: raw.statePersistenceEnabled !== false,
    stateDbPath: typeof raw.stateDbPath === "string" && raw.stateDbPath.trim()
      ? raw.stateDbPath.trim()
      : process.env.OPENCLAW_IAO_DB_PATH?.trim() || "/home/kbm/.openclaw/inter-agent-orchestration.sqlite",
    reviewerRequestMode: raw.reviewerRequestMode === "discord" ? "discord" : "internal",
    simpleCommandBlacklist: uniqueStrings([
      ...SIMPLE_COMMAND_BLACKLIST,
      ...normalizeStringList(raw.simpleCommandBlacklist)
    ]),
    maxRounds: clampInteger(raw.maxRounds, 1, 5, 1),
    waitMs: clampInteger(raw.waitMs, 5000, 180000, 45000),
    pollMs: clampInteger(raw.pollMs, 1000, 10000, 2500),
    confidenceThreshold: Math.max(0, Math.min(1, Number(raw.confidenceThreshold ?? 0.7) || 0.7))
  };
}

function uniqueStrings(values) {
  return [...new Set(values.map((value) => String(value ?? "").trim()).filter(Boolean))];
}

function clampInteger(value, min, max, fallback) {
  const parsed = Math.floor(Number(value ?? fallback));
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

export function normalizeDiscordTargetId(value) {
  const raw = String(value ?? "").trim();
  if (/^\d{5,25}$/.test(raw)) return raw;
  const channelMatch = raw.match(/^channel:(\d{5,25})$/);
  if (channelMatch) return channelMatch[1];
  const discordChannelMatch = raw.match(/^discord:channel:(\d{5,25})$/);
  if (discordChannelMatch) return discordChannelMatch[1];
  return null;
}

export function isParentChannelTargetId(value) {
  return /^(?:discord:)?channel:\d{5,25}$/.test(String(value ?? "").trim());
}

function promptHasParentChannelTarget(prompt) {
  return /"chat_id"\s*:\s*"channel:\d+"/.test(String(prompt ?? ""))
    || /discord:channel:\d+/.test(String(prompt ?? ""));
}

function isSilentReplyText(text) {
  return String(text ?? "").trim() === "NO_REPLY";
}

export function isParentChannelLeakageContent(text) {
  const raw = String(text ?? "");
  return /\*\*OpenClaw draft\*\*/i.test(raw)
    || /ORCHESTRATION MODE/i.test(raw)
    || /\*\*Final synthesis\*\*/i.test(raw)
    || /Hermes 핵심 리뷰/i.test(raw)
    || /subscription usage limit/i.test(raw)
    || /You've reached your Codex/i.test(raw);
}

function normalizeDiscordTargetForSend(raw, logger = liveLog) {
  const normalized = normalizeDiscordTargetId(raw);
  if (normalized && String(raw ?? "").trim() !== normalized) {
    logger("normalized discord target id", { raw, normalized });
  }
  return normalized;
}

export function hasOrchestrationMarker(text, marker = DEFAULT_MARKER) {
  return new RegExp(`\\[${escapeRegex(marker)}:[^\\]]+\\]`).test(String(text ?? ""));
}

export function hasDiscussionIntent(text) {
  const raw = String(text ?? "");
  return /\b(discuss|debate|talk\s+(?:together|with)|review\s+(?:together|with)|collaborat(?:e|ion)|consult)\b/i.test(raw)
    || /(서로|같이|함께).{0,20}(토론|논의|상의|리뷰|검토)|(?:토론|논의|상의|리뷰|검토).{0,20}(서로|같이|함께)/.test(raw);
}

export function extractDiscordFacts(prompt) {
  const text = String(prompt ?? "");
  const channelId =
    firstMatch(text, /"chat_id"\s*:\s*"channel:(\d+)"/)
    ?? firstMatch(text, /channel id:(\d+)/i)
    ?? firstMatch(text, /"conversation_label"\s*:\s*"#.*?channel id:(\d+)"/);
  const messageId = firstMatch(text, /"message_id"\s*:\s*"(\d+)"/);
  const currentRequest = firstMatch(text, /Current user request:\s*[\s\S]*?\n\n([\s\S]*)$/) ?? text;
  return {
    channelId,
    messageId,
    currentRequest: currentRequest.trim()
  };
}

export function shouldOrchestrate(prompt, config) {
  return detectOrchestrationIntent(prompt, config).detected;
}

export function extractMentionSignals(input, config) {
  const raw = typeof input === "string" ? input : String(input?.content ?? "");
  const metadata = typeof input === "string" ? undefined : input?.metadata;
  const detectedIds = uniqueStrings([
    ...extractUserMentionIds(raw),
    ...extractUserMentionIds(safeStringify(metadata)),
    ...extractIdsFromUnknown(metadata)
  ]);
  const detectedRoleIds = uniqueStrings([
    ...extractRoleMentionIds(raw),
    ...extractRoleMentionIds(safeStringify(metadata))
  ]);
  const rawLower = raw.toLocaleLowerCase();
  const reviewerByName = config.reviewerMentionNames.some((name) => rawLower.includes(name.toLocaleLowerCase()));
  const orchestratorByName = config.orchestratorMentionNames.some((name) => rawLower.includes(name.toLocaleLowerCase()));
  const reviewerByBotId = config.reviewerBotIds.some((id) => detectedIds.includes(id));
  const reviewerByRoleId = config.reviewerRoleIds.some((id) => detectedRoleIds.includes(id) || raw.includes(`<@&${id}>`));
  const orchestratorByBotId = config.orchestratorBotIds.some((id) => detectedIds.includes(id));
  return {
    raw,
    detectedIds,
    detectedRoleIds,
    configuredReviewerIds: config.reviewerBotIds,
    configuredOrchestratorIds: config.orchestratorBotIds,
    configuredReviewerRoleIds: config.reviewerRoleIds,
    reviewerMention: reviewerByBotId || reviewerByRoleId || reviewerByName,
    orchestratorMention: orchestratorByBotId || orchestratorByName,
    reviewerByBotId,
    reviewerByRoleId,
    reviewerByName,
    orchestratorByBotId,
    orchestratorByName
  };
}

export function isSimpleCommand(text, config) {
  const normalized = String(text ?? "")
    .replace(/<@!?\d+>/g, "")
    .replace(/<@&\d+>/g, "")
    .trim()
    .toLocaleLowerCase();
  if (!normalized) return true;
  return config.simpleCommandBlacklist.some((command) => normalized === command.toLocaleLowerCase());
}

export function isBotAuthor(input, config) {
  if (typeof input === "string") return false;
  const authorId = String(input?.authorId ?? input?.senderId ?? input?.metadata?.authorId ?? input?.metadata?.senderId ?? "");
  if (authorId && [...config.orchestratorBotIds, ...config.reviewerBotIds].includes(authorId)) return true;
  if (input?.authorBot === true || input?.isBot === true || input?.metadata?.authorBot === true || input?.metadata?.senderBot === true) return true;
  return false;
}

export function detectOrchestrationIntent(input, config) {
  const signals = extractMentionSignals(input, config);
  const raw = signals.raw;
  if (!config.enabled) return { detected: false, reason: "plugin disabled" };
  if (!config.reviewerMention) return { detected: false, reason: "reviewer mention is not configured" };
  if (isBotAuthor(input, config)) return { detected: false, reason: "bot author ignored", botAuthor: true, ...signals };
  if (hasOrchestrationMarker(raw, config.marker)) return { detected: false, reason: "orchestration marker already present" };
  if (isSimpleCommand(raw, config)) return { detected: false, reason: "simple command", simpleCommand: true, ...signals };
  const directOrchestratorMention = signals.orchestratorByBotId;
  const directReviewerMention = signals.reviewerByBotId || signals.reviewerByRoleId;
  if (directOrchestratorMention && !directReviewerMention) {
    return { detected: false, reason: "single OpenClaw direct mention", directMention: "openclaw", ...signals };
  }
  if (directReviewerMention && !directOrchestratorMention) {
    return { detected: false, reason: "single Hermes direct mention", directMention: "hermes", ...signals };
  }
  if (signals.reviewerMention && signals.orchestratorMention) {
    return { detected: true, reason: "multi-bot discussion intent detected", mentionCount: signals.detectedIds.length, ...signals };
  }
  return { detected: true, reason: "default orchestration enabled by channel policy", defaultChannelPolicy: true, mentionCount: 0, ...signals };
}

function extractUserMentionIds(text) {
  return [...String(text ?? "").matchAll(/<@!?(\d+)>/g)].map((match) => match[1]);
}

function extractRoleMentionIds(text) {
  return [...String(text ?? "").matchAll(/<@&(\d+)>/g)].map((match) => match[1]);
}

function safeStringify(value) {
  const seen = new WeakSet();
  try {
    return JSON.stringify(value ?? {}, (_key, entry) => {
      if (entry && typeof entry === "object") {
        if (seen.has(entry)) return undefined;
        seen.add(entry);
      }
      return entry;
    });
  } catch {
    return "";
  }
}

function extractIdsFromUnknown(value, depth = 0, seen = new WeakSet()) {
  if (depth > 4 || value == null) return [];
  if (typeof value === "string") return extractUserMentionIds(value);
  if (Array.isArray(value)) return value.flatMap((entry) => extractIdsFromUnknown(entry, depth + 1, seen));
  if (typeof value !== "object") return [];
  if (seen.has(value)) return [];
  seen.add(value);
  const ids = [];
  for (const [key, entry] of Object.entries(value)) {
    if (/^(?:id|user_id|bot_id|author_id)$/i.test(key) && typeof entry === "string" && /^\d+$/.test(entry)) ids.push(entry);
    if (/mentions?/i.test(key) || key === "users" || key === "members") ids.push(...extractIdsFromUnknown(entry, depth + 1, seen));
  }
  return ids;
}

export function violatesReviewerMode(text) {
  const raw = String(text ?? "");
  return [
    /(?:^|\n)\s*(?:[-*]\s*)?(?:A|B|C|사회자|감독|촬영감독|프로듀서|작가|스태프|회의록|art[- ]house director|k-?pop performance director|experimental visual artist|director|performance director|visual artist)\s*[:：]/i,
    /(가상의|내부|팀|스태프|패널).{0,18}(대화|회의|토론|논의)/,
    /(fictional|imagined|internal|simulated).{0,18}(panel|meeting|debate|conversation|participants)/i,
    /(multi[- ]persona|roleplay|panel[- ]style|staff room|roundtable)/i,
    /(A\s*[:：].*\n\s*B\s*[:：])|(".*"\s*라고\s*(?:말|답))/s,
    /(fake|fictional|imagined|internal)\s+(dialogue|meeting|conversation|participants)/i
  ].some((pattern) => pattern.test(raw));
}

export function buildReviewerModeSystemText(config) {
  return [
    "Inter-agent orchestration reviewer mode is active.",
    `You are ${config.reviewerName}, exactly one single reviewer agent.`,
    "Do not simulate fictional participants, internal panel discussions, staff meetings, roundtables, multi-persona roleplay, multi-persona dialogue, or dialogue between invented roles.",
    "Forbidden formats include 'Art-house director:', 'K-pop performance director:', 'Experimental visual artist:', 'A:', 'B:', and any panel-style speaker labels.",
    "Reply only from your own single reviewer perspective.",
    "Keep the response concise: critique, alternatives, risks, and a recommendation.",
    "Do not make the final decision. OpenClaw owns synthesis and final recommendation."
  ].join("\n");
}

export function buildOpenClawSynthesisInstruction(config, reviewState) {
  return [
    "Inter-agent orchestration context is attached below.",
    "OpenClaw is the orchestrator and final synthesis owner.",
    `Reviewer: ${config.reviewerName}`,
    `Stop condition: ${reviewState.stop.reason}; confidence=${reviewState.stop.confidence.toFixed(2)}; converged=${reviewState.stop.converged}`,
    "The same-thread Discord result must include a clearly labeled Final synthesis section and then a final recommendation.",
    "Do not claim Hermes made the final decision."
  ].join("\n");
}

export function buildOpenClawDraft({ request }) {
  return "";
}

export function isUsableOpenClawDraft(draft, request) {
  const normalizedDraft = String(draft ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
  const normalizedRequest = String(request ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
  if (!normalizedDraft) return false;
  if (normalizedDraft === normalizedRequest) return false;
  if (["후보a", "후보b", "후보c"].every((candidate) => normalizedDraft.includes(candidate))) return false;
  return true;
}

export function extractDraftReviewAnchors(draft) {
  const candidateLine = String(draft ?? "").match(/Draft candidates:\s*([^\n]+)/i)?.[1];
  if (candidateLine) {
    return candidateLine
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);
  }
  return [...new Set(String(draft ?? "").match(/[가-힣A-Za-z0-9][가-힣A-Za-z0-9_-]{1,}/g) ?? [])]
    .filter((term) => !["OpenClaw", "draft", "User", "request", "Draft", "candidates", "criteria", "respond", "directly", "current", "Hermes", "final", "synthesis"].includes(term))
    .slice(0, 20);
}

export function reviewerMentionsOpenClawDraft(response, draft) {
  const normalizedResponse = String(response ?? "").toLocaleLowerCase();
  return extractDraftReviewAnchors(draft)
    .some((anchor) => normalizedResponse.includes(anchor.toLocaleLowerCase()));
}

export function buildHermesRequest({ config, correlationId, round, request, openClawDraft, previousReview }) {
  return [
    `${config.reviewerMention} [${config.marker}:${correlationId}:round:${round}]`,
    "ORCHESTRATION MODE: strict reviewer-only response required.",
    `Identity: ${config.reviewerName}, one single reviewer agent only.`,
    "Hard rule: Never invent staff members, fictional participants, directors, artists, panelists, an internal meeting, a debate panel, a roundtable, or multi-persona dialogue.",
    "Forbidden examples: 'Art-house director:', 'K-pop performance director:', 'Experimental visual artist:', 'A:', 'B:', '사회자:', '감독:', '프로듀서:'.",
    "Required format: one concise reviewer critique from your own perspective only, with alternatives, risks, and recommendation.",
    "Do not make the final decision. OpenClaw will post the final synthesis after reading your reply.",
    `User request:\n${request}`,
    `OpenClaw draft to review:\n${openClawDraft}`,
    previousReview ? `Previous reviewer response:\n${previousReview}` : undefined
  ].filter(Boolean).join("\n\n");
}

export function buildFinalSynthesisMessage({ config, request, openClawDraft, reviews, stop }) {
  const reviewerText = reviews.map((review) => review.text).join("\n\n").trim();
  const compactRequest = String(request ?? "").trim();
  const draftCue = previewText(openClawDraft, 120);
  const reviewCue = previewText(reviewerText, 140);
  return limitDiscordMessage([
    "**Final synthesis**",
    "",
    `Request: ${compactRequest}`,
    "",
    "Sources: OpenClaw draft + Hermes review stored in SQLite.",
    `Draft cue: ${draftCue}`,
    reviewCue ? `Hermes cue: ${reviewCue}` : undefined,
    "",
    "Final recommendation:",
    reviewCue
      ? `${config.reviewerName}의 critique를 반영해 OpenClaw draft를 그대로 반복하지 않고, 차별화 기준과 실행 방향만 최종 정리합니다.`
      : "현재 요청에 대한 reviewer 내용을 찾지 못했습니다.",
    "",
    `_Reviewer: ${config.reviewerName}; stop=${stop.reason}; request=${compactRequest.replace(/\s+/g, " ").slice(0, 80)}._`
  ].filter((line) => line !== undefined).join("\n"));
}

export function buildOpenClawDraftTimelineMessage({ openClawDraft, compact = true }) {
  if (!compact) return String(openClawDraft ?? "").trim();
  return [
    "**OpenClaw draft**",
    previewText(openClawDraft, 360),
    "",
    "_Full draft stored in SQLite._"
  ].join("\n");
}

export function buildSynthesisSources({ request, openClawDraft, reviews }) {
  const reviewerText = reviews.map((review) => review.text).join("\n\n").trim();
  return {
    userRequest: String(request ?? "").trim(),
    draft: String(openClawDraft ?? "").trim(),
    reviewer: reviewerText
  };
}

export function hasUnrelatedStaleSynthesisSource({ userRequest, draft, reviewer }) {
  const haystack = [userRequest, draft, reviewer].join("\n");
  const requestHasStaleDomain = /(폐허도시|오프닝|롱샷)/.test(userRequest);
  return !requestHasStaleDomain && /(폐허도시|추천 오프닝|롱샷)/.test(haystack);
}

function limitDiscordMessage(text, max = 1900) {
  const raw = String(text ?? "");
  if (raw.length <= max) return raw;
  return `${raw.slice(0, max - 20).trim()}\n\n...(truncated)`;
}

function exceedsDiscordSafeLimit(text) {
  return String(text ?? "").length > DISCORD_SAFE_CONTENT_LIMIT;
}

function buildInternalHermesRequestTimelineMessage({ config, marker }) {
  return [
    "**Hermes reviewer request**",
    "",
    `${config.reviewerName} executor route: internal CLI/API.`,
    "Full captured OpenClaw draft was sent to the reviewer executor and stored in SQLite. Discord shows this compact timeline entry only.",
    `Marker: ${marker}`
  ].join("\n");
}

export function buildOrchestrationFailureMessage({ reason }) {
  return [
    "**Final synthesis unavailable**",
    reason === "hermes_request_post_failed"
      ? "Hermes reviewer request could not be posted."
      : reason === "reviewer_timeout"
      ? "Hermes reviewer response를 제한 시간 안에 읽지 못했습니다. 같은 스레드에서 최종 합성을 진행하지 않고 실패로 표시합니다."
      : reason === "stale_synthesis_source_detected"
      ? "Final synthesis source validation failed because stale or unrelated context was detected. No synthesis was produced."
      : "Hermes 응답이 reviewer-only 규칙을 위반했습니다. fictional participants / panel-style dialogue가 포함된 응답은 합성에 사용하지 않습니다."
  ].join("\n");
}

export function parseReviewerVerdict(text) {
  const raw = String(text ?? "");
  const match = raw.match(/\bverdict\s*:\s*(partial_agree|agree_with_changes|needs_user_decision|disagree|agree)\b/i);
  if (match) {
    const verdict = match[1].toLocaleLowerCase();
    return verdict === "agree_with_changes" ? "partial_agree" : verdict;
  }
  if (/\bneeds_user_decision\b/i.test(raw) || /(사용자|유저|오너).{0,12}(승인|결정|확인).{0,8}(필요|받아야|먼저)/.test(raw)) {
    return "needs_user_decision";
  }
  if (/\bdisagree\b/i.test(raw) || /(반대|동의하지 않)/.test(raw)) return "disagree";
  if (/\b(partial_agree|agree_with_changes)\b/i.test(raw) || /(수정|보완).{0,12}(동의|진행|추천)/.test(raw)) return "partial_agree";
  if (/\bagree\b/i.test(raw) || /(동의|추천)/.test(raw)) return "agree";
  return "partial_agree";
}

const ESCALATION_PATTERNS = [
  { pattern: /결제|구매|유료\s*구독|카드\s*등록|송금|payment|purchase/i, reason: "budget_or_payment" },
  { pattern: /법무\s*검토|저작권\s*침해|상표\s*등록|계약\s*(?:체결|서명)|라이선스\s*(?:구매|계약)|초상권\s*허가|IP\s*(?:계약|침해)/i, reason: "legal_or_ip" },
  { pattern: /브랜드.{0,12}(?:훼손|리스크|변경|승인|공개)|외부\s*공개|실제\s*게시|업로드\s*해줘|배포\s*해줘|출시\s*해줘|publish|deploy/i, reason: "brand_or_public_release" },
  { pattern: /네가\s*대신\s*(?:선택|골라)|최종\s*승인|사용자\s*승인|내\s*대신\s*결정/i, reason: "subjective_choice" },
  { pattern: /삭제\s*해줘|초기화\s*해줘|되돌릴 수 없는|irreversible|reset\s+--hard|drop\s+table/i, reason: "irreversible_action" },
  { pattern: /git push|push\b|merge\b|main\b|production/i, reason: "source_control_or_production" }
];

export function evaluateEscalationReasons({ request, openClawDraft, review }) {
  const haystack = [request, openClawDraft, review?.text ?? review].join("\n");
  const reasons = ESCALATION_PATTERNS
    .filter(({ pattern }) => pattern.test(haystack))
    .map(({ reason }) => reason);
  if (parseReviewerVerdict(review?.text ?? review) === "needs_user_decision") {
    reasons.push("reviewer_requested_user_decision");
  }
  return [...new Set(reasons)];
}

export function buildEscalationMessage({ reasons }) {
  return [
    "**User decision required**",
    "",
    "Final synthesis paused before making a decision.",
    `Reasons: ${reasons.join(", ")}`,
    "",
    "사용자 확인이 필요합니다. 이 thread에서 승인/수정 방향을 알려주면 다음 단계로 진행합니다."
  ].join("\n");
}

export function buildHermesThreadViolationMessage({ expectedThreadId, observedThreadId }) {
  return [
    "**User decision required**",
    "",
    "Hermes replied outside the task thread.",
    `Expected thread: ${expectedThreadId}`,
    `Observed thread: ${observedThreadId}`,
    "",
    "Reasons: hermes_wrong_thread",
    "",
    "Same-thread policy was violated. Reply in this thread with the direction to continue."
  ].join("\n");
}

export function buildResumedFinalSynthesisMessage({ config, request, openClawDraft, reviews, userDecision }) {
  const base = buildFinalSynthesisMessage({
    config,
    request: `${request}\n\nUser decision after escalation:\n${userDecision}`,
    openClawDraft,
    reviews,
    stop: { reason: "user_decision_received", confidence: 1, converged: true }
  });
  return limitDiscordMessage([
    base,
    "",
    "User decision applied:",
    String(userDecision ?? "").trim()
  ].join("\n"));
}

export function buildHermesRequestPostFailureMessage() {
  return "Final synthesis unavailable: Hermes reviewer request could not be posted.";
}

export function buildThreadCreationFailureMessage() {
  return "thread creation failed";
}

export function buildFallbackContext({ config, reason, threadId, messageId }) {
  return [
    "Inter-agent orchestration fallback is active.",
    `Reason: ${reason}`,
    `Thread id: ${threadId ?? "unknown"}`,
    `Message id: ${messageId ?? "unknown"}`,
    `${config.reviewerName} could not be called directly, but the user expected a Hermes review.`,
    "OpenClaw must answer visibly in this turn.",
    "Separate the response into a Hermes-style reviewer perspective and a Final synthesis section.",
    "Be explicit that this is a fallback because the direct Hermes reviewer request could not be posted."
  ].join("\n");
}

export function evaluateStopCondition({ text, round, config }) {
  let confidence = Math.min(0.95, String(text ?? "").length / 900);
  if (/\b(agree|converge|solid|recommend|clear|works)\b/i.test(text) || /(동의|수렴|좋|추천|명확|괜찮)/.test(text)) confidence += 0.25;
  if (/\b(uncertain|not sure|needs more|disagree|risk)\b/i.test(text) || /(불확실|모르|리스크|위험|반대|부족)/.test(text)) confidence -= 0.2;
  confidence = Math.max(0, Math.min(1, confidence));
  const converged = /\b(agree|converge|same direction|recommend)\b/i.test(text) || /(동의|수렴|같은 방향|추천)/.test(text);
  if (violatesReviewerMode(text)) return { stop: false, reason: "reviewer_mode_violation", confidence: 0, converged: false };
  if (confidence >= config.confidenceThreshold) return { stop: true, reason: "confidence_threshold", confidence, converged };
  if (converged) return { stop: true, reason: "convergence", confidence, converged };
  if (round >= config.maxRounds) return { stop: true, reason: "max_rounds", confidence, converged };
  return { stop: false, reason: "continue", confidence, converged };
}

function firstMatch(text, pattern) {
  return String(text ?? "").match(pattern)?.[1];
}

function escapeRegex(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runHermesCliReview(prompt, timeoutMs = 120000) {
  return new Promise((resolve, reject) => {
    const child = execFile("hermes", ["-z", prompt], { timeout: timeoutMs, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr?.trim() || error.message));
        return;
      }
      resolve(String(stdout ?? "").trim());
    });
    child.stdin?.end();
  });
}

function parseTimestampMs(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string" || !value.trim()) return undefined;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function previewText(text, max = 160) {
  return String(text ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

function getStateDb(config) {
  if (!config?.statePersistenceEnabled) return undefined;
  const dbPath = String(config.stateDbPath ?? "").trim();
  if (!dbPath) return undefined;
  if (stateDbByPath.has(dbPath)) return stateDbByPath.get(dbPath);
  if (dbPath !== ":memory:") mkdirSync(dirname(dbPath), { recursive: true });
  const db = new DatabaseSync(dbPath);
  db.exec("PRAGMA journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS orchestration_tasks (
      id TEXT PRIMARY KEY,
      parent_channel_id TEXT NOT NULL,
      thread_id TEXT NOT NULL,
      message_id TEXT NOT NULL,
      user_request TEXT NOT NULL,
      status TEXT NOT NULL,
      correlation_id TEXT,
      final_message_id TEXT,
      failure_reason TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orchestration_turns (
      id TEXT PRIMARY KEY,
      task_id TEXT NOT NULL REFERENCES orchestration_tasks(id) ON DELETE CASCADE,
      round INTEGER NOT NULL,
      role TEXT NOT NULL,
      kind TEXT NOT NULL,
      content TEXT NOT NULL,
      visible_summary TEXT NOT NULL,
      message_id TEXT,
      created_at TEXT NOT NULL
    );
  `);
  stateDbByPath.set(dbPath, db);
  return db;
}

function recordStateTask(config, input, logger = liveLog) {
  try {
    const db = getStateDb(config);
    if (!db) return;
    const now = new Date().toISOString();
    db.prepare(`
      INSERT INTO orchestration_tasks
        (id, parent_channel_id, thread_id, message_id, user_request, status, correlation_id, final_message_id, failure_reason, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        parent_channel_id = excluded.parent_channel_id,
        thread_id = excluded.thread_id,
        message_id = excluded.message_id,
        user_request = excluded.user_request,
        status = excluded.status,
        correlation_id = COALESCE(excluded.correlation_id, orchestration_tasks.correlation_id),
        final_message_id = COALESCE(excluded.final_message_id, orchestration_tasks.final_message_id),
        failure_reason = excluded.failure_reason,
        updated_at = excluded.updated_at
    `).run(
      input.id,
      input.parentChannelId,
      input.threadId,
      input.messageId,
      input.userRequest,
      input.status,
      input.correlationId ?? null,
      input.finalMessageId ?? null,
      input.failureReason ?? null,
      now,
      now
    );
  } catch (error) {
    logger("state persistence failed", { operation: "task", error: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240) });
  }
}

function recordStateTurn(config, input, logger = liveLog) {
  try {
    const db = getStateDb(config);
    if (!db) return;
    const id = input.id ?? crypto.randomUUID();
    db.prepare(`
      INSERT INTO orchestration_turns
        (id, task_id, round, role, kind, content, visible_summary, message_id, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      id,
      input.taskId,
      input.round,
      input.role,
      input.kind,
      input.content,
      input.visibleSummary ?? previewText(input.content, 240),
      input.messageId ?? null,
      input.createdAt ?? new Date().toISOString()
    );
  } catch (error) {
    logger("state persistence failed", { operation: "turn", kind: input.kind, error: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240) });
  }
}

function getWaitingTaskByThread(config, threadId, logger = liveLog) {
  try {
    const db = getStateDb(config);
    const normalizedThreadId = normalizeDiscordTargetId(threadId);
    if (!db || !normalizedThreadId) return undefined;
    return db.prepare(`
      SELECT id, parent_channel_id, thread_id, message_id, user_request, status, correlation_id, final_message_id, failure_reason
      FROM orchestration_tasks
      WHERE thread_id = ? AND status = 'waiting_for_user'
      ORDER BY updated_at DESC
      LIMIT 1
    `).get(normalizedThreadId);
  } catch (error) {
    logger("state persistence failed", { operation: "read waiting task", error: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240) });
    return undefined;
  }
}

function getStateTurns(config, taskId, logger = liveLog) {
  try {
    const db = getStateDb(config);
    if (!db || !taskId) return [];
    return db.prepare(`
      SELECT round, role, kind, content, message_id
      FROM orchestration_turns
      WHERE task_id = ?
      ORDER BY rowid ASC
    `).all(taskId);
  } catch (error) {
    logger("state persistence failed", { operation: "read turns", error: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240) });
    return [];
  }
}

async function discordFetch(token, path, init = {}) {
  const response = await fetch(`https://discord.com/api/v10${path}`, {
    ...init,
    headers: {
      authorization: `Bot ${token}`,
      "content-type": "application/json",
      ...(init.headers ?? {})
    }
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`Discord API ${response.status}: ${body.slice(0, 240)}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function buildThreadName(request) {
  return `Agent discussion: ${String(request ?? "").replace(/<@&?\d+>/g, "").replace(/\s+/g, " ").trim().slice(0, 80) || "review"}`;
}

async function createDiscordThread({ token, channelId, messageId, request, logger = liveLog }) {
  const name = buildThreadName(request);
  try {
    const thread = messageId
      ? await discordFetch(token, `/channels/${channelId}/messages/${messageId}/threads`, {
          method: "POST",
          body: JSON.stringify({ name, auto_archive_duration: 60 })
        })
      : await discordFetch(token, `/channels/${channelId}/threads`, {
          method: "POST",
          body: JSON.stringify({ name, type: 11, auto_archive_duration: 60 })
        });
    if (!thread?.id || String(thread.id) === String(channelId)) {
      throw new Error(`Discord thread creation did not return a real thread id: ${thread?.id ?? "missing"}`);
    }
    return { id: String(thread.id), name };
  } catch (error) {
    logger("auto thread creation failed", {
      parentChannelId: channelId,
      error: error instanceof Error ? error.message.slice(0, 500) : String(error).slice(0, 500)
    });
    throw error;
  }
}

async function sendDiscordMessage({ token, channelId, content, config }) {
  return discordFetch(token, `/channels/${channelId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      content,
      allowed_mentions: {
        users: config.reviewerBotIds,
        roles: config.reviewerRoleIds,
        parse: []
      }
    })
  });
}

function resolveReviewerPostToken(config) {
  const envName = String(config?.reviewerPostTokenEnv ?? "").trim();
  if (!envName) return undefined;
  return process.env[envName]?.trim() || undefined;
}

async function sendReviewerDiscordMessage({ token, channelId, content, config, logger = liveLog }) {
  const reviewerToken = resolveReviewerPostToken(config);
  const effectiveToken = reviewerToken || token;
  const sent = await sendDiscordMessage({ token: effectiveToken, channelId, content, config });
  logger("Hermes review posted", {
    threadId: channelId,
    messageId: sent?.id,
    route: reviewerToken ? "hermes-bot-token" : "openclaw-fallback-token"
  });
  return sent;
}

export function getReviewerReplyExclusionReason(message, { requestMessageId, requestCreatedAtMs, config, marker }) {
  const content = typeof message?.content === "string" ? message.content.trim() : "";
  const authorId = message?.author?.id;
  const createdAtMs = parseTimestampMs(message?.timestamp ?? message?.created_at);
  if (!content) return "empty response";
  if (message?.id === requestMessageId) return "same message as Hermes reviewer request";
  if (isDiscordSnowflakeLike(message?.id) && isDiscordSnowflakeLike(requestMessageId) && BigInt(message.id) <= BigInt(requestMessageId)) return "message predates Hermes reviewer request";
  if (config.reviewerBotIds.length > 0 && !config.reviewerBotIds.includes(authorId)) return "author is not configured Hermes bot";
  if (!isDiscordSnowflakeLike(message?.id) && requestCreatedAtMs !== undefined && createdAtMs !== undefined && createdAtMs < requestCreatedAtMs) return "message predates Hermes reviewer request";
  if (content.includes(marker)) return "message is orchestration request marker";
  if (isSystemLikeReviewerMessage(content)) return "system/control message";
  return undefined;
}

function isDiscordSnowflakeLike(value) {
  return /^\d{17,25}$/.test(String(value ?? ""));
}

export function isSystemLikeReviewerMessage(text) {
  const raw = String(text ?? "").trim();
  if (!raw) return true;
  return [
    /\bstopped\b/i,
    /you can continue this session/i,
    /needs your input/i,
    /skill_view\s*:/i,
    /^\s*📚\s*skill_view\s*:/i,
    /looking at the available skills/i,
    /available skills.{0,120}(don't|do not|doesn't|not).{0,80}(relevant|directly relevant)/i,
    /this is a pure review task/i,
    /session_search/i,
    /\bclarify\s*:/i,
    /delegate_task/i,
    /\bchoose\b.{0,40}\b(option|one)\b/i,
    /\bselect\b.{0,40}\b(option|one)\b/i,
    /click (a )?button/i,
    /waiting for (your )?(input|selection|confirmation)/i,
    /please (choose|select|confirm)/i
  ].some((pattern) => pattern.test(raw));
}

export function isForbiddenHermesStandaloneMessage(text) {
  const raw = String(text ?? "").trim();
  if (!raw) return false;
  return [
    /\bclarify\s*:/i,
    /needs your input/i,
    /\bchoice buttons?\b/i,
    /\bchoose\b.{0,80}\b(option|one)\b/i,
    /\bselect\b.{0,80}\b(option|one)\b/i,
    /delegate_task/i,
    /stop requested/i,
    /\bstopped\b/i,
    /you can continue this session/i
  ].some((pattern) => pattern.test(raw));
}

export function buildHermesStandaloneBlockInstruction() {
  return [
    "Orchestration channel policy: Hermes standalone conversational flow is disabled.",
    "If this is not an OpenClaw reviewer request containing an [OC-IA:...] marker, your visible final answer must be exactly: NO_REPLY",
    "Do not ask clarify questions, do not request choices, do not call delegate_task, and do not emit needs-your-input messages.",
    "Only respond to OpenClaw reviewer requests marked with [OC-IA:...] as one concise reviewer critique with alternatives, risks, and recommendation."
  ].join("\n");
}

export function evaluateHermesStandaloneBlock({ text, messages, marker = DEFAULT_MARKER }) {
  const haystack = [text, messages ? safeStringify(messages) : ""].join("\n");
  if (hasOrchestrationMarker(haystack, marker)) return { blocked: false, allowedReviewerOnly: true };
  if (!isForbiddenHermesStandaloneMessage(text)) return { blocked: false, allowedReviewerOnly: false };
  return {
    blocked: true,
    reason: "Hermes standalone response blocked by orchestration policy",
    instruction: buildHermesStandaloneBlockInstruction()
  };
}

export function selectReviewerReply(messages, params) {
  const ordered = (Array.isArray(messages) ? messages : [])
    .slice()
    .sort((a, b) => (parseTimestampMs(a?.timestamp ?? a?.created_at) ?? 0) - (parseTimestampMs(b?.timestamp ?? b?.created_at) ?? 0));
  for (const message of ordered) {
    const authorId = message?.author?.id;
    const content = typeof message?.content === "string" ? message.content.trim() : "";
    if (authorId === params.config.reviewerBotIds[0] || params.config.reviewerBotIds.includes(authorId)) {
      params.logger?.("Hermes candidate message found", {
        messageId: message?.id,
        authorId,
        timestamp: message?.timestamp ?? message?.created_at,
        preview: previewText(content)
      });
    }
    const reason = getReviewerReplyExclusionReason(message, params);
    if (reason) {
      if (authorId === params.config.reviewerBotIds[0] || params.config.reviewerBotIds.includes(authorId)) {
        params.logger?.("Hermes candidate excluded", {
          messageId: message?.id,
          reason,
          preview: previewText(content)
        });
      }
      continue;
    }
    params.logger?.("Hermes reviewer reply selected", {
      messageId: message?.id,
      authorId,
      preview: previewText(content)
    });
    return {
      messageId: message.id,
      authorId,
      authorName: message.author?.global_name ?? message.author?.username ?? params.config.reviewerName,
      text: content
    };
  }
  return null;
}

function getOrchestrationStateKey({ channelId, messageId, threadId }) {
  if (messageId) return `${channelId ?? threadId ?? "unknown"}:${messageId}`;
  return `${threadId ?? channelId ?? "unknown"}:no-message`;
}

function rememberOrchestrationMessageId(messageId, value = {}) {
  const normalized = String(messageId ?? "").trim();
  if (!normalized) return;
  orchestrationMessageIds.set(normalized, { ...value, expiresAt: Date.now() + 10 * 60 * 1000 });
}

function getRememberedOrchestrationMessageId(messageId) {
  const normalized = String(messageId ?? "").trim();
  if (!normalized) return;
  const value = orchestrationMessageIds.get(normalized);
  if (!value) return;
  if (value.expiresAt < Date.now()) {
    orchestrationMessageIds.delete(normalized);
    return;
  }
  return value;
}

function rememberOrchestrationResult(key, value) {
  orchestrationResults.set(key, { ...value, expiresAt: Date.now() + 10 * 60 * 1000 });
}

function getRememberedOrchestrationResult(key) {
  const value = orchestrationResults.get(key);
  if (!value) return;
  if (value.expiresAt < Date.now()) {
    orchestrationResults.delete(key);
    return;
  }
  return value;
}

function rememberPendingDraftCapture(parentChannelId, value) {
  const normalized = normalizeDiscordTargetId(parentChannelId);
  if (!normalized) return;
  pendingDraftCaptures.set(normalized, { ...value, expiresAt: Date.now() + 10 * 60 * 1000 });
}

function getPendingDraftCapture(parentChannelId) {
  const normalized = normalizeDiscordTargetId(parentChannelId);
  if (!normalized) return;
  const value = pendingDraftCaptures.get(normalized);
  if (!value) return;
  if (value.expiresAt < Date.now()) {
    pendingDraftCaptures.delete(normalized);
    return;
  }
  return value;
}

function clearPendingDraftCapture(parentChannelId) {
  const normalized = normalizeDiscordTargetId(parentChannelId);
  if (normalized) pendingDraftCaptures.delete(normalized);
}

function rememberThreadAutoReplySuppression(threadId, value = {}) {
  const normalized = normalizeDiscordTargetId(threadId);
  if (!normalized) return;
  threadAutoReplySuppressions.set(normalized, { ...value, expiresAt: Date.now() + 5 * 60 * 1000 });
}

export function consumeThreadAutoReplySuppression(threadId) {
  const normalized = normalizeDiscordTargetId(threadId);
  if (!normalized) return undefined;
  const value = threadAutoReplySuppressions.get(normalized);
  if (!value) return undefined;
  if (value.expiresAt < Date.now()) {
    threadAutoReplySuppressions.delete(normalized);
    return undefined;
  }
  threadAutoReplySuppressions.delete(normalized);
  return value;
}

function rememberParentAutoReplySuppression(parentChannelId, value = {}) {
  const normalized = normalizeDiscordTargetId(parentChannelId);
  if (!normalized) return;
  parentAutoReplySuppressions.set(normalized, { ...value, expiresAt: Date.now() + 5 * 60 * 1000 });
}

export function consumeParentAutoReplySuppression(parentChannelId) {
  const normalized = normalizeDiscordTargetId(parentChannelId);
  if (!normalized) return undefined;
  const value = parentAutoReplySuppressions.get(normalized);
  if (!value) return undefined;
  if (value.expiresAt < Date.now()) {
    parentAutoReplySuppressions.delete(normalized);
    return undefined;
  }
  parentAutoReplySuppressions.delete(normalized);
  return value;
}

function resolveInboundFacts(event, ctx) {
  const threadId = event.threadId ?? ctx?.conversationId;
  const channelId = threadId ?? String(event.from ?? "").match(/discord:channel:(\d+)/)?.[1] ?? ctx?.conversationId;
  return {
    channelId,
    threadId,
    messageId: event.messageId ?? ctx?.messageId,
    currentRequest: String(event.content ?? "").trim()
  };
}

async function waitForReviewer({ token, threadId, requestMessageId, requestCreatedAtMs, config, marker, fetchMessages, logger = liveLog }) {
  const startedAt = Date.now();
  logger("Hermes reply polling started", { threadId, requestMessageId, requestCreatedAtMs, waitMs: config.waitMs, pollMs: config.pollMs });
  while (Date.now() - startedAt < config.waitMs) {
    await sleep(config.pollMs);
    const messages = fetchMessages
      ? await fetchMessages()
      : await discordFetch(token, `/channels/${threadId}/messages?limit=30`);
    const match = selectReviewerReply(messages, {
      requestMessageId,
      requestCreatedAtMs,
      config,
      marker,
      logger
    });
    if (match) {
      logger("Hermes reply detected", {
        threadId,
        messageId: match.messageId,
        authorId: match.authorId,
        preview: previewText(match.text)
      });
      return match;
    }
  }
  return null;
}

async function runThreadReview({ api, prompt, config }) {
  const token = api.config.channels?.discord?.token;
  if (!token) {
    liveLog("orchestration skipped with explicit reason", { reason: "Discord bot token not configured" });
    return null;
  }
  const facts = extractDiscordFacts(prompt);
  if (!facts.channelId || !facts.messageId) {
    liveLog("orchestration skipped with explicit reason", { reason: "Discord channel/message facts not found in prompt" });
    return null;
  }
  return runThreadReviewFromFacts({ api, facts, config });
}

export async function prepareThreadOrchestrationFromFacts({ api, facts, config, sendMessage = sendDiscordMessage, waitForReview = waitForReviewer, createThread = createDiscordThread, logger = liveLog }) {
  const token = api.config.channels?.discord?.token;
  if (!token) {
    logger("orchestration skipped with explicit reason", { reason: "Discord bot token not configured" });
    return null;
  }
  if (!facts.channelId || !facts.messageId) {
    logger("orchestration skipped with explicit reason", { reason: "Discord channel/message facts not found" });
    return null;
  }
  const normalizedChannelId = normalizeDiscordTargetForSend(facts.channelId, logger);
  if (!normalizedChannelId) {
    logger("orchestration skipped with explicit reason", { reason: "invalid Discord channel target id", raw: facts.channelId });
    return { failureReason: "invalid_discord_target_id", rawTargetId: facts.channelId };
  }
  const stateKey = getOrchestrationStateKey(facts);
  if (getRememberedOrchestrationResult(stateKey)) {
    logger("orchestration skipped with explicit reason", { reason: "orchestration result already recorded", stateKey });
    return null;
  }
  if (getPendingDraftCapture(normalizedChannelId)) {
    logger("orchestration skipped with explicit reason", { reason: "draft capture already pending", parentChannelId: normalizedChannelId });
    return null;
  }

  const parentChannelId = isParentChannelTargetId(facts.threadId)
    ? normalizeDiscordTargetForSend(facts.threadId, logger)
    : normalizedChannelId;
  logger("parent channel request detected", { parentChannelId });
  logger("auto thread creation requested", { parentChannelId });

  try {
    const createdThread = await createThread({
      token,
      channelId: parentChannelId,
      messageId: facts.messageId,
      request: facts.currentRequest,
      logger
    });
    logger("auto thread created", { threadId: createdThread.id, name: createdThread.name });
    logger("orchestration target switched", { parentChannelId, threadId: createdThread.id });
    try {
      await sendMessage({
        token,
        channelId: parentChannelId,
        config,
        content: `Agent discussion started -> <#${createdThread.id}>`
      });
    } catch (noticeError) {
      logger("orchestration launcher notice failed", {
        parentChannelId,
        threadId: createdThread.id,
        error: noticeError instanceof Error ? noticeError.message.slice(0, 240) : String(noticeError).slice(0, 240)
      });
    }
    rememberPendingDraftCapture(parentChannelId, {
      api,
      facts: {
        ...facts,
        channelId: parentChannelId,
        threadId: createdThread.id
      },
      config,
      sendMessage,
      waitForReview,
      stateKey
    });
    rememberOrchestrationMessageId(facts.messageId, {
      status: "awaiting_draft_capture",
      threadId: createdThread.id,
      channelId: parentChannelId
    });
    logger("awaiting OpenClaw draft capture", { parentChannelId, threadId: createdThread.id, messageId: facts.messageId });
    return { status: "awaiting_draft_capture", parentChannelId, threadId: createdThread.id, stateKey };
  } catch (error) {
    try {
      await sendMessage({
        token,
        channelId: parentChannelId,
        config,
        content: buildThreadCreationFailureMessage()
      });
    } catch (sendError) {
      logger("orchestration failed", {
        reason: "thread creation failure message could not be posted",
        error: sendError instanceof Error ? sendError.message.slice(0, 240) : String(sendError).slice(0, 240),
        parentChannelId
      });
    }
    rememberOrchestrationResult(stateKey, {
      status: "failed",
      threadId: undefined,
      failureReason: "auto_thread_creation_failed"
    });
    return {
      failureReason: "auto_thread_creation_failed",
      parentChannelId,
      error: error instanceof Error ? error.message : String(error)
    };
  }
}

export async function capturePendingOpenClawDraftSend({ api, event, config, logger = liveLog }) {
  const parentChannelId = normalizeDiscordTargetForSend(event.to ?? event.channelId, logger);
  const pending = getPendingDraftCapture(parentChannelId);
  if (!pending) return;

  logger("OpenClaw parent reply intercepted", { parentChannelId, threadId: pending.facts.threadId });
  logger("parent reply suppressed", { parentChannelId, threadId: pending.facts.threadId });

  const content = String(event.content ?? "").trim();
  if (!isUsableOpenClawDraft(content, pending.facts.currentRequest)) {
    logger("OpenClaw draft capture failed", { parentChannelId, threadId: pending.facts.threadId });
    clearPendingDraftCapture(parentChannelId);
  await runThreadReviewFromFacts({
    api,
    facts: {
      ...pending.facts,
      openClawDraft: ""
    },
    config,
    sendMessage: pending.sendMessage,
    waitForReview: pending.waitForReview,
    logger
  });
    return {
      content: "NO_REPLY",
      metadata: { ...(event.metadata ?? {}), interAgentOrchestrationSuppressed: true }
    };
  }

  logger("OpenClaw draft captured", { parentChannelId, threadId: pending.facts.threadId, draftPreview: previewText(content) });
  clearPendingDraftCapture(parentChannelId);
  await runThreadReviewFromFacts({
    api,
    facts: {
      ...pending.facts,
      openClawDraft: content
    },
    config,
    sendMessage: pending.sendMessage,
    waitForReview: pending.waitForReview,
    logger
  });
  return {
    content: "NO_REPLY",
    metadata: { ...(event.metadata ?? {}), interAgentOrchestrationSuppressed: true }
  };
}

function buildReviewsFromTurns(turns, config) {
  return turns
    .filter((turn) => turn.kind === "review")
    .map((turn) => ({
      role: "reviewer",
      round: turn.round,
      messageId: turn.message_id,
      authorId: config.reviewerBotIds[0] ?? "hermes-reviewer",
      authorName: config.reviewerName,
      text: turn.content
    }));
}

export async function recordHermesThreadViolation({ api, config, task, observedThreadId, openClawDraft, sendMessage = sendDiscordMessage, logger = liveLog }) {
  const token = api.config.channels?.discord?.token;
  if (!token) {
    logger("same-thread violation skipped", { reason: "Discord bot token not configured" });
    return null;
  }
  const expectedThreadId = normalizeDiscordTargetId(task?.threadId);
  const normalizedObservedThreadId = normalizeDiscordTargetId(observedThreadId);
  if (!expectedThreadId || !normalizedObservedThreadId) {
    logger("same-thread violation skipped", {
      reason: "invalid thread id",
      expectedThreadId: task?.threadId,
      observedThreadId
    });
    return null;
  }

  const taskId = String(task.id);
  const draft = String(openClawDraft ?? "").trim();
  recordStateTask(config, {
    id: taskId,
    parentChannelId: task.parentChannelId,
    threadId: expectedThreadId,
    messageId: task.messageId,
    userRequest: task.userRequest,
    status: "waiting_for_user",
    correlationId: task.correlationId,
    finalMessageId: null,
    failureReason: "hermes_wrong_thread"
  }, logger);
  if (draft) {
    recordStateTurn(config, {
      taskId,
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      content: draft
    }, logger);
  }

  const content = buildHermesThreadViolationMessage({
    expectedThreadId,
    observedThreadId: normalizedObservedThreadId
  });
  const sent = await sendMessage({
    token,
    channelId: expectedThreadId,
    config,
    content
  });
  logger("User decision required", {
    threadId: expectedThreadId,
    observedThreadId: normalizedObservedThreadId,
    messageId: sent?.id,
    reasons: ["hermes_wrong_thread"]
  });
  recordStateTurn(config, {
    taskId,
    round: 1,
    role: "openclaw-finalizer",
    kind: "escalation",
    content,
    messageId: sent?.id
  }, logger);
  recordStateTask(config, {
    id: taskId,
    parentChannelId: task.parentChannelId,
    threadId: expectedThreadId,
    messageId: task.messageId,
    userRequest: task.userRequest,
    status: "waiting_for_user",
    correlationId: task.correlationId,
    finalMessageId: sent?.id,
    failureReason: "hermes_wrong_thread"
  }, logger);
  rememberOrchestrationMessageId(task.messageId, {
    status: "waiting_for_user",
    threadId: expectedThreadId,
    correlationId: task.correlationId,
    finalMessageId: sent?.id
  });
  return {
    status: "waiting_for_user",
    threadId: expectedThreadId,
    taskId,
    finalMessageId: sent?.id,
    escalationReasons: ["hermes_wrong_thread"]
  };
}

export async function resumeWaitingOrchestrationFromUserDecision({ api, event, ctx, config, sendMessage = sendDiscordMessage, logger = liveLog }) {
  if (isBotAuthor({ senderId: event?.senderId, authorId: event?.senderId, metadata: event?.metadata }, config)) return null;
  const token = api.config.channels?.discord?.token;
  if (!token) {
    logger("orchestration resume skipped", { reason: "Discord bot token not configured" });
    return null;
  }
  const threadId = normalizeDiscordTargetId(event?.threadId ?? ctx?.conversationId);
  if (!threadId) return null;
  const task = getWaitingTaskByThread(config, threadId, logger);
  if (!task) return null;

  const userDecision = String(event?.content ?? "").trim();
  if (!userDecision) return null;
  const turns = getStateTurns(config, task.id, logger);
  const openClawDraft = turns.find((turn) => turn.kind === "owner_draft")?.content ?? "";
  const reviews = buildReviewsFromTurns(turns, config);
  const canResumeWithoutReview = task.failure_reason === "hermes_wrong_thread";
  if (!openClawDraft || (!canResumeWithoutReview && reviews.length === 0)) {
    logger("orchestration resume skipped", { reason: "waiting task sources missing", threadId, taskId: task.id });
    return null;
  }

  logger("User decision received", {
    threadId,
    taskId: task.id,
    messageId: event?.messageId,
    decisionPreview: previewText(userDecision)
  });
  recordStateTurn(config, {
    taskId: task.id,
    round: reviews.at(-1)?.round ?? 1,
    role: "user",
    kind: "user_decision",
    content: userDecision,
    messageId: event?.messageId
  }, logger);

  const finalContent = buildResumedFinalSynthesisMessage({
    config,
    request: task.user_request,
    openClawDraft,
    reviews,
    userDecision
  });
  const final = await sendMessage({
    token,
    channelId: threadId,
    config,
    content: finalContent
  });
  logger("Final synthesis posted", { threadId, messageId: final?.id, stopReason: "user_decision_received", resumed: true });
  recordStateTurn(config, {
    taskId: task.id,
    round: reviews.at(-1)?.round ?? 1,
    role: "openclaw-finalizer",
    kind: "final_synthesis",
    content: finalContent,
    messageId: final?.id
  }, logger);
  recordStateTask(config, {
    id: task.id,
    parentChannelId: task.parent_channel_id,
    threadId,
    messageId: task.message_id,
    userRequest: task.user_request,
    status: "completed",
    correlationId: task.correlation_id,
    finalMessageId: final?.id,
    failureReason: null
  }, logger);
  rememberOrchestrationMessageId(event?.messageId, {
    status: "completed",
    threadId,
    correlationId: task.correlation_id,
    finalMessageId: final?.id
  });
  rememberThreadAutoReplySuppression(threadId, {
    reason: "resumed_user_decision",
    messageId: event?.messageId,
    taskId: task.id,
    finalMessageId: final?.id
  });
  return { status: "completed", threadId, taskId: task.id, finalMessageId: final?.id };
}

export async function runThreadReviewFromFacts({ api, facts, config, sendMessage = sendDiscordMessage, sendReviewerMessage, waitForReview = waitForReviewer, createThread = createDiscordThread, runCliReview = runHermesCliReview, logger = liveLog }) {
  const token = api.config.channels?.discord?.token;
  if (!token) {
    logger("orchestration skipped with explicit reason", { reason: "Discord bot token not configured" });
    return null;
  }
  if (!facts.channelId || !facts.messageId) {
    logger("orchestration skipped with explicit reason", { reason: "Discord channel/message facts not found" });
    return null;
  }
  const normalizedChannelId = normalizeDiscordTargetForSend(facts.channelId, logger);
  if (!normalizedChannelId) {
    logger("orchestration skipped with explicit reason", { reason: "invalid Discord channel target id", raw: facts.channelId });
    return { failureReason: "invalid_discord_target_id", rawTargetId: facts.channelId };
  }
  const stateKey = getOrchestrationStateKey(facts);
  if (inFlight.has(stateKey)) {
    logger("orchestration skipped with explicit reason", { reason: "orchestration already in flight", stateKey });
    return null;
  }
  inFlight.set(stateKey, Date.now() + config.waitMs * config.maxRounds + 60000);
  try {
    const postReviewerMessage = sendReviewerMessage ?? (sendMessage === sendDiscordMessage ? sendReviewerDiscordMessage : sendMessage);
    let rawThreadId;
    if (!facts.threadId || isParentChannelTargetId(facts.threadId)) {
      const parentChannelId = isParentChannelTargetId(facts.threadId)
        ? normalizeDiscordTargetForSend(facts.threadId, logger)
        : normalizedChannelId;
      logger("parent channel request detected", { parentChannelId });
      logger("auto thread creation requested", { parentChannelId });
      try {
        const createdThread = await createThread({
          token,
          channelId: parentChannelId,
          messageId: facts.messageId,
          request: facts.currentRequest,
          logger
        });
        rawThreadId = createdThread.id;
        logger("auto thread created", { threadId: createdThread.id, name: createdThread.name });
        logger("orchestration target switched", { parentChannelId, threadId: createdThread.id });
        try {
          await sendMessage({
            token,
            channelId: parentChannelId,
            config,
            content: `Agent discussion started -> <#${createdThread.id}>`
          });
        } catch (noticeError) {
          logger("orchestration launcher notice failed", {
            parentChannelId,
            threadId: createdThread.id,
            error: noticeError instanceof Error ? noticeError.message.slice(0, 240) : String(noticeError).slice(0, 240)
          });
        }
      } catch (error) {
        try {
          await sendMessage({
            token,
            channelId: parentChannelId,
            config,
            content: buildThreadCreationFailureMessage()
          });
        } catch (sendError) {
          logger("orchestration failed", {
            reason: "thread creation failure message could not be posted",
            error: sendError instanceof Error ? sendError.message.slice(0, 240) : String(sendError).slice(0, 240),
            parentChannelId
          });
        }
        rememberOrchestrationResult(stateKey, {
          status: "failed",
          threadId: undefined,
          failureReason: "auto_thread_creation_failed"
        });
        return {
          failureReason: "auto_thread_creation_failed",
          parentChannelId,
          error: error instanceof Error ? error.message : String(error)
        };
      }
    } else {
      rawThreadId = facts.threadId;
    }
    const threadId = normalizeDiscordTargetForSend(rawThreadId, logger);
    if (!threadId) {
      logger("orchestration skipped with explicit reason", { reason: "invalid Discord thread target id", raw: rawThreadId });
      return { failureReason: "invalid_discord_target_id", rawTargetId: rawThreadId };
    }
    if (threadId === normalizedChannelId && facts.threadId && isParentChannelTargetId(facts.threadId)) {
      logger("orchestration skipped with explicit reason", { reason: "refusing to use parent channel as orchestration thread target", threadId, parentChannelId: normalizedChannelId });
      return { failureReason: "parent_channel_cannot_be_orchestration_target", rawTargetId: facts.threadId };
    }
    logger("thread ID detected", { threadId, channelId: normalizedChannelId, rawChannelId: facts.channelId, messageId: facts.messageId });
    const correlationId = `${facts.messageId}-${Date.now().toString(36)}`;
    const taskId = String(facts.messageId);
    logger("orchestration started", { threadId, correlationId, maxRounds: config.maxRounds });
    logger("orchestration owner=OpenClaw", { threadId, correlationId });
    recordStateTask(config, {
      id: taskId,
      parentChannelId: normalizedChannelId,
      threadId,
      messageId: facts.messageId,
      userRequest: facts.currentRequest,
      status: "started",
      correlationId
    }, logger);
    const openClawDraft = typeof facts.openClawDraft === "string" && facts.openClawDraft.trim()
      ? facts.openClawDraft.trim()
      : buildOpenClawDraft({ request: facts.currentRequest });
    if (!isUsableOpenClawDraft(openClawDraft, facts.currentRequest)) {
      logger("OpenClaw draft capture failed", { threadId, correlationId });
      let failureMessageId;
      try {
        const failure = await sendMessage({
          token,
          channelId: threadId,
          config,
          content: "OpenClaw draft capture failed"
        });
        failureMessageId = failure?.id;
      } catch (error) {
        logger("orchestration failed", {
          reason: "draft capture failure message could not be posted",
          error: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240),
          threadId
        });
      }
      rememberOrchestrationResult(stateKey, {
        status: "failed",
        threadId,
        correlationId,
        finalMessageId: failureMessageId,
        failureReason: "openclaw_draft_capture_failed"
      });
      recordStateTask(config, {
        id: taskId,
        parentChannelId: normalizedChannelId,
        threadId,
        messageId: facts.messageId,
        userRequest: facts.currentRequest,
        status: "failed",
        correlationId,
        finalMessageId: failureMessageId,
        failureReason: "openclaw_draft_capture_failed"
      }, logger);
      return {
        threadId,
        correlationId,
        reviews: [],
        stop: { reason: "openclaw_draft_capture_failed", confidence: 0, converged: false },
        maxRounds: config.maxRounds,
        finalMessageId: failureMessageId,
        failureReason: "openclaw_draft_capture_failed"
      };
    }
    const draftMessage = await sendMessage({
      token,
      channelId: threadId,
      config,
      content: buildOpenClawDraftTimelineMessage({ openClawDraft, compact: config.compactTimeline })
    });
    logger("OpenClaw draft posted", {
      threadId,
      messageId: draftMessage?.id,
      correlationId,
      draftPreview: previewText(openClawDraft)
    });
    recordStateTurn(config, {
      taskId,
      round: 0,
      role: "openclaw-owner",
      kind: "owner_draft",
      content: openClawDraft,
      messageId: draftMessage?.id
    }, logger);
    const reviews = [];
    let previousReview;
    let stop = { reason: "timeout", confidence: 0, converged: false };
    let finalMessageId;
    let failureReason;
    for (let round = 1; round <= config.maxRounds; round++) {
      const marker = `[${config.marker}:${correlationId}:round:${round}]`;
      const hermesRequestContent = buildHermesRequest({ config, correlationId, round, request: facts.currentRequest, openClawDraft, previousReview });
      let sent;
      logger("reviewer request includes captured draft", {
        threadId,
        correlationId,
        draftPreview: previewText(openClawDraft),
        requestLength: hermesRequestContent.length
      });
      recordStateTurn(config, {
        taskId,
        round,
        role: "openclaw-owner",
        kind: "review_request",
        content: hermesRequestContent
      }, logger);
      let review;
      const useInternalReviewer = exceedsDiscordSafeLimit(hermesRequestContent)
        || (config.reviewerRequestMode === "internal" && sendMessage === sendDiscordMessage);
      if (useInternalReviewer) {
        logger(exceedsDiscordSafeLimit(hermesRequestContent)
          ? "Hermes reviewer request exceeds Discord limit; using internal executor"
          : "Hermes reviewer request using internal executor", {
          threadId,
          round,
          requestLength: hermesRequestContent.length
        });
        try {
          sent = await sendMessage({
            token,
            channelId: threadId,
            config,
            content: buildInternalHermesRequestTimelineMessage({ config, marker })
          });
          logger("Hermes reviewer request posted", { threadId, messageId: sent?.id, round, correlationId, route: "internal-executor", requestCreatedAtMs: Date.now() });
        } catch (timelineError) {
          logger("orchestration failed", {
            reason: "Hermes internal executor timeline could not be posted",
            error: timelineError instanceof Error ? timelineError.message.slice(0, 240) : String(timelineError).slice(0, 240),
            threadId
          });
        }
        try {
          const cliText = await runCliReview(hermesRequestContent);
          if (cliText) {
            const cliMessage = await postReviewerMessage({
              token,
              channelId: threadId,
              config,
              content: limitDiscordMessage(["**Hermes review**", "", cliText].join("\n")),
              logger
            });
            review = {
              messageId: cliMessage?.id ?? `hermes-cli-${Date.now()}`,
              authorId: config.reviewerBotIds[0] ?? "hermes-cli",
              authorName: config.reviewerName,
              text: cliText
            };
            logger("Hermes reply detected", {
              threadId,
              messageId: review.messageId,
              authorId: "hermes-cli",
              route: "internal-executor",
              preview: previewText(review.text)
            });
          }
        } catch (error) {
          logger("orchestration skipped with explicit reason", {
            reason: "Hermes reviewer timeout",
            threadId,
            round,
            internalExecutorError: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240)
          });
        }
        if (!review) {
          failureReason = "reviewer_timeout";
          break;
        }
      } else {
        try {
          sent = await sendMessage({
            token,
            channelId: threadId,
            config,
            content: hermesRequestContent
          });
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          if (/native hook relay/i.test(message)) logger("orchestration failed", { reason: "native hook relay not available" });
          logger("orchestration failed", { reason: "Hermes reviewer request could not be posted", error: message.slice(0, 240), threadId, round });
          let failureMessageId;
          try {
            const failure = await sendMessage({
              token,
              channelId: threadId,
              config,
              content: buildHermesRequestPostFailureMessage()
            });
            failureMessageId = failure?.id;
            logger("Final synthesis posted", { threadId, messageId: failureMessageId, stopReason: "hermes_request_post_failed", failure: true });
          } catch (failureError) {
            logger("orchestration failed", {
              reason: "visible failure message could not be posted",
              error: failureError instanceof Error ? failureError.message.slice(0, 240) : String(failureError).slice(0, 240),
              threadId
            });
          }
          const fallbackContext = buildFallbackContext({
            config,
            reason: "Hermes reviewer request could not be posted.",
            threadId,
            messageId: facts.messageId
          });
          rememberOrchestrationResult(stateKey, {
            status: "failed",
            threadId,
            correlationId,
            finalMessageId: failureMessageId,
            fallbackContext,
            failureReason: "hermes_request_post_failed"
          });
          return { threadId, correlationId, reviews: [], stop: { reason: "hermes_request_post_failed", confidence: 0, converged: false }, maxRounds: config.maxRounds, finalMessageId: failureMessageId, failureReason: "hermes_request_post_failed", fallbackContext };
        }
        const requestCreatedAtMs = parseTimestampMs(sent?.timestamp ?? sent?.created_at) ?? Date.now();
        logger("Hermes reviewer request posted", { threadId, messageId: sent?.id, round, correlationId, requestCreatedAtMs });
        review = await waitForReview({ token, threadId, requestMessageId: sent?.id, requestCreatedAtMs, config, marker, logger });
        if (!review) {
          logger("Hermes Discord polling timed out; trying CLI fallback", { threadId, round });
          try {
            const cliText = await runCliReview(hermesRequestContent);
            if (cliText) {
              const cliMessage = await postReviewerMessage({
                token,
                channelId: threadId,
                config,
                content: limitDiscordMessage(["**Hermes review**", "", cliText].join("\n")),
                logger
              });
              review = {
                messageId: cliMessage?.id ?? `hermes-cli-${Date.now()}`,
                authorId: config.reviewerBotIds[0] ?? "hermes-cli",
                authorName: config.reviewerName,
                text: cliText
              };
              logger("Hermes reply detected", {
                threadId,
                messageId: review.messageId,
                authorId: "hermes-cli",
                route: "cli-fallback",
                preview: previewText(review.text)
              });
            }
          } catch (error) {
            logger("orchestration skipped with explicit reason", {
              reason: "Hermes reviewer timeout",
              threadId,
              round,
              cliFallbackError: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240)
            });
          }
          if (!review) {
            failureReason = "reviewer_timeout";
            break;
          }
        }
      }
      if (violatesReviewerMode(review.text)) {
        logger("orchestration skipped with explicit reason", { reason: "Hermes reviewer-mode violation", threadId, round });
        previousReview = "Previous response violated reviewer-only mode. Retry as one concise reviewer agent with no fictional dialogue.";
        if (round >= config.maxRounds) failureReason = "reviewer_mode_violation";
        continue;
      }
      if (!reviewerMentionsOpenClawDraft(review.text, openClawDraft)) {
        logger("orchestration skipped with explicit reason", {
          reason: "Hermes response did not mention OpenClaw draft",
          threadId,
          round,
          draftAnchors: extractDraftReviewAnchors(openClawDraft).slice(0, 8),
          reviewerPreview: previewText(review.text)
        });
        previousReview = "Previous response did not mention the OpenClaw draft candidates. Retry by explicitly critiquing at least one OpenClaw draft candidate.";
        if (round >= config.maxRounds) failureReason = "review_did_not_reference_openclaw_draft";
        continue;
      }
      reviews.push({ role: "reviewer", round, ...review });
      recordStateTurn(config, {
        taskId,
        round,
        role: "hermes-reviewer",
        kind: "review",
        content: review.text,
        messageId: review.messageId
      }, logger);
      const escalationReasons = evaluateEscalationReasons({
        request: facts.currentRequest,
        openClawDraft,
        review
      });
      if (escalationReasons.length > 0) {
        const escalationContent = buildEscalationMessage({ reasons: escalationReasons });
        const escalationMessage = await sendMessage({
          token,
          channelId: threadId,
          config,
          content: escalationContent
        });
        finalMessageId = escalationMessage?.id;
        stop = { reason: "waiting_for_user", confidence: 0, converged: false };
        logger("User decision required", { threadId, messageId: finalMessageId, reasons: escalationReasons });
        recordStateTurn(config, {
          taskId,
          round,
          role: "openclaw-finalizer",
          kind: "escalation",
          content: escalationContent,
          messageId: finalMessageId
        }, logger);
        const reviewState = { threadId, correlationId, openClawDraft, reviews, stop, maxRounds: config.maxRounds, finalMessageId, failureReason: null, escalationReasons };
        rememberOrchestrationResult(stateKey, { status: "waiting_for_user", ...reviewState });
        rememberOrchestrationMessageId(facts.messageId, { status: "waiting_for_user", threadId, correlationId, finalMessageId });
        rememberParentAutoReplySuppression(normalizedChannelId, {
          reason: "thread_result_already_posted",
          status: "waiting_for_user",
          threadId,
          correlationId,
          finalMessageId
        });
        recordStateTask(config, {
          id: taskId,
          parentChannelId: normalizedChannelId,
          threadId,
          messageId: facts.messageId,
          userRequest: facts.currentRequest,
          status: "waiting_for_user",
          correlationId,
          finalMessageId,
          failureReason: null
        }, logger);
        return reviewState;
      }
      previousReview = review.text;
      stop = evaluateStopCondition({ text: review.text, round, config });
      if (stop.stop) break;
    }
    if (reviews.length > 0) {
      const synthesisSources = buildSynthesisSources({ request: facts.currentRequest, openClawDraft, reviews });
      logger("synthesis sources", {
        correlationId,
        userRequestPreview: previewText(synthesisSources.userRequest),
        draftPreview: previewText(synthesisSources.draft),
        reviewerPreview: previewText(synthesisSources.reviewer)
      });
      if (hasUnrelatedStaleSynthesisSource(synthesisSources)) {
        logger("orchestration failed", {
          reason: "stale synthesis source detected",
          correlationId,
          userRequestPreview: previewText(synthesisSources.userRequest),
          reviewerPreview: previewText(synthesisSources.reviewer)
        });
        failureReason = "stale_synthesis_source_detected";
        const failure = await sendMessage({
          token,
          channelId: threadId,
          config,
          content: buildOrchestrationFailureMessage({ reason: failureReason })
        });
        finalMessageId = failure?.id;
        stop = { reason: failureReason, confidence: 0, converged: false };
      logger("Final synthesis posted", { threadId, messageId: finalMessageId, stopReason: stop.reason, failure: true });
      recordStateTurn(config, {
        taskId,
        round: config.maxRounds,
        role: "openclaw-finalizer",
        kind: "final_synthesis",
        content: buildOrchestrationFailureMessage({ reason: failureReason }),
        messageId: finalMessageId
      }, logger);
      } else {
      const finalContent = buildFinalSynthesisMessage({ config, request: facts.currentRequest, openClawDraft, reviews, stop });
      const final = await sendMessage({
          token,
          channelId: threadId,
          config,
          content: finalContent
        });
        finalMessageId = final?.id;
        logger("Final synthesis posted", { threadId, messageId: finalMessageId, stopReason: stop.reason });
        recordStateTurn(config, {
          taskId,
          round: config.maxRounds,
          role: "openclaw-finalizer",
          kind: "final_synthesis",
          content: finalContent,
          messageId: finalMessageId
        }, logger);
      }
    } else {
      const failureContent = buildOrchestrationFailureMessage({ reason: failureReason ?? "reviewer_timeout" });
      const failure = await sendMessage({
        token,
        channelId: threadId,
        config,
        content: failureContent
      });
      finalMessageId = failure?.id;
      stop = { reason: failureReason ?? "reviewer_timeout", confidence: 0, converged: false };
      logger("Final synthesis posted", { threadId, messageId: finalMessageId, stopReason: stop.reason, failure: true });
      recordStateTurn(config, {
        taskId,
        round: config.maxRounds,
        role: "openclaw-finalizer",
        kind: "final_synthesis",
        content: failureContent,
        messageId: finalMessageId
      }, logger);
    }
    const reviewState = { threadId, correlationId, openClawDraft, reviews, stop, maxRounds: config.maxRounds, finalMessageId, failureReason };
    rememberOrchestrationResult(stateKey, { status: "completed", ...reviewState });
    rememberOrchestrationMessageId(facts.messageId, { status: "completed", threadId, correlationId, finalMessageId });
    rememberParentAutoReplySuppression(normalizedChannelId, {
      reason: "thread_result_already_posted",
      status: failureReason ? "failed" : "completed",
      threadId,
      correlationId,
      finalMessageId
    });
    recordStateTask(config, {
      id: taskId,
      parentChannelId: normalizedChannelId,
      threadId,
      messageId: facts.messageId,
      userRequest: facts.currentRequest,
      status: failureReason ? "failed" : "completed",
      correlationId,
      finalMessageId,
      failureReason
    }, logger);
    return reviewState;
  } finally {
    inFlight.delete(stateKey);
  }
}

export default definePluginEntry({
  id: "inter-agent-orchestration",
  name: "Inter-Agent Orchestration",
  description: "Thread-relayed reviewer orchestration",
  register(api) {
    liveLog("plugin hooks registered", {
      hooks: ["message_received", "before_prompt_build", "before_agent_finalize", "agent_turn_prepare", "before_agent_run", "message_sending"]
    });

    api.on("message_received", async (event, ctx) => {
      const config = resolveConfig(api.pluginConfig);
      const channelId = ctx?.channelId;
      const threadId = event.threadId ?? ctx?.conversationId;
      const intent = detectOrchestrationIntent({
        content: event.content,
        senderId: event.senderId,
        authorId: event.senderId,
        metadata: {
          eventMetadata: event.metadata,
          event,
          ctx
        }
      }, config);
      liveLog("live Discord inbound message detected", {
        channelId,
        threadId,
        messageId: event.messageId,
        from: event.from,
        senderId: event.senderId,
        rawContentPreview: String(event.content ?? "").replace(/\s+/g, " ").slice(0, 200),
        eventFields: Object.keys(event ?? {}),
        metadataFields: event.metadata && typeof event.metadata === "object" ? Object.keys(event.metadata) : []
      });
      if (threadId) liveLog("thread ID detected", { threadId, channelId, messageId: event.messageId });
      liveLog("mentions detected", {
        openclaw: intent.orchestratorMention === true,
        hermes: intent.reviewerMention === true,
        detectedIds: intent.detectedIds ?? [],
        detectedRoleIds: intent.detectedRoleIds ?? [],
        configuredReviewerIds: intent.configuredReviewerIds ?? config.reviewerBotIds,
        configuredReviewerRoleIds: intent.configuredReviewerRoleIds ?? config.reviewerRoleIds,
        configuredOrchestratorIds: intent.configuredOrchestratorIds ?? config.orchestratorBotIds,
        detectedReviewerMention: intent.reviewerMention === true,
        detectedOrchestratorMention: intent.orchestratorMention === true,
        skipReason: intent.detected ? undefined : intent.reason
      });
      if (intent.reviewerMention && intent.orchestratorMention) {
        liveLog("multi-bot mention detected", { mentionCount: intent.detectedIds?.length ?? 0, detectedIds: intent.detectedIds ?? [] });
      }
      const resumed = await resumeWaitingOrchestrationFromUserDecision({ api, event, ctx, config });
      if (resumed) {
        liveLog("orchestration resumed from user decision", {
          threadId: resumed.threadId,
          taskId: resumed.taskId,
          finalMessageId: resumed.finalMessageId
        });
        return;
      }
      if (intent.detected) {
        liveLog("orchestration intent detected", { reason: intent.reason, mentionCount: intent.mentionCount });
        if (intent.defaultChannelPolicy) liveLog("default orchestration enabled by channel policy", { threadId, messageId: event.messageId });
        const facts = resolveInboundFacts(event, ctx);
        await prepareThreadOrchestrationFromFacts({ api, facts, config });
      } else {
        if (intent.botAuthor) liveLog("skipped bot author", { senderId: event.senderId, reason: intent.reason });
        if (intent.simpleCommand) liveLog("skipped simple command", { command: previewText(event.content, 80) });
        if (intent.directMention) liveLog("single-bot direct mention detected", { target: intent.directMention, reason: intent.reason });
        liveLog("orchestration skipped with explicit reason", { reason: intent.reason });
      }
    }, { priority: 100 });

    api.on("before_prompt_build", async (event) => {
      const config = resolveConfig(api.pluginConfig);
      if (!hasOrchestrationMarker(event.prompt, config.marker) && !event.messages?.some((message) => hasOrchestrationMarker(safeStringify(message), config.marker))) return;
      liveLog("reviewer-only Hermes response allowed", { reason: "orchestration marker present" });
      return { appendSystemContext: buildReviewerModeSystemText(config) };
    }, { priority: 100 });

    api.on("before_agent_finalize", async (event) => {
      const config = resolveConfig(api.pluginConfig);
      const haystack = [event.lastAssistantMessage, event.messages ? safeStringify(event.messages) : ""].join("\n");
      const standaloneBlock = evaluateHermesStandaloneBlock({
        text: event.lastAssistantMessage,
        messages: event.messages,
        marker: config.marker
      });
      if (standaloneBlock.blocked) {
        liveLog("Hermes standalone response blocked by orchestration policy", {
          preview: previewText(event.lastAssistantMessage),
          sessionKey: event.sessionKey,
          runId: event.runId
        });
        liveLog("Hermes standalone response blocked", {
          preview: previewText(event.lastAssistantMessage),
          sessionKey: event.sessionKey,
          runId: event.runId
        });
        return {
          action: "revise",
          reason: standaloneBlock.reason,
          retry: {
            idempotencyKey: "inter-agent-hermes-standalone-block",
            maxAttempts: 1,
            instruction: standaloneBlock.instruction
          }
        };
      }
      if (!hasOrchestrationMarker(haystack, config.marker)) return;
      liveLog("reviewer-only Hermes response allowed", { reason: "orchestration marker present", runId: event.runId });
      if (!violatesReviewerMode(event.lastAssistantMessage)) return;
      return {
        action: "revise",
        reason: "reviewer-mode violation",
        retry: {
          idempotencyKey: "inter-agent-reviewer-mode",
          maxAttempts: 1,
          instruction: buildReviewerModeSystemText(config)
        }
      };
    }, { priority: 100 });

    api.on("before_agent_run", async (event) => {
      const config = resolveConfig(api.pluginConfig);
      if (!promptHasParentChannelTarget(event.prompt) || hasOrchestrationMarker(event.prompt, config.marker)) return;
      const factsForParentPrompt = extractDiscordFacts(event.prompt);
      liveLog("parent channel launcher run prepared for draft capture", {
        reason: "parent channel reply will be captured and moved to thread",
        messageId: factsForParentPrompt.messageId,
        channelId: factsForParentPrompt.channelId ?? event.channelId
      });
      return {
        appendContext: [
          "Inter-agent orchestration is active for this Discord request.",
          "Create the actual OpenClaw owner draft for the user's request.",
          "Your visible parent-channel reply will be captured, suppressed, and reposted inside the task thread as the OpenClaw draft.",
          "Do not output NO_REPLY unless explicitly instructed by a later hook."
        ].join("\n\n")
      };
    }, { priority: 100 });

    api.on("agent_turn_prepare", async (event) => {
      const config = resolveConfig(api.pluginConfig);
      if (promptHasParentChannelTarget(event.prompt) && !hasOrchestrationMarker(event.prompt, config.marker)) {
        const factsForParentPrompt = extractDiscordFacts(event.prompt);
        liveLog("parent channel launcher turn prepared for draft capture", {
          reason: "parent channel reply will be captured and moved to thread",
          messageId: factsForParentPrompt.messageId,
          channelId: factsForParentPrompt.channelId
        });
        return {
          appendContext: [
            "Inter-agent orchestration is handling this Discord request inside the created thread.",
            "The parent channel is only a launcher, but this turn must still produce the actual OpenClaw owner draft.",
            "The message_sending hook will capture and suppress the parent-channel delivery, then repost the draft inside the task thread.",
            "Do not return the user request unchanged. Do not use dummy candidates."
          ].join("\n\n")
        };
      }
      const factsForOriginalTurn = extractDiscordFacts(event.prompt);
      const rememberedMessage = getRememberedOrchestrationMessageId(factsForOriginalTurn.messageId);
      if (rememberedMessage?.status === "in_progress" || rememberedMessage?.status === "completed") {
        liveLog("orchestration skipped with explicit reason", {
          reason: "parent channel launcher turn suppressed",
          messageId: factsForOriginalTurn.messageId,
          status: rememberedMessage.status
        });
        return {
          appendContext: [
            "Inter-agent orchestration is handling this Discord request inside the created thread.",
            "The parent channel is only a launcher.",
            "To prevent parent-channel draft leakage, your visible final answer for this turn must be exactly: NO_REPLY"
          ].join("\n\n")
        };
      }
      const intent = detectOrchestrationIntent(event.prompt, config);
      if (!intent.detected) {
        liveLog("mentions detected", {
          openclaw: intent.orchestratorMention === true,
          hermes: intent.reviewerMention === true,
          detectedIds: intent.detectedIds ?? [],
          detectedRoleIds: intent.detectedRoleIds ?? [],
          configuredReviewerIds: intent.configuredReviewerIds ?? config.reviewerBotIds,
          configuredReviewerRoleIds: intent.configuredReviewerRoleIds ?? config.reviewerRoleIds,
          configuredOrchestratorIds: intent.configuredOrchestratorIds ?? config.orchestratorBotIds,
          detectedReviewerMention: intent.reviewerMention === true,
          detectedOrchestratorMention: intent.orchestratorMention === true,
          skipReason: intent.reason
        });
        if (intent.botAuthor) liveLog("skipped bot author", { reason: intent.reason });
        if (intent.simpleCommand) liveLog("skipped simple command", { command: previewText(event.prompt, 80) });
        if (intent.directMention) liveLog("single-bot direct mention detected", { target: intent.directMention, reason: intent.reason });
        liveLog("orchestration skipped with explicit reason", { reason: intent.reason });
        return;
      }
      liveLog("mentions detected", {
        openclaw: intent.orchestratorMention === true,
        hermes: intent.reviewerMention === true,
        detectedIds: intent.detectedIds ?? [],
        detectedRoleIds: intent.detectedRoleIds ?? [],
        configuredReviewerIds: intent.configuredReviewerIds ?? config.reviewerBotIds,
        configuredReviewerRoleIds: intent.configuredReviewerRoleIds ?? config.reviewerRoleIds,
        configuredOrchestratorIds: intent.configuredOrchestratorIds ?? config.orchestratorBotIds,
        detectedReviewerMention: true,
        detectedOrchestratorMention: true
      });
      liveLog("multi-bot mention detected", { mentionCount: intent.mentionCount, detectedIds: intent.detectedIds ?? [] });
      liveLog("orchestration intent detected", { reason: intent.reason, mentionCount: intent.mentionCount });
      if (intent.defaultChannelPolicy) liveLog("default orchestration enabled by channel policy", {});
      const facts = extractDiscordFacts(event.prompt);
      const stateKey = getOrchestrationStateKey(facts);
      const remembered = getRememberedOrchestrationResult(stateKey);
      if (remembered?.fallbackContext) {
        liveLog("orchestration skipped with explicit reason", { reason: "using fallback context from prior failure", stateKey });
        return {
          appendContext: remembered.fallbackContext
        };
      }
      if (remembered?.status === "completed") {
        liveLog("orchestration skipped with explicit reason", { reason: "same-thread orchestration already completed", stateKey });
        return {
          appendContext: [
            "Inter-agent orchestration already posted its same-thread result directly to Discord.",
            "To prevent a duplicate channel reply, your visible final answer for this turn must be exactly: NO_REPLY",
            `Thread id: ${remembered.threadId}`,
            `Final message id: ${remembered.finalMessageId ?? "unknown"}`,
            `Correlation id: ${remembered.correlationId ?? "unknown"}`
          ].join("\n\n")
        };
      }
      if (inFlight.has(stateKey)) {
        liveLog("orchestration skipped with explicit reason", { reason: "same-thread orchestration already in progress", stateKey });
        return {
          appendContext: [
            "Inter-agent orchestration is already running for this Discord message.",
            "To prevent a duplicate channel reply, your visible final answer for this turn must be exactly: NO_REPLY"
          ].join("\n\n")
        };
      }
      const reviewState = await runThreadReview({ api, prompt: event.prompt, config });
      if (!reviewState) {
        liveLog("orchestration skipped with explicit reason", { reason: "runThreadReview returned no review state" });
        return;
      }
      const reviewerText = reviewState.reviews.map((review) => `Round ${review.round} ${review.authorName}: ${review.text}`).join("\n\n") || "No valid reviewer response before timeout.";
      return {
        appendContext: [
          "Inter-agent orchestration already posted its same-thread result directly to Discord.",
          "To prevent a duplicate channel reply, your visible final answer for this turn must be exactly: NO_REPLY",
          buildOpenClawSynthesisInstruction(config, reviewState),
          `Thread id: ${reviewState.threadId}`,
          `Final message id: ${reviewState.finalMessageId ?? "unknown"}`,
          `Correlation id: ${reviewState.correlationId}`,
          `Reviewer feedback:\n${reviewerText}`
        ].join("\n\n")
      };
    }, { priority: 100, timeoutMs: 180000 });

    api.on("message_sending", async (event) => {
      const config = resolveConfig(api.pluginConfig);
      const captured = await capturePendingOpenClawDraftSend({ api, event, config });
      if (captured) return captured;
      if (isSilentReplyText(event.content)) {
        liveLog("parent silent reply passed to OpenClaw core", {
          to: event.to,
          threadId: event.threadId
        });
        return;
      }
      const autoReplySuppression = consumeThreadAutoReplySuppression(event.to ?? event.threadId ?? event.channelId);
      if (autoReplySuppression) {
        liveLog("resumed user decision auto reply suppressed", {
          to: event.to,
          threadId: event.threadId,
          messageId: autoReplySuppression.messageId,
          finalMessageId: autoReplySuppression.finalMessageId
        });
        return {
          content: "NO_REPLY",
          metadata: { ...(event.metadata ?? {}), interAgentOrchestrationSuppressed: true }
        };
      }
      const parentAutoReplySuppression = consumeParentAutoReplySuppression(event.to ?? event.channelId);
      if (parentAutoReplySuppression) {
        liveLog("completed orchestration parent auto reply suppressed", {
          to: event.to,
          threadId: event.threadId,
          finalMessageId: parentAutoReplySuppression.finalMessageId,
          resultThreadId: parentAutoReplySuppression.threadId,
          reason: parentAutoReplySuppression.reason
        });
        return {
          content: "NO_REPLY",
          metadata: { ...(event.metadata ?? {}), interAgentOrchestrationSuppressed: true }
        };
      }
      if (!isParentChannelLeakageContent(event.content)) return;
      liveLog("parent channel delivery cancelled", {
        to: event.to,
        threadId: event.threadId,
        contentPreview: previewText(event.content)
      });
      return {
        content: "NO_REPLY",
        metadata: { ...(event.metadata ?? {}), interAgentOrchestrationSuppressed: true }
      };
    }, { priority: 100 });
  }
});
