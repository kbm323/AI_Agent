from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parents[1] / "hermes_skills" / "kakao-collect" / "SKILL.md"
)


def test_kakao_collect_skill_uses_buttons_and_read_only_tools() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "name: kakao-collect" in skill
    assert "`list_recent_kakaotalk_rooms`" in skill
    assert "`clarify`" in skill
    assert "`collect_kakaotalk_room_readonly`" in skill
    assert "Never call any KakaoTalk send or reply operation" in skill
    assert "initial_baseline" in skill
