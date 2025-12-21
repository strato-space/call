# MediaGenBlender feature design (plan only)

1) Include model name with image output
- Repo: prompt
- Files: prompt/MediaGenBlender/project.md
- Functions: none
- Data: none
- Plan: update output instructions so image responses include a model label (e.g., "OpenAI gpt-image-1.5") in the output block.

2) Running cost totals in the Telegram bot (all-time + daily)
- Repo: call
- Files: call/telegram_bot/bot.py, call/lib/api.py (if needed to expose pricing), call/app/call.py (if needed to surface pricing)
- Functions: add a "cost_totals" helper (read/update), add optional "append_cost_totals" formatter
- Data: reuse existing SQLite (call/.cache/call/call.db), add table
  - Table: bot_cost_totals
  - Columns: total_cost_all_time REAL, total_cost_today REAL, last_updated_date TEXT (YYYY-MM-DD)
- Plan: parse pricing from call result; on success update totals; if BOT_SHOW_COST_TOTALS=1 append a line with both totals and last_updated_date.

3) Session logging for reflection + rating buttons
- Repo: call
- Files: call/telegram_bot/bot.py, call/app/call.py (hook), call/lib/api.py (if needed)
- Functions: log_session_entry(...), attach_rating_buttons(...), handle_rating_callback(...)
- Data: reuse call SQLite, add table
  - Table: bot_session_log
  - Columns: chat_id, message_id, from_id, input, output, ai_request_id,
    input_timestamp, output_timestamp, rating_value, rating_user_id, rating_timestamp
- Plan: on each bot response, insert row and attach inline buttons: "1 ", "2 ", "3 ", "4 ", "5 ".
  Update row when rating callback arrives.

4) Cancel command (/cancel) tied to ai_request_id
- Repo: call
- Files: call/telegram_bot/bot.py, call/lib/api.py, call/app/call.py
- Functions: cancel_request(ai_request_id), resolve_last_request_id(chat_id)
- Data: reuse bot_session_log (feature 3) to find latest ai_request_id
- Plan: implement /cancel [id?] to cancel latest in chat or explicit id; propagate cancel to provider if supported.

5) Dedicated user guide for MediaGenBlender
- Repo: prompt, call
- Files: prompt/MediaGenBlender/tg-user-guide.ru.md (new), call/tg-user-guide.ru.md (link/update)
- Functions: none
- Data: none
- Plan: create a specialized guide mixing bot commands + prompt commands; reference from call guide.

6) Enforce n=1 (no batch of 5 images)
- Repo: prompt
- Files: prompt/MediaGenBlender/project.md
- Functions: none
- Data: none
- Plan: add explicit instruction to force n=1 and ignore user n>1.

7) Do not generate by default; require [image]/[video] or /image /video
- Repo: prompt, call
- Files: prompt/MediaGenBlender/project.md, call/telegram_bot/bot.py (command aliases)
- Functions: command router tweak to treat /image /video as explicit intent
- Data: none
- Plan: default to text response unless a keyword/command is present.

8) Model selection keywords + /models command
- Repo: prompt, call
- Files: prompt/MediaGenBlender/project.md, call/telegram_bot/bot.py, call/lib/api.py
- Functions: list_models(), resolve_model_keyword(text)
- Data: optional metadata list of models in prompt METADATA
- Plan: allow [image] [openai1.5] or similar; /models returns available models from tool schemas or prompt metadata.

9) Video duration defaults to 4 seconds
- Repo: prompt
- Files: prompt/MediaGenBlender/project.md
- Functions: none
- Data: none
- Plan: if user does not specify duration, force 4s; if specified, round to nearest allowed value within max.

10) Continuous typing while generating
- Repo: call
- Files: call/telegram_bot/bot.py
- Functions: typing_loop(...) running every 5s; stop on completion
- Data: none
- Plan: start background typing task per request; cancel on completion.

11) Document multi-image edit limits
- Repo: prompt
- Files: prompt/MediaGenBlender/project.md
- Functions: none
- Data: none
- Plan: add guidance: images-edit accepts 1..16 images; video accepts exactly one image.

12) /prompt command to run prompt_engineer agent
- Repo: call, prompt
- Files: call/telegram_bot/bot.py, prompt/MediaGenBlender/prompts/prompt.md, prompt/MediaGenBlender/project.md
- Functions: handle_prompt_command(...), invoke_prompt_engineer(...)
- Data: none
- Plan: add /prompt or keyword to call prompt_engineer and replace prompt text.

13) /scene, /clear-scene, /template, /clear-template, /brand
- Repo: call
- Files: call/telegram_bot/bot.py, call/lib/api.py (payload injection)
- Functions: set_chat_setting(...), get_chat_setting(...), clear_chat_setting(...)
- Data: reuse call SQLite, add table
  - Table: chat_settings
  - Columns: chat_id, scene, template, brand, updated_at
- Plan: store per-chat scene/template/brand; inject scene into payload sent to call after input/response fields.

14) Prompt METADATA to declare supported commands
- Repo: fast-agent (or call if parsing happens there), prompt
- Files: fast-agent/src/fast_agent/core/* (metadata parser), prompt/MediaGenBlender/project.md
- Functions: parse_prompt_metadata(...), expose_commands_metadata(...)
- Data: add metadata field: commands: [ ... ]
- Plan: allow prompt to declare command list in METADATA; bot can use this to render /models or help text.
