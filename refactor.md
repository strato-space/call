exec_action has no attributes and obtain only one json payload 


{
  "agent": "agent-name",
   context: {}
} 


or 


{
  "prompt": "prompt-name",
   context: {}
} 


code shold extract context and pass it to input and exctract agent or prompt and pass in to similar correponding arg in call_lib


---


telegram bot shoudl use target attr in call - we do not know user setup project, agent or prompt - desigin shoud be meked inside call lib 


--- 
    # Interpret 'target' shortcut before agent resolution using provided filters


make separate function 


---
write commit message into commit..md


---
update docs
---
makke tests
---
improve tests
---




i want refactor  logic for:


- build_and_run_agent 
- run_digest_pipeline - i do not need this fn more, just use build_and_run_agent
- build_and_run_agent
- build_agent_config => build_runnable_instructions_config is shoud return cfg with .instructions attibubre and moodel and other attrs used in build_and_run_agent  like telegram bot config
- call_async move build_runnable_instructions_config call into call_async and prepare all instructiona and args inside call_async
- build_and_run_agent should obtain ready-to-run config with relevant instructions and all other nessesary attrubutes
- make DTO dedinition for cfg 


--- 
- pls use KISS princple in all tasks and make code more compact and ready-to-read

---
update docs, tests, prepare commit message in commit.md, run tests 
update call and voice repo's