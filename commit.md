
feat(call): wildcard selection for prompts/projects/agents; fix bot plain-text parser; add prompts CLI

- telegram_bot: implement `_resolve_agent_and_input()` helper used by `handle_plain_text()`
  - Satisfies `app/tests/test_bot_plain_text.py::test_handle_plain_text_no_nameerror`
  - Conservative parsing rules:
    - In groups: only handle messages starting with `@Name`.
    - In private chats: accept both `Name <input>` and `@Name <input>`.
    - `@` with no name is ignored.

- discovery: enhance `call.lib.discovery.prompts()`
  - Add wildcard `*` support for project and agent filters (full-string, case-insensitive; agent ignores spaces).
  - Keeps existing metadata parsing and natural sorting.

- api: strengthen selection logic in `call.lib.api.call_async()`
  - New behavior for `target`:
    - Prefer prompt matches (id or name), then agent/alias, then project. All allow `*` wildcard.
    - On ambiguity, return `{ ok:false, code: "TOO_MANY_ROWS", options:[...] }`.
  - Wildcard prompts in `prompt` kw:
    - Resolve against repo with current project/agent filters.
    - Return `TOO_MANY_ROWS` or `NO_DATA_FOUND` envelopes when needed.
  - Keep existing `resolve_agent()` flow (already supports wildcard agent via `list()`), so ambiguous agent filters produce `TOO_MANY_ROWS`.

- cli: add `prompts` subcommand (table or JSON)
  - Added `--prompt` filter (supports `*`) alongside `--project` and `--agent`.
  - Examples:
    - `python -m call.cli.main prompts --project UxFab --agent UxCr*`
    - `python -m call.cli.main prompts --project * --agent * --prompt 10*`
  - columns: `id | name | agent | project | state | url`

Notes
- All ambiguity paths provide `options` array with matched entities to help user pick.
- No breaking signature changes to CLI; API `call()`/`call_async()` keep kw-only signature (with optional `target`).

feat(call): wildcard selection for prompts/projects/agents; fix bot plain-text parser; add prompts CLI

- telegram_bot: implement [_resolve_agent_and_input()](cci:1://file:///c:/home/strato-space/call/telegram_bot/bot.py:363:0-393:27) helper used by [handle_plain_text()](cci:1://file:///c:/home/strato-space/call/telegram_bot/bot.py:745:0-770:63)
  - Satisfies [app/tests/test_bot_plain_text.py::test_handle_plain_text_no_nameerror](cci:1://file:///c:/home/strato-space/call/app/tests/test_bot_plain_text.py:9:0-44:47)
  - Conservative parsing rules:
    - In groups: only handle messages starting with `@Name`.
    - In private chats: accept both `Name <input>` and `@Name <input>`.
    - `@` with no name is ignored.

- discovery: enhance [call.lib.discovery.prompts()](cci:1://file:///c:/home/strato-space/voice/src/actions/main.py:125:0-141:75)
  - Add wildcard `*` support for project and agent filters (full-string, case-insensitive; agent ignores spaces).
  - Keeps existing metadata parsing and natural sorting.

- api: strengthen selection logic in [call.lib.api.call_async()](cci:1://file:///c:/home/strato-space/call/lib/api.py:118:0-455:5)
  - New behavior for `target`:
    - Prefer prompt matches (id or name), then agent/alias, then project. All allow `*` wildcard.
    - On ambiguity, return `{ ok:false, code: "TOO_MANY_ROWS", options:[...] }`.
  - Wildcard prompts in `prompt` kw:
    - Resolve against repo with current project/agent filters.
    - Return `TOO_MANY_ROWS` or `NO_DATA_FOUND` envelopes when needed.
  - Keep existing [resolve_agent()](cci:1://file:///c:/home/strato-space/call/lib/api.py:520:0-544:170) flow (already supports wildcard agent via [list()](cci:1://file:///c:/home/strato-space/voice/src/lib/core.py:797:8-812:111)), so ambiguous agent filters produce `TOO_MANY_ROWS`.

- cli: add [prompts](cci:1://file:///c:/home/strato-space/voice/src/actions/main.py:125:0-141:75) subcommand (table or JSON)
  - `python -m call.cli.main prompts --project UxFab --agent UxCr*` now matches wildcard agent and prints:
    - columns: `id | name | agent | project | state | url`

Notes
- All ambiguity paths provide `options` array with matched entities to help user pick.
- No breaking signature changes to CLI; API [call()](cci:1://file:///c:/home/strato-space/voice/src/lib/core.py:108:4-168:30)/[call_async()](cci:1://file:///c:/home/strato-space/call/lib/api.py:118:0-455:5) keep kw-only signature (with optional `target`).