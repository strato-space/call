# MediaGenBlender feature design (plan only)

## Prompt-only changes (simple)
Files: prompt/MediaGenBlender/project.md, prompt/MediaGenBlender/tg-user-guide.ru.md (new)

1) [x] Include model name with image output
- Repo: prompt
- Plan: add model label to the output block (example: "OpenAI gpt-image-1.5").

2) [x] Enforce n=1 (no batch generation)
- Repo: prompt
- Plan: force n=1 and ignore user n>1.

3) [x] Video duration default to 4 seconds
- Repo: prompt
- Plan: if duration is not specified, force 4s; if specified, round to nearest allowed value within max.

4) [x] Document multi-image edit limits
- Repo: prompt
- Plan: images-edit accepts 1..16 images; video accepts exactly one image.

5) [x] /prompt command handled by prompt text
- Repo: prompt
- Files: prompt/MediaGenBlender/prompts/prompt.md, prompt/MediaGenBlender/project.md
- Plan: treat `/prompt` and similar keywords as plain text; prompt routes to prompt_engineer without bot code changes.

6) [x] Require explicit generation intent (/image, /video, [image], [video])
- Repo: prompt
- Plan: treat `/image` and `/video` as plain-text commands; default to text reply unless /image, /video, or [image]/[video] keyword is present.

## Docs (simple)

7) [x] Dedicated MediaGenBlender user guide
- Repo: prompt, call
- Files: prompt/MediaGenBlender/tg-user-guide.ru.md (new), call/tg-user-guide.ru.md (link/update)
- Plan: specialized guide mixing bot commands + prompt commands; link from call guide.

## Code changes (call)

8) [x] Continuous typing while generating
- Repo: call (bot-level only)
- Files: call/telegram_bot/bot.py
- Functions: typing_loop(...), start/stop in request handler
- Plan: send ChatAction.TYPING every 5s until the call completes.

## Prompt-only updates (done)

- [x] Allow `file://` URLs as media refs in the prompt instructions.
- [x] Strip `Model:`, `Cost:`, `Quality:`, `Size:` lines from tool prompts before calling tools.
- [x] Map `horizontal`/`vertical` to tool sizes and remove those words from the prompt; include `Quality` and `Size` in output only when non-default.

## Code changes (call) — additional (done)

- [x] Include URLs from reply text and current message text as `resource_link` context items (dedupe by URI).

## Advanced / multi-repo (complex)

9) [ ] Session logging + rating buttons
- Repo: call
- Files: call/telegram_bot/bot.py, call/app/call.py, call/lib/api.py
- Functions: log_session_entry(...), attach_rating_buttons(...), handle_rating_callback(...)
- Data: reuse call SQLite (call/.cache/call/call.db), add table
  - Table: bot_session_log
  - Columns: chat_id, message_id, from_id, input, output, ai_request_id,
    input_timestamp, output_timestamp, rating_value, rating_user_id, rating_timestamp
- Plan: on each bot response, insert row and attach inline buttons:
  "1 👎", "2 😞", "3 😕", "4 👍", "5 ⭐️". Store output as returned (includes image links/prompts).
  Update row on rating callback.

10) [ ] /cansel command tied to ai_request_id
- Repo: call
- Files: call/telegram_bot/bot.py, call/lib/api.py, call/app/call.py
- Functions: cancel_request(ai_request_id), resolve_last_request_id(chat_id)
- Data: reuse bot_session_log (feature 9) to find latest ai_request_id
- Plan: implement /cansel [id?] (alias /cancel) to cancel latest in chat or explicit id; propagate cancel to provider if supported.

11) [ ] Running cost totals in the Telegram bot (all-time + daily)
- Repo: call
- Files: call/telegram_bot/bot.py, call/lib/api.py (if needed), call/app/call.py (if needed)
- Functions: cost_totals_read_update(...), append_cost_totals(...)
- Data: reuse call SQLite, add table
  - Table: bot_cost_totals
  - Columns: total_cost_all_time REAL, total_cost_today REAL, last_updated_date TEXT (YYYY-MM-DD)
- Plan: parse pricing from call result; on success update totals; when BOT_SHOW_COST_TOTALS=1, append a line with total_all_time, total_today, last_updated_date.

12) [x] Chat-scoped instructions (single command)
- Repo: call
- Files: call/telegram_bot/bot.py (plus a helper module if needed; no api wiring)
- Functions: set_chat_setting(...), get_chat_setting(...), clear_chat_setting(...)
- Plan:
  - Provide one command: `/instructions` (read/write/clear). For specialized bots with a project, resolve project root from call_db metadata and use `<project_root>/instructions/`; for the main bot without a project, use `./instructions/` under the working directory. Ensure the folder exists, write `instructions_{chat_id}.md` via `set_chat_setting`, and support reading/clearing that file.
  - Behavior: when called with text, take `replay` (if present) + the command text (trim `/instructions` or `/instructions@BotNameBot`) + current message `input`, join with newlines, and persist to the file. When called with no arguments, output the current instructions file content. Ignore if no file exists.
  - `clear_chat_setting` removes the instructions file for the chat.
  - For every outgoing call, add an `instructions` attribute from the file content placed before `replay` and `input`—omit the attribute entirely if no instructions file exists.

13) [ ] Model selection keywords + /models + prompt METADATA commands (deduped)
- Repo: prompt, call, fast-agent (if metadata parsing lives there)
- Files: prompt/MediaGenBlender/project.md, call/telegram_bot/bot.py, call/lib/api.py,
  fast-agent/src/fast_agent/core/* (metadata parser)
- Functions: parse_prompt_metadata(...), list_models(), resolve_model_keyword(text)
- Data: add METADATA field in prompt (commands/models list)
- Plan: allow [image] [openai1.5]-style keywords; /models returns available models from tool schemas
  or prompt metadata; prompt METADATA declares command list for bot help.

14) [ ] Keep prompt/MediaGenBlender/project.md in sync with bot instructions
- Repo: prompt, call
- Files: prompt/MediaGenBlender/project.md, call/telegram_bot/bot.py (or helper)
- Plan: when `/instructions` updates per-chat instructions, also update the project's prompt header/commands section automatically (no manual reminder needed). Define how to merge/replace instructions and ensure idempotency.
