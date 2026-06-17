"""Debug script for context relevance validator scores."""
import sys
sys.path.insert(0, "/home/kbm/F:ai-projects/AI_Agent")

from src.context_relevance_validator import validate_context_relevance, _tokenise

ctx = {
    'agenda': '뮤직비디오 오프닝 시퀀스에 대한 비주얼 컨셉 아이디에이션. 신규 싱글 발매를 위한 티저 콘텐츠 기획.',
    'tags': ['music-video', 'visual-concept', 'opening-sequence', 'teaser-content', 'brand-identity'],
    'round_count': 1,
    'required_roles': ['art-director', 'content-director', 'marketing-lead'],
    'optional_roles': ['tech-director', 'validator'],
    'decisions': [
        {'decision_id': 'dec_001', 'role_id': 'art-director', 'content': '네온 느와르 팔레트 채택', 'round': 1},
        {'decision_id': 'dec_002', 'role_id': 'content-director', 'content': '스토리텔링 기반 오프닝', 'round': 1},
    ],
}

response = '네온 느와르 스타일의 비주얼 컨셉을 제안합니다. 뮤직비디오 오프닝 시퀀스는 고대비 색감과 실루엣 중심의 구도로 구성하여 브랜드 아이덴티티를 강조합니다. 타이포그래피는 미니멀 산세리프를 사용하고, 티저 콘텐츠는 15초 컷의 인트로 영상으로 기획합니다. 이는 음악 장르와의 조화를 최우선으로 한 결정입니다.'

result = validate_context_relevance(response, ctx)
print('passed:', result.passed)
print('overall:', result.overall_score)
print('agenda:', result.agenda_relevance_score)
print('topic:', result.topic_alignment_score)
print('off_topic:', result.off_topic_score)
print('ref:', result.reference_consistency_score)
print('violations:')
for v in result.violations:
    print(f'  [{v.severity}] {v.dimension}: {v.message[:150]}')

# Token analysis
print()
at = set(_tokenise(ctx['agenda']))
rt = set(_tokenise(response))
print('Agenda tokens:', sorted(at))
print()
print('Response tokens:', sorted(rt))
print()
print('Overlap:', sorted(at & rt))
print('Agenda token count:', len(at))
print('Response token count:', len(rt))
print('Overlap count:', len(at & rt))
