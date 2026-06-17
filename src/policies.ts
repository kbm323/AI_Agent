import type { EscalationPolicy } from "./types.ts";

const defaultEscalationPatterns = [
  { pattern: /예산|결제|구매|비용|가격|구독/, reason: "budget_or_payment" },
  { pattern: /법무|저작권|상표|계약|라이선스|초상권|IP\b/i, reason: "legal_or_ip" },
  { pattern: /브랜드.{0,12}(훼손|리스크|변경|승인|공개)|외부 공개|게시|업로드|배포|출시|publish|deploy/i, reason: "brand_or_public_release" },
  { pattern: /선택|골라|A\/B|후보|취향|목소리|보이스/, reason: "subjective_choice" },
  { pattern: /삭제|초기화|되돌릴 수|irreversible|reset|drop/i, reason: "irreversible_action" },
  { pattern: /git push|push\b|merge\b|main\b|production/i, reason: "source_control_or_production" },
] as const;

export function createDefaultEscalationPolicy(): EscalationPolicy {
  return {
    requiresUserDecision({ userRequest, draft, review, reviewerVerdict }) {
      const combined = [userRequest, draft, review].join("\n");
      const reasons = defaultEscalationPatterns
        .filter(({ pattern }) => pattern.test(combined))
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
