---
name: kakao-collect
description: Collect unseen messages from one approved KakaoTalk room.
---

# KakaoTalk Read-Only Collection

Follow this workflow exactly:

1. Ignore trailing user text. Call `list_recent_kakaotalk_rooms` once with no
   arguments.
2. If the result is not successful, return its message and stop.
3. Present every returned room, up to 10, through the `clarify` tool as one
   button choice. Display only the room name; retain its `chat_id` internally.
4. If the user cancels, the choice expires, or the selected room is not in the
   returned list, stop without calling another tool.
5. If `has_cursor` is false, explain that the first run establishes the current
   point and call `collect_kakaotalk_room_readonly` with the selected `chat_id`
   and `initial_baseline` set to `current`.
6. Otherwise call `collect_kakaotalk_room_readonly` with only the selected
   `chat_id`.
7. Report only the room name, collected count, whether the baseline was
   initialized, and cursor. Never include message bodies.

Never call any KakaoTalk send or reply operation. Never invent a room, room ID,
cursor, or collection result.
