"""
Argument parsing helpers for Telegram bot commands (prompt listing).

This module is dependency-light to enable straightforward unit testing.
"""

from __future__ import annotations


def parse_prompts_filters(
    text: str, *, command: str, default_project: str | None
) -> tuple[str | None, str | None, str | None, str | None]:
    """Parse command text into (project, agent, prompt, target) with AND semantics.

    Accepted forms (order-insensitive after the command token):
    - --project X, --agent X, --prompt X, --target X
    - project=X, agent=X, prompt=X, target=X
    - @Agent (agent shorthand)
    - Bare token as project when none set; extra bare token as prompt
    """
    try:
        s = (text or "").strip()
        tokens = s.split()
        if tokens and tokens[0].startswith(command):
            tokens = tokens[1:]
        project = None
        agent = None
        prompt = None
        target = None
        it = iter(tokens)
        for tok in it:
            t = tok.strip()
            if not t:
                continue
            low = t.lower()
            if low.startswith("--project"):
                project = next(it, "").strip() or project
                continue
            if low.startswith("--agent"):
                agent = next(it, "").strip().lstrip("@") or agent
                continue
            if low.startswith("--prompt"):
                prompt = next(it, "").strip() or prompt
                continue
            if low.startswith("--target"):
                target = next(it, "").strip() or target
                continue
            # Ignore other unknown long flags (like --state) here; parse_prompts_and_state handles them
            if low.startswith("--"):
                _ = next(it, "")  # consume potential value if present; safe to ignore
                continue
            if "=" in t:
                k, v = t.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k == "project":
                    project = v or project
                elif k == "agent":
                    agent = v.lstrip("@") or agent
                elif k == "prompt":
                    prompt = v or prompt
                elif k == "target":
                    target = v or target
                continue
            if t.startswith("@"):
                agent = t[1:]
            elif project is None:
                project = t
            else:
                prompt = prompt or t
        if not project:
            project = default_project or None
        return project, agent, prompt, target
    except Exception:
        # Fallback to safest defaults
        return (default_project or None), None, None, None


def parse_prompts_and_state(
    text: str, *, command: str, default_project: str | None
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Extended parser that also recognizes state (ready|draft) via --state or state=.

    Returns (project, agent, prompt, target, state).
    """
    project, agent, prompt, target = parse_prompts_filters(
        text, command=command, default_project=default_project
    )
    # Lightweight pass to capture state options
    try:
        s = (text or "").strip()
        tokens = s.split()
        if tokens and tokens[0].startswith(command):
            tokens = tokens[1:]
        state = None
        it = iter(tokens)
        for tok in it:
            t = tok.strip()
            if not t:
                continue
            low = t.lower()
            if low.startswith("--state"):
                state = next(it, "").strip() or state
                continue
            if "=" in t:
                k, v = t.split("=", 1)
                if k.strip().lower() == "state":
                    state = v.strip() or state
        return project, agent, prompt, target, state
    except Exception:
        return project, agent, prompt, target, None
