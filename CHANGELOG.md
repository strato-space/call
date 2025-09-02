# Changelog

All notable changes to this project will be documented in this file.

## 2025-09-02
- Fix: eliminate runpy warning by lazy-importing `call.app.call` inside entrypoints (`call/app/__init__.py`).
- Change: `post_run_git_push()` now checks for changes first (`git status --porcelain -uno`) and returns silently if none; no stdout logging from this helper (`call/app/call.py`).
