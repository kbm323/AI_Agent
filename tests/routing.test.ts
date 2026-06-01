import test from "node:test";
import assert from "node:assert/strict";
import { classifyTeamRoute } from "../src/routing.ts";

test("classifies content planning requests", () => {
  assert.equal(classifyTeamRoute("뮤직비디오 오프닝 장면 아이디어를 만들어줘"), "content");
});

test("classifies art direction requests", () => {
  assert.equal(classifyTeamRoute("캐릭터 비주얼 컨셉아트 색감과 의상 방향을 제안해줘"), "art");
});

test("classifies technical implementation requests", () => {
  assert.equal(classifyTeamRoute("Discord bot API와 Unreal VFX 구현 계획을 짜줘"), "tech");
});

test("classifies marketing requests", () => {
  assert.equal(classifyTeamRoute("쇼츠 제목 썸네일 SNS 카피를 제안해줘"), "marketing");
});

test("classifies executive risk requests", () => {
  assert.equal(classifyTeamRoute("예산 법무 IP 브랜드 리스크를 검토해줘"), "executive");
});

test("defaults ambiguous requests to content", () => {
  assert.equal(classifyTeamRoute("새 프로젝트 아이디어 회의하자"), "content");
});
