# Plan to enable agentless/global prompts in build_runnable_instructions_config

1. Update prompt-selection fallback in call/lib/api.py::build_runnable_instructions_config

- Relax the filter that currently discards repo rows whose project or agent fields are empty, so that prompts without those attributes can still populate prompt_row.

- When such a prompt is chosen, ensure resolved_project/resolved_agent are None.

- Verify the computed RunnableConfig continues to prefer prompt metadata (goal/role/model) once a prompt row is accepted.

- Make _load(...) more forgiving: if neither `id` nor `target` is available use filename whiout .md suffix as `id`

2. Adjust call/lib/repo_db.py::find_prompts matching semantics

- Allow prompts whose stored project column is empty to match a non-empty project filter so that reusable prompts surface even when a caller scopes by project.

- When a specific project filter is supplied, run the existing query first; if it yields no rows, retry (or append) a query that targets prompts where project is empty so “global” prompts remain discoverable.

- Keep ordering so explicit matches win; only return the global rows when no project-scoped match exists to avoid triggering TooManyRows.

- Update/extend unit coverage (e.g., new assertion in call/lib/tests/test_repo_reload_cards.py or a dedicated test_repo_db module) that exercises find_prompts(project=\"SomeProj\", prompt=\"GlobalPrompt\") and confirms the empty-project row is returned.

- Keep existing behavior for agent/prompt filters and retain the guardrails that weed out malformed rows (missing IDs/paths).

Double-check repo loader behavior

Confirm _scan_prompt_repo already persists blank project/agent fields as empty strings; add a regression test if necessary to guarantee those rows are present in repo.db.

4.Extend tests

Augment app/tests/test_builder_config.py (or add a new focused test) with a prompt card lacking both project and agent metadata to lock in the desired behavior for both the API builder and the find_prompts helper.

Cover the scenario where a project filter is provided but the prompt row’s project is empty, ensuring the fallback still succeeds.

5.Validation

- Re-run the targeted pytest module (pytest call/app/tests/test_builder_config.py -q) and, if practical, a quick smoke run of other relevant suites touching repo DB lookup logic.

- Run `pytest call` with full test stute as final step
