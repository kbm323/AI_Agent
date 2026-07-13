---
name: save
description: Save the current Discord thread to Obsidian.
---

# Save Skill

Follow these rules exactly for the user text trailing `/save`:

1. If the trailing text is empty or whitespace only, call `save_discord_thread_to_obsidian` exactly once with no arguments. Return only the `message` field from the tool's JSON result, unchanged. Do not add any prose.
2. If the trailing text contains any non-whitespace character, do not call any tool. Return exactly `사용법: /save`.
