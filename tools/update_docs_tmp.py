from pathlib import Path

path = Path(r"c:/home/strato-space/call/README.md")
text = path.read_text(encoding="utf-8")

old_cli = "```powershell\n# call with raw input\npython -m call.cli.main call --target AgentFab --input \"as is text\"\n\n# call with parsed input (Telegram-identical payload)\npython -m call.cli.main call --target AgentFab --parse-input \"@3-OnlineChunkSummarization\" --echo --format yaml\n\n# exec with content items\npython -m call.cli.main exec --project UxFab --agent DialogPostAnalysis \\\n  --content-item \"https://docs.google.com/document/d/FILE_ID/edit\" \\\n  --content-item '{\"type\":\"text\",\"text\":\"Hello\"}' --output-type html\n\n# exec with multiple selectors (falls back to explicit call path)\npython -m call.cli.main exec --project UxFab --agent DialogPostAnalysis --target 33-Questioning --echo\n\n# exec using wildcards (auto-resolved into context items)\npython -m call.cli.main exec --target AgentFab --parse-input \"@50-* @3-*\" --echo\n```"
new_cli = old_cli[:-3] + "\n\n# exec with default target resolution\npython -m call.cli.main exec --target Vasil3\n```"
if old_cli not in text:
    raise SystemExit("cli block not found")
text = text.replace(old_cli, new_cli, 1)

if "### Call Actions API (curl examples)" not in text:
    marker = "### Parsed vs raw input (New)"
    if marker not in text:
        raise SystemExit("marker missing for actions block")
    block = "### Call Actions API (curl examples)\n\n- **List prompts (HTTPS via nginx)**\n\n  ```bash\n  curl -v \"https://call-actions.stratospace.fun/prompts\" \\\n    -H \"Authorization: Bearer 123123142356365864895789678967\" \\\n    | jq\n  ```\n\n- **List prompts filtered by project**\n\n  ```bash\n  curl -v \"https://call-actions.stratospace.fun/prompts?project=AgentFab\" \\\n    -H \"Authorization: Bearer 123123142356365864895789678967\" \\\n    | jq\n  ```\n\n- **`GET /prompts` parameters**\n\n  - `project`: optional exact match (supports empty string for all)\n  - `agent`: optional exact match (supports empty string for all)\n  - `prompt`: optional identifier or name (supports `*` wildcard)\n  - `state`: optional `ready`, `draft`, or empty for both\n\n- **Selection tip**\n\n  When you are unsure whether an identifier refers to a project, agent, or prompt, send it via the `target` parameter. The API resolves the name against all supported scopes, so a single call works even if the type is unknown.\n\n- **Execute an agent with JSON payload**\n\n  ```bash\n  curl -v \"https://call-actions.stratospace.fun/exec\" \\\n    -H \"Authorization: Bearer 123123142356365864895789678967\" \\\n    -H \"Content-Type: application/json\" \\\n    --data '{\n      \"target\": \"Vasil3\"\n      }'\n  ```\n\n- **Exec payload with mixed context sources**\n\n  ```bash\n  curl -v \"https://call-actions.stratospace.fun/exec\" \\\n    -H \"Authorization: Bearer 123123142356365864895789678967\" \\\n    -H \"Content-Type: application/json\" \\\n    --data '{\n      \"prompt\": \"49-BusinessAnalyticAgent\",\n      \"context\": [\n        {\n          \"type\": \"text\",\n          \"text\": \"Заголовок с ключевым предложением.\nКраткое описание преимуществ сотрудничества.\nПризыв к действию с кнопкой \\\"Стать агентом\\\" / \\\"Seja um agente\\\" / \\\"Become an agent\\\".\",\n          \"source\": {\n            \"type\": \"file\",\n            \"file_id\": \"13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-\",\n            \"name\": \"11.09.24_Мобильная касса Лендинг #2.docx\"\n          }\n        },\n        {\n          \"type\": \"text\",\n          \"text\": \"В чем разница между облачной платной версией и нашей? Облачная платная версия позволяет регистрировать цепочки прямо на сайте LongChain и работать с ними. Наша версия из коробки такого не позволяет.\",\n          \"source\": {\n            \"type\": \"session\",\n            \"_id\": \"68afe646ef46aed531a8ecc5\",\n            \"name\": \"2025-08-28 08:16 OpenCanvas hacks; diff; cloud vs local; integration with langgraph 2\"\n          }\n        },\n        {\n          \"type\": \"session\",\n          \"_id\": \"68c7ab4cab67ffbd365062f1\"\n        },\n        {\n          \"type\": \"file\",\n          \"file_id\": \"13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-\"\n        }\n      ]\n    }'\n  ```\n"
    text = text.replace(marker, block + "\n\n" + marker, 1)

if "### Google service account key" not in text:
    marker_sa = "(e.g., `error_code: 502`, `code: UPSTREAM_CONNECT_ERROR|PIPELINE_ERROR`) to avoid printing tracebacks to users.\n"
    idx = text.find(marker_sa)
    if idx == -1:
        raise SystemExit("service account insertion point missing")
    idx += len(marker_sa)
    addition = "\n### Google service account key\n\n- Actions/CLI flows that call Google APIs expect the service account JSON at `call/wallet/service-account-key.json`.\n- The file holds the full Google Cloud credential (`type: service_account`), so treat it as sensitive secret material.\n\n"
    text = text[:idx] + addition + text[idx:]

path.write_text(text, encoding="utf-8")
