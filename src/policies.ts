import type { EscalationPolicy } from "./types.ts";

const defaultEscalationPatterns = [
  { pattern: /결제|구매|유료\s*구독|카드\s*등록|송금|payment|purchase/i, reason: "budget_or_payment" },
  { pattern: /법무\s*검토|저작권\s*침해|상표\s*등록|계약\s*(체결|서명)|라이선스\s*(구매|계약)|초상권\s*허가|IP\s*(계약|침해)/i, reason: "legal_or_ip" },
  { pattern: /브랜드.{0,12}(훼손|리스크|변경|승인|공개)|외부\s*공개|실제\s*게시|업로드\s*해줘|배포\s*해줘|출시\s*해줘|publish|deploy/i, reason: "brand_or_public_release" },
  { pattern: /네가\s*대신\s*(선택|골라)|최종\s*승인|사용자\s*승인|내\s*대신\s*결정/i, reason: "subjective_choice" },
  { pattern: /삭제\s*해줘|초기화\s*해줘|되돌릴 수 없는|irreversible|reset\s+--hard|drop\s+table/i, reason: "irreversible_action" },
  { pattern: /git push|push\b|merge\b|main\b|production/i, reason: "source_control_or_production" },
] as const;

export function createDefaultEscalationPolicy(): EscalationPolicy {
  return {
    requiresUserDecision({ userRequest, draft, review, reviewerVerdict }) {
      const reasons = defaultEscalationPatterns
        .filter(({ pattern }) => pattern.test(userRequest))
        .map(({ reason }) => reason);

      if (reviewerVerdict === "needs_user_decision") {
        reasons.push("reviewer_requested_user_decision");
      }

      return Array.from(new Set(reasons));
    },
  };
}

export function summarizeForThread(content: string, maxChars = 1200): string {
  const normalized = content.trim().replace(/\n{3,}/g, "\n\n");
  if (normalized.length <= maxChars) return normalized;
  return `${normalized.slice(0, maxChars - 1)}…`;
}
