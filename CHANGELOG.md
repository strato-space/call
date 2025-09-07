# Changelog

All notable changes to this project will be documented in this file.

## 2025-09-07
- Change: Voice now uses the Call library directly (no subprocess). `voice/src/lib/core.py` calls `call.lib.api.call(...)` and forwards the `echo` flag. When `echo=True`, Voice returns the full dict; otherwise it returns plain text.
- Feature: Add `echo: bool` to `call.lib.api.call()` and `call_async()`; include `echo` in the success payload.
- Change: Centralize Telegram/HTML sanitization and text preparation via `call/app/utils/` (html_sanitizer, telegram_text, telegraph_utils). Snapshot `call/app/call.78e8440.py` updated to use these utilities.
- Fix: Snapshot script-mode fallback for utils/Telegraph imports to avoid relative-import errors when running `python call/app/call.78e8440.py` directly.

## 2025-09-02
- Fix: eliminate runpy warning by lazy-importing `call.app.call` inside entrypoints (`call/app/__init__.py`).
- Change: `post_run_git_push()` now checks for changes first (`git status --porcelain -uno`) and returns silently if none; no stdout logging from this helper (`call/app/call.py`).
