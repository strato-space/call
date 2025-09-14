# Changelog

All notable changes to this project will be documented in this file.

## 2025-09-14
- Feat: Introduced `call/repos.sh` script for managing local clones of primary repositories.
- Feat: Added `repo(url, [dir])` helper that clones when missing and performs `git -C dir pull --ff-only` when the repository exists.
- Change: `ensure_repo` renamed to `repo`; kept a backward-compatible alias `ensure_repo()` that delegates to `repo`.
- Docs: Updated `README.md` with a new section “Repo sync helper (repos.sh)” including usage, behavior, examples for Git Bash/WSL and PowerShell.

## 2025-09-13
- Feat: project_name-only token routing. Added `get_project_token(project_name)` and made `init_bot(project_name=...)` mandatory. Removed fallbacks and any env mutation of `TELEGRAM_TOKEN`.
- Change: case-sensitive agent names (KISS). Removed `to_pascal_case` normalization from bot/CLI/API; names are used exactly as provided.
- Change: Telegram bot no longer validates agent existence; validation/discovery happens centrally in `call.lib.api.call_async()` which returns structured errors for unknown agents.
- Feat: project-scoped listing. `list(..., project_name=...)` scopes to a single directory under the prompt repo. No default cross-project merge; removed `grouped` output from library and CLI. CLI `list` now supports `--project-name`.
- Docs: updated README to reflect project_name-only tokens, KISS naming, bot validation policy, and project-scoped listing; replaced `prompt/agents` → `prompt/UxFab` in discovery/order examples.
- Config: updated `.env` guidance to use `TELEGRAM_TOKEN.<ProjectName>` keys (e.g., `TELEGRAM_TOKEN.StratoSpaceAi`, `TELEGRAM_TOKEN.AgentFab`).

## 2025-09-12
- Fix: Telegram chat routing — preserve caller-provided `chat_id`/`thread_id` passed via `call.lib.api.call(...)`. The pipeline no longer overwrites targets with Agent YAML or `.env` defaults; explicit values are propagated through final notifications to avoid global-state races. (files: `call/app/call.py`, `call/lib/api.py`)
- Refactor: Centralize Telegram HTML prep per Bot API — introduce `sanitize_telegram_html()`, `truncate_telegram_html_safe()`, and `prepare_telegram_html()` in `call/app/utils/html_sanitizer.py`. Route `telegram_prepare_html()` and HTML truncation through the centralized implementation. (files: `call/app/utils/html_sanitizer.py`, `call/app/utils/telegram_text.py`)
- Change: Use only the documented HTML tags/attrs for Telegram: `a`, `b/strong`, `i/em`, `u/ins`, `s/strike/del`, `code`, `pre`, `blockquote`, `tg-spoiler`, `tg-emoji`; preserve `a[href]`, `blockquote[expandable]`, `tg-emoji[emoji-id]`, and `code[class~=language-*]`. Normalize `h1–h6` to `<b>…</b>` + newline; replace `<hr>` with two newlines; flatten lists to text. (file: `call/app/utils/html_sanitizer.py`)
 - Change: `send_digest_notification()` signature simplified — removed unused `url` parameter. Internal publishing now uses a local `local_url` variable and macro `{{digest_url}}` in `buttons` resolves against it. (file: `call/app/call.py`)
 - Fix: Avoid sending empty Telegram messages — empty/whitespace-only `text` is normalized to `None` so the function falls back to the banner with input echo. (file: `call/app/call.py`)
 - Debug: Add `[DEBUG] send_digest_notification ...` prints of argument summary and resulting publish URL. (file: `call/app/call.py`)
 - Tests: Add `test_send_digest_notification.py` covering empty-text fallback, long-text publish link, and `{{digest_url}}` macro in buttons. (file: `call/app/tests/test_send_digest_notification.py`)

## 2025-09-07
- Change: Voice now uses the Call library directly (no subprocess). `voice/src/lib/core.py` calls `call.lib.api.call(...)` and forwards the `echo` flag. When `echo=True`, Voice returns the full dict; otherwise it returns plain text.
- Feature: Add `echo: bool` to `call.lib.api.call()` and `call_async()`; include `echo` in the success payload.
- Change: Centralize Telegram/HTML sanitization and text preparation via `call/app/utils/` (html_sanitizer, telegram_text, telegraph_utils). Snapshot `call/app/call.78e8440.py` updated to use these utilities.
- Fix: Snapshot script-mode fallback for utils/Telegraph imports to avoid relative-import errors when running `python call/app/call.78e8440.py` directly.

## 2025-09-02
- Fix: eliminate runpy warning by lazy-importing `call.app.call` inside entrypoints (`call/app/__init__.py`).
- Change: `post_run_git_push()` now checks for changes first (`git status --porcelain -uno`) and returns silently if none; no stdout logging from this helper (`call/app/call.py`).
