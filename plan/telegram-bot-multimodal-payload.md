# Telegram Bot Multimodal Payload Plan

## 1. Current Behavior (Summary)

- Telegram bot builds structured payloads via `build_input_payload_from_reply()` in `telegram_bot/bot.py`.
- This helper:
  - Reads reply text from `update.message.reply_to_message`.
  - Extracts a replied document (`reply_to_message.document`) and resolves it to a Telegram file URL.
  - Passes `target`, `main_text`, `extra_context` and `reply_text` into `call_api.build_input_payload()`.
- `call_api.build_input_payload()` constructs a JSON payload with ordered keys: `target`, `replay`, `input`, `context`.
- The resulting JSON string is passed as the `input` argument into `call_api.call_async()`.

Effective payload schema today:

```jsonc
{
  "target": "optional-target-id",      // @Name / prompt id, when present
  "replay": "optional reply text",     // text from replied-to message
  "input": "current user text",        // text from the new message
  "context": [                           // optional array
    // 1) Extra context from Telegram reply (documents as URLs)
    // 2) Repo-based references resolved from @Tokens in input
  ]
}
```

This contract is already used in MCP hooks and PM routing (see `docs/formatting.md`).

---

## 2. Goal

- Keep the top-level payload contract `{target, replay, input, context}` unchanged.
- Make `replay` / `input` effectively **multimodal** by enriching `context` with **links to all attachments**:
  - images/photos
  - documents (PDF, DOCX, etc.)
  - video / video_note
  - audio / voice messages
- Support attachments from both:
  - the current message (`input` side)
  - the replied-to message (`replay` side).

---

## 3. Context item schema in `context`

### 3.1. Attachment items (`type: "resource_link"`)

Align with the MCP `resource_link` contract while specializing for Telegram attachments.

Proposed structure for each attachment:

```jsonc
{
  "type": "resource_link",           // MCP-style reference to an external resource
  "uri": "https://api.telegram.org/file/bot<token>/<path>",
  "name": "filename-or-kind",        // Telegram file_name or synthesized name
  "mimeType": "image/jpeg",          // optional, when available
  "description": "Telegram attachment (photo/document/video/voice)",
  "source": {                        // extension with Telegram-specific metadata
    "type": "telegram",
    "chat_id": 123456,
    "message_id": 789,
    "direction": "input" | "replay"  // from new message or from replied-to message
  }
}
```

 Notes:

 - For photos (`message.photo` is an array of sizes), pick the **smallest** size
   (first element or by min `width`/`height`) when building a single `resource_link`.
 - For photos without `file_name`, synthesize `name` (e.g. `photo_<file_id>.jpg`).
 - For voice/video, use appropriate extension if Telegram exposes mime-type.
 - Maintain compatibility: agents that ignore unknown fields still work.

### 3.2. Telegram raw message items (`type: "telegram_message"`)

To preserve full Telegram semantics (threading, topics, link previews, etc.), also
add raw `Message` objects as separate context items:

```jsonc
{
  "type": "telegram_message",
  "message": {
    // Raw Telegram message object as logged in "Update raw":
    // message_id, chat, from, date, text/caption, entities,
    // message_thread_id, is_topic_message, reply_to_message, photo, document, ...
  }
}
```

- `message` is taken **as is** from `update.message.to_dict()` or
  `update.message.reply_to_message.to_dict()` without нормализации.
- Inclusion of these items is controlled by `CALL_INCLUDE_TELEGRAM_MESSAGE`
  (env flag read by the bot layer):
  - `CALL_INCLUDE_TELEGRAM_MESSAGE=1` → append `{"type": "telegram_message", ...}`
    for the current message and its reply (when available).
  - `CALL_INCLUDE_TELEGRAM_MESSAGE=0` → do **not** add these items (default).
- This is **not** an MCP resource; это просто структурированный контекст.
- При необходимости агенты могут использовать либо только `resource_link` элементы,
  либо обращаться к полному Telegram-объекту.

### 3.3. Telegram bot info items (`type: "telegram_bot"`)

