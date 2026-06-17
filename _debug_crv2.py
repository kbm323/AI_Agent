import sys
sys.path.insert(0, "/home/kbm/F:ai-projects/AI_Agent")

from src.context_relevance_validator import validate_context_relevance, _tokenise, _tag_matches_response

# Test 1: Korean response with English tags mixed in
ctx = {
    "agenda": (
        "뮤직비디오 오프닝 시퀀스에 대한 비주얼 컨셉 아이디에이션. "
        "신규 싱글 발매를 위한 티저 콘텐츠 기획."
    ),
    "tags": ["music-video", "visual-concept", "opening-sequence", "teaser-content", "brand-identity"],
    "round_count": 1,
    "required_roles": ["art-director"],
    "optional_roles": [],
    "decisions": [],
}

response = (
    "music-video의 visual-concept으로 neon-noir 스타일을 "
    "제안합니다. opening-sequence는 teaser-content로 활용."
)

result = validate_context_relevance(response, ctx)
print("passed:", result.passed)
print("overall:", result.overall_score)
print("agenda:", result.agenda_relevance_score)
print("topic:", result.topic_alignment_score)
print("off_topic:", result.off_topic_score)
print("ref:", result.reference_consistency_score)
print("violations:")
for v in result.violations:
    print(f"  [{v.severity}] {v.dimension}: {v.message[:150]}")

# Test 2: English response with Korean agenda
response2 = (
    "I suggest a neon-noir visual concept for the music video "
    "opening sequence. The brand identity should be emphasized."
)
result2 = validate_context_relevance(response2, ctx)
print("\n--- English response ---")
print("overall:", result2.overall_score)
print("agenda:", result2.agenda_relevance_score)
print("topic:", result2.topic_alignment_score)
