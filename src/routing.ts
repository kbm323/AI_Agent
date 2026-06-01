import type { TeamRoute } from "./types.ts";

const routePatterns: Array<{ route: TeamRoute; patterns: RegExp[] }> = [
  {
    route: "executive",
    patterns: [/예산|법무|계약|IP|저작권|상표|브랜드\s*리스크|외부\s*공개|승인|우선순위|리스크/i],
  },
  {
    route: "tech",
    patterns: [/코드|구현|API|CLI|서버|Discord|디스코드|봇|Unreal|언리얼|VFX|자동화|n8n|Make|성능|보안/i],
  },
  {
    route: "art",
    patterns: [/캐릭터|비주얼|컨셉아트|색감|실루엣|의상|소품|공간\s*미술|아트|디자인/i],
  },
  {
    route: "marketing",
    patterns: [/마케팅|제목|썸네일|쇼츠|숏폼|SNS|카피|팬덤|클릭률|포지셔닝/i],
  },
  {
    route: "content",
    patterns: [/뮤직비디오|뮤비|영상|스토리|감정선|오프닝|엔딩|후킹|기획|시청자/i],
  },
];

export function classifyTeamRoute(userRequest: string): TeamRoute {
  for (const { route, patterns } of routePatterns) {
    if (patterns.some((pattern) => pattern.test(userRequest))) {
      return route;
    }
  }

  return "content";
}
