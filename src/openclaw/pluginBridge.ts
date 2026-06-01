import { buildReviewerRequest } from "../orchestrator.ts";
import type { ReviewerVerdict, TaskRecord } from "../types.ts";

export type HermesRoute = "cli" | "local-api" | "gateway-command" | "discord-mention-polling";

export interface OpenClawCenteredTaskInput {
  parentChannelId: string;
  threadId: string;
  userRequest: string;
  capturedOpenClawDraft: string;
  hermesRoute: HermesRoute;
  round: number;
}

export interface OpenClawCenteredTaskPlan {
  parentNotice: string;
  threadId: string;
  openClawDraftPost: string;
  hermesReviewerRequest: string;
  hermesRoute: HermesRoute;
}

export interface HermesHybridReviewResult {
  content: string;
  verdict: ReviewerVerdict;
  route: HermesRoute;
}

export interface OpenClawPluginReviewInput {
  task: TaskRecord;
  userRequest: string;
  capturedOpenClawDraft: string;
  reviewerRequest: string;
  round: number;
}

export interface OpenClawPluginReviewerHost {
  reviewWithCli?(input: OpenClawPluginReviewInput): Promise<HermesHybridReviewResult>;
  reviewWithLocalApi?(input: OpenClawPluginReviewInput): Promise<HermesHybridReviewResult>;
  reviewWithGatewayCommand?(input: OpenClawPluginReviewInput): Promise<HermesHybridReviewResult>;
  reviewWithDiscordMentionPolling?(input: OpenClawPluginReviewInput): Promise<HermesHybridReviewResult>;
}

const defaultHermesRoutePreference: HermesRoute[] = ["cli", "local-api", "gateway-command", "discord-mention-polling"];

export function buildOpenClawCenteredTaskPlan(input: OpenClawCenteredTaskInput): OpenClawCenteredTaskPlan {
  const capturedDraft = input.capturedOpenClawDraft.trim();
  if (!capturedDraft || normalize(capturedDraft) === normalize(input.userRequest)) {
    throw new Error("OpenClaw draft capture failed");
  }

  return {
    parentNotice: `Agent discussion started -> ${input.threadId}`,
    threadId: input.threadId,
    openClawDraftPost: ["OpenClaw draft", "", capturedDraft].join("\n"),
    hermesReviewerRequest: buildReviewerRequest({
      userRequest: input.userRequest,
      draft: capturedDraft,
      round: input.round,
    }),
    hermesRoute: input.hermesRoute,
  };
}

export async function requestHermesHybridReview(
  host: OpenClawPluginReviewerHost,
  input: OpenClawPluginReviewInput & { preferredRoutes?: HermesRoute[] },
): Promise<HermesHybridReviewResult> {
  const routes = input.preferredRoutes ?? defaultHermesRoutePreference;

  for (const route of routes) {
    const handler = handlerForRoute(host, route);
    if (!handler) continue;

    try {
      return await handler(input);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      console.warn(`[AI_AGENT] Hermes route failed route=${route} reason=${reason}`);
    }
  }

  throw new Error(`No Hermes review route succeeded: ${routes.join(", ")}`);
}

function handlerForRoute(
  host: OpenClawPluginReviewerHost,
  route: HermesRoute,
): ((input: OpenClawPluginReviewInput) => Promise<HermesHybridReviewResult>) | undefined {
  switch (route) {
    case "cli":
      return host.reviewWithCli?.bind(host);
    case "local-api":
      return host.reviewWithLocalApi?.bind(host);
    case "gateway-command":
      return host.reviewWithGatewayCommand?.bind(host);
    case "discord-mention-polling":
      return host.reviewWithDiscordMentionPolling?.bind(host);
  }
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}