To provide agents with information about the current bot (name, username, etc.),
include the result of Telegram `getMe` in the context as a dedicated item:

```jsonc
{
  "type": "telegram_bot",
  "bot": {
    // Raw User object returned by getMe():
    // id, is_bot, first_name, username, language_code?, can_join_groups?, ...
  }
}
```

- `bot` is taken **as is** from `context.bot.get_me().to_dict()` (or from a
  cached value obtained at application startup).
- Inclusion of this item is controlled by
  `CALL_INCLUDE_TELEGRAM_BOT` (env flag read by the bot layer):
  - `CALL_INCLUDE_TELEGRAM_BOT=1` → always append `{"type": "telegram_bot", ...}`
    to the `context` array.
  - `CALL_INCLUDE_TELEGRAM_BOT=0` → do **not** add this item (default).

### 3.4. Payload contract (unchanged)

Top-level JSON stays the same:

```jsonc
{
  "target": "optional-target-id",
  "replay": "string reply text or null",
  "input": "string current text or null",
  "context": [
    // repo references (existing)
    // + Telegram attachments (new)
  ]
}
```

No new top-level keys are introduced; multimodальность живёт в массиве `context`.

---

## 4. Implementation Plan

### 4.1. Helper for Attachment Extraction

**File:** `telegram_bot/bot.py`

1. [x] Introduce internal helper, e.g. `_collect_telegram_attachments(message, context, *, direction) -> list[dict]`.
2. [x] For a given `telegram.Message`:
   - [x] Inspect these attributes (when present):
     - [x] `document`
     - [x] `photo` (list of sizes; pick variant according to `TELEGRAM_PHOTO_VARIANT` env flag:
       - `smallest`/`min` → minimal area,
       - `largest`/`max` → maximal area (default),
       - `first` → first element,
       - `last` → last element)
     - [x] `video`
     - [x] `video_note`
     - [x] `audio`
     - [x] `voice`
     - [x] `animation`
   - [x] For each media entity:
     - [x] Call `context.bot.get_file(file_id)` once per unique `file_id`.
     - [x] Build `file_path` URL:
       - [x] Start from `file.file_path`.
       - [x] If it is not absolute, prefix with `https://api.telegram.org/file/bot{CALL_TELEGRAM_TOKEN or TELEGRAM_TOKEN}/{file_path}` (reuse existing logic from document handling).
     - [x] Build context item according to schema in §3.1.
3. [x] Deduplicate attachments by `(url, name)` to avoid duplicates if multiple sizes map to same file.

### 4.2. Extend `build_input_payload_from_reply`

Current behavior (simplified):

- Reads `reply_text` from `update.message.reply_to_message`.
- Adds a single document from `reply_to_message.document` into `ctx_items`.
- Delegates to `call_api.build_input_payload(...)`.

Planned behavior:

1. [x] Initialize `ctx_items = []`, `reply_text = ""`.
2. [x] If `update.message.reply_to_message` exists:
   - [x] Keep existing logic for `reply_text` (text or caption).
   - [x] Call `_collect_telegram_attachments(reply_to_message, context, direction="replay")` and extend `ctx_items`.
3. [x] Also inspect the **current message**:
   - [x] Call `_collect_telegram_attachments(update.message, context, direction="input")` and extend `ctx_items`.
4. [x] Pass merged context into `build_input_payload`:

   ```python
   input_arg, payload = call_api.build_input_payload(
       target=(name or None),
       main_text=(main_text or ""),
       extra_context=ctx_items or None,
       reply_text=(reply_text or None),
   )
   ```

5. [x] Preserve existing logging of `[bot][PAYLOAD]` (pretty-printed JSON) so attachment items are visible for debugging.

### 4.3. Preserve `build_input_payload` Contract

**File:** `lib/api.py`

- [x] Do **not** change function signature or ordering of keys in `ordered` dict.
- [x] Continue to:
  - [x] set `ordered["target"]` when provided,
  - [x] set `ordered["replay"]` when `reply_text` is non-empty,
  - [x] set `ordered["input"]` from `main_text`,
  - [x] append mixed context items (repo references + Telegram attachments) into `ordered["context"]`.
