i want refactor  logic of

- build_and_run_agent 
- run_digest_pipeline - i do not need this fn more, just use build_and_run_agent
- build_and_run_agent
- build_agent_config => build_runnable_instructions_config is shoud return cfg with .instructions attibubre and moodel and other attrs used in build_and_run_agent  like telegram bot config
- call_async