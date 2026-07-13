from pathlib import Path

SKILL_PATH = Path(__file__).resolve().parents[1] / "hermes_skills" / "save" / "SKILL.md"


def test_save_skill_metadata_and_instructions_are_strict() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert skill == (
        "---\n"
        "name: save\n"
        "description: Save the current Discord thread to Obsidian.\n"
        "---\n"
        "\n"
        "# Save Skill\n"
        "\n"
        "Follow these rules exactly for the user text trailing `/save`:\n"
        "\n"
        "1. If the trailing text is empty or whitespace only, call "
        "`save_discord_thread_to_obsidian` exactly once with no arguments. "
        "Return only the `message` field from the tool's JSON result, unchanged. "
        "Do not add any prose.\n"
        "2. If the trailing text contains any non-whitespace character, do not "
        "call any tool. Return exactly `사용법: /save`.\n"
    )
