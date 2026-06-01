import { AiAgentDatabase } from "../src/db.ts";
import { CompanyOrchestrator } from "../src/orchestrator.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor } from "../src/types.ts";

const db = new AiAgentDatabase();

const discord: DiscordDelivery = {
  async createThread({ parentChannelId, name }) {
    console.log(`[AI_AGENT] thread created parent=${parentChannelId} name="${name}" threadId=thread-demo-1`);
    return { threadId: "thread-demo-1", url: "https://discord.test/thread-demo-1" };
  },
  async postParent({ content }) {
    console.log(`[parent] ${content}`);
  },
  async postThread({ threadId, content }) {
    console.log(`[thread:${threadId}]\n${content}\n`);
  },
};

const owner: OwnerExecutor = {
  async createDraft() {
    return [
      "OpenClaw owner draft:",
      "- 영상은 3막 구조로 시작한다.",
      "- 첫 3초는 강한 후킹 컷으로 시작한다.",
      "- 후반부는 캐릭터 실루엣과 브랜드 컬러를 반복 노출한다.",
    ].join("\n");
  },
};

const reviewer: ReviewerExecutor = {
  async review() {
    return {
      verdict: "agree",
      content: [
        "Hermes review:",
        "agree. 후킹/구조/브랜드 반복은 타당하다.",
        "다만 썸네일 전환 컷과 숏폼 재사용 가능성을 final에 명시하는 것이 좋다.",
      ].join("\n"),
    };
  },
};

const finalizer: FinalizerExecutor = {
  async synthesize({ draft, review }) {
    return [
      "Final synthesis:",
      "뮤직비디오 오프닝은 3초 후킹 컷, 3막 감정선, 반복 가능한 브랜드 컬러를 중심으로 구성한다.",
      "",
      "Accepted Hermes feedback:",
      "- 썸네일 전환 컷을 포함한다.",
      "- 숏폼 재사용 가능성을 명시한다.",
      "",
      "Source draft:",
      draft,
      "",
      "Source review:",
      review,
    ].join("\n");
  },
};

const orchestrator = new CompanyOrchestrator({
  db,
  discord,
  owner,
  reviewer,
  finalizer,
  idFactory: () => "task-demo-1",
});

const result = await orchestrator.runUserRequest({
  project: { channelId: "project-channel-demo", name: "Virtual AI Company" },
  userRequest: "뮤직비디오 오프닝 아이디어를 회의해줘.",
});

console.log(`[AI_AGENT] completed status=${result.status} threadId=${result.threadId}`);
db.close();