- [x] No additional serialization layers (avoid nested JSON-in-JSON).

### 4.4. Context Feature Flags

- [x] Gate raw Telegram message objects behind `CALL_INCLUDE_TELEGRAM_MESSAGE` /
  `INCLUDE_TELEGRAM_MESSAGE_CONTEXT` so `{type: "telegram_message"}` items
  are added only when explicitly enabled (default: `0`, disabled).
- [x] Gate Telegram bot info behind `CALL_INCLUDE_TELEGRAM_BOT` /
  `INCLUDE_TELEGRAM_BOT_CONTEXT` so `{type: "telegram_bot"}` items are added
  only when explicitly enabled (default: `0`, disabled).
- [ ] (Optional, future) Introduce a dedicated flag (e.g.
  `TELEGRAM_ATTACHMENTS_CONTEXT`) to **disable** attachment extraction entirely
  when needed. Currently, attachment extraction is always enabled.

---

## 5. Testing Strategy

### 5.1. Unit Tests (Bot-Level)

- [ ] **New tests** for `_collect_telegram_attachments`:
  - Synthetic `Message` objects with:
    - only photo
    - only document
    - only voice
    - mixed attachments (photo + document + voice)
  - Verify:
    - each attachment produces a context item with expected `type`, `uri`, `name`, `source.direction`.
    - deduplication works.
- [x] **Extended tests** for `build_input_payload_from_reply`:
  - Message replying to text + document.
  - Message replying to photo + current message with voice.
  - Message with attachments only (no text) → `input` empty, but `context` filled.

### 5.2. Integration Tests (Existing Suite)
 
- Update `app/tests/test_telegram_bot_handlers.py`:
  - `FakeCallApi.build_input_payload` already asserts the structure of arguments.
  - Extend expectations to include attachment items in `context`.
- Ensure `/call` and plain-text flows in:
  - private chats
  - group chats with `@Bot` mention
  still behave the same from user perspective.

### 5.3. Regression & Manual Checks

- Run existing suites:
  - `pytest app/tests/test_telegram_bot_handlers.py`
  - smoke tests for Actions/MCP/CLI (should not be affected).
- Manual Telegram recipe:
  1. Send text only → check payload (`context` empty or repo-only).
  2. Reply to message with PDF → see that payload contains `context[*].url` with Telegram file link.
  3. Send photo + voice in a single message → verify two items in `context` with proper `direction="input"`.

---

## 6. Documentation Updates

- [x] **README.md** – section *Telegram Bot / Context Extraction*:
  - Add that attachments (images, documents, video, voice) are converted into `context` entries with Telegram file URLs.
- [x] **tg-user-guide.md / tg-user-guide.ru.md**:
  - Short examples showing how sending a document or photo enriches the agent context.
- [x] Cross-reference `docs/formatting.md` where `{input, context, replay}` contract is already explained, noting that `context` may contain Telegram attachment descriptors.

---

## 7. Rollout & Compatibility

- Backwards-compatible top-level payload shape – no breaking changes for consumers that already parse `{target, replay, input, context}`.
- Existing orchestrators that ignore unknown `context` items will continue to work.
- Attachments simply provide **дополнительный контекст** для агентов; логика агентов может постепенно обновляться, чтобы использовать ссылки на файлы, но не обязана делать это сразу.
  - Short examples showing how sending a document or photo enriches the agent context.
- Cross-reference `docs/formatting.md` where `{input, context, replay}` contract is already explained, noting that `context` may contain Telegram attachment descriptors.

---

## 7. Rollout & Compatibility

- Backwards-compatible top-level payload shape – no breaking changes for consumers that already parse `{target, replay, input, context}`.
- Existing orchestrators that ignore unknown `context` items will continue to work.
- Attachments simply provide **дополнительный контекст** для агентов; логика агентов может постепенно обновляться, чтобы использовать ссылки на файлы, но не обязана делать это сразу.
