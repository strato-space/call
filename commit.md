feat(call): refine target interpretation; introduce RunnableConfig DTO; wire builder into pipeline

- interpret_target():
  - Precedence is prompt > project > agent.
  - Removed the “direct directory scan” fallback (no filesystem probing for project names).
  - Added a conservative fallback: simple token (without ‘*’) is treated as project only if prompts/agents don’t match.
- New DTO:
  - dataclass RunnableConfig(name, project, prompt_override, merge, agent_yaml_path, base_dir, instructions, model, vs_list, attributes)
- New builder:
  - build_runnable_instructions_config(project, agent, prompt, merge) -> (cfg, err)
  - Uses resolve_agent() and best-effort YAML parsing to populate DTO fields.
- Pipeline wiring:
  - call_async() constructs RunnableConfig and forwards DTO fields to app.call.build_and_run_agent
  - No behavior change for callers; DTO is a non-breaking step.
- Tests:
  - Target interpretation tests ensure exact project resolution without directory fallback.
  - Prepared to add builder tests (RunnableConfig shape and error conditions).

Files:
- call/lib/api.py
  - interpret_target(): removed repo directory fallback; ensure precedence prompt > project > agent
  - + dataclass RunnableConfig
  - + build_runnable_instructions_config()
  - call_async(): now builds DTO and forwards fields to build_and_run_agent

Status:
- All call tests green locally (54 passed)