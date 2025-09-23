# Call и Prompt Repo. AgentFab и Agents

Вызов AgentFab и созданных Агентов - Руководство пользователя Telegram

## /call — @AgentFab или @Agent

- `/call @AgentFab @31-*` — обработать промпты, совпадающие с шаблоном `31-*`, через AgentFab.
- `/call @AiNewsAggr` — вызвать готового агента `AiNewsAggr`
- `/call @AiNewsAggr Новости Apple` — вызвать готового агента `AiNewsAggr` с входом `Новости Apple`

## Списки промптов в Prompt Repo

- `/prompts_draft`  — список промптов в статусе draft со ссылками на githib 
- `/prompts_ready`  — список промптов в статусе ready со ссылками на githib
- [Prompt Repo: strato-space/prompt](https://github.com/strato-space/prompt)

## Обновления индекса промтов и контекнсых боксов в боте

- Перестроить индекс: `/reload`

## Как создать новый промпт (через AgentFab)

- Пример:
  - `/call @AgentFab Создай промпт goal: подготовка повестки daily id:301-Daily`
- AgentFab интерпретирует задачу, создаёт карточку промпта в Prompt Repo и добавляет её в индекс после `/reload`.

## Как создать контекстный бокс (Content Box)

- Сейчас контекстные боксы создаются созданием папки в Prompt Repository:
  - [AgentFab/ContentBoxes](https://github.com/strato-space/prompt/tree/main/AgentFab/ContentBoxes)
- Создайте новую подпапку с понятным именем и поместите внутрь необходимые материалы (md, txt, csv и т.д.). 
- После обновления github выполните `/reload`.

## Как использовать контексный бокс

- в input в любом месте текста указать context-box: @BusinessAnalytycAgent 

## Как изменить состав или инстукции AgentFab

- [Карточка AgentFab в Prompt Repo](https://github.com/strato-space/prompt/blob/main/AgentFab/project.md)
- [Карточка StratoFormatter](https://github.com/strato-space/prompt/blob/main/AgentFab/StratoFormater/agent.md)
- [Конексный бокс BusinessAnalyticAgent](https://github.com/strato-space/prompt/tree/main/AgentFab/ContentBoxes/DialogOnlineAnalysis/content)
- [Промпт 49-BusinessAnalyticAgent](https://github.com/strato-space/prompt/blob/main/draft/49-BusinessAnalyticAgent.md)
- [Промпт 50-Discoveryagent.md](https://github.com/strato-space/prompt/blob/main/draft/50-Discoveryagent.md)
- [Off: Выключенный SelfReflection](https://github.com/strato-space/prompt/blob/main/AgentFab/SelfReflection/agent.md)


## Как добавить новый пропмп в цепочку в AgentFab

- Отредактировать список prompt в разделе METADATA - см. [Карточка AgentFab в Prompt Repo](https://github.com/strato-space/prompt/blob/main/AgentFab/AgentFab.md)

## Особенности

- Имена чувствительны к регистру. Используйте точные названия `Project`, `Agent`, `Prompt`, как в Prompt Repo.
- Бот работае в личных сообщениях и в группах с указанием команды /call.
- В процессе работы @AgentFab бот выводит помежуточные шаги, вызовы MCP Filesystem, MCP Sequential Thinking, MCP Voicebot, вызовы дочених агентов в ту же группу где вызывана команда /call
- Бот сохранияет истрию переписки даже при перезапуске, и позволят строить длительные диалоги с агентами и промптами. 


## Быстрый старт

- В личке: отправьте текст или `@Target <ввод>`
- В группах: упомяните бота и укажите `Target` или `@Target` в том же сообщении
- Список промптов: `/prompts`, `/prompts_ready`, `/prompts_draft` (с фильтрами)
- Перестроить индекс: `/reload`

## Бот и назначение

- Бот: [@StratoSpaceAiBot](https://t.me/StratoSpaceAiBot)
- Предназначен для запуска `AgentFab`, готовых агентов и промптов из Prompt Repository.


## Команды бота

- `/reload`
  - Пересканировать репозитории из `.env` (например, `repos=agent,prompt`) и пересобрать SQLite‑индекс.

- `/prompts_ready`, `/prompts_draft`
  - Выводят список промптов из индекса с гибкими фильтрами.
  - Варианты ready/draft заранее применяют `state=ready` / `state=draft`.

### Дополниельные фильтры (доступны во всех трёх командах)

- `--project <ProjectName>`
- `--agent <AgentName>`
- `--prompt <PromptName>`
- `--target <plain|pattern>`
- `--state ready|draft`
- Поддерживаются подстановки `*` во всех фильтрах.
- Принимаются формы key=value и сокращение `@Agent`.
- Фильтры объединяются по правилу AND.

Примеры:

- `/prompts --project UxFab --agent DialogPostAnalysis --state ready`
- `/prompts_ready --project * --prompt 10* --target r:*`
- `/prompts_draft --project AgentFab --prompt 3*-*`

# Документация разрабочка 

## Как парсятся сообщения

- __Личные сообщения__
  - Простой текст (без @) → запускается как input‑only (аналог `/call <input>`)
  - `@Target <input>` → выполняется, если Target существует (приоритет: prompt > agent > project)
  - Одиночное `@ <input>` → трактуется как input‑only (без target)
  - Начальный `@BotName` допускается и отбрасывается

- __Групповые чаты__
  - Обрабатываются только сообщения с явным @‑упоминанием
  - `@Target <input>` выполняется только при валидном Target
  - `@BotName Target <input>` ведёт себя так же; если `Target` невалиден → input‑only
  - `@ <input>` → input‑only

## Резолвинг цели и приоритеты

- Если указан `target`, приоритет такой:
  1) prompt
  2) точное совпадение project
  3) agent
  4) нечёткое/шаблонное совпадение project
- Поддерживаемые формы:
  - Непрефиксированная с подстановками: `Ux*` (по попытке: prompt → agent → project)
  - Похоже на путь: `path:project/agent/prompt`
    - `path:UxFab/DialogPostAnalysis/33-*`
    - `path:UxFab/DialogPostAnalysis`
    - `path:UxFab`

## Шаблоны (wildcards) в тексте

- Токены вида `@31-*` или `32-*` резолвятся через БД репозитория.
- Первая найденная карточка по каждому токену добавляется в контекст как файловая ссылка
  `{ type: "file", name, path, mutable: true }`.
- Несколько токенов поддерживаются; дубликаты удаляются.
- Ведущий `@` и суффиксы `.md/.markdown` автоматически убираются.

Примеры:

- `@31-*` → добавит один контекстный файл
- `31-* 32-*` → добавит два контекстных файла

## Соответствие MCP (mcp-voicebot)

- Инструменты:
  - `agents(query?, include_aliases?, project_name?)`
  - `prompts(project?, agent?, prompt?, state?)`
  - `exec(payload: object)`
  - `reload()`
- Маппинг:
  - `/reload` → `reload()`
  - `/prompts*` → `prompts()` с переданными фильтрами
  - Сообщение с `@Target` и текстом → `exec(payload)` с полями `target/input/context`

### Контракт exec payload (используется в Actions/MCP)

```json
{
  "project": "?",
  "agent": "?",
  "prompt": "?",
  "target": "?",
  "context": [],
  "echo": false,
  "session_id": "chat[:thread]"
}
```

- Ровно один из `project|agent|prompt|target` должен быть указан.
- В Telegram чаще всего используется `target`; остальное формируется парсером.

## ToDo примеры curl команд 

- todo

## Todo Actions

## Todo MCP

## Сессии и маршрутизация

- Формат `session_id`: `chat` или `chat:thread` (например, `AgentName:-100123:10`).
- Если `session_id` передан, он приоритетный; библиотека сама разберёт `chat_id/thread_id`.
- Иначе используются `chat_id/thread_id` из обновления или окружения.

## Практические примеры

- Списки:
  - `/prompts --project AgentFab --format text`
  - `/prompts_ready --project * --agent * --prompt 3*-*`
- Запуски:
  - `@AgentFab "Сделай обзор по @11-ExtractUserPain"`
  - `@DialogPostAnalysis "Проанализируй https://docs.google.com/document/d/FILE_ID/edit"`
  - `@path:UxFab/DialogPostAnalysis/33-* "Выполни пайплайн 33-*"`
- Группы:
  - `@BotName AgentFab "Собери дайджест по 31-* 32-*"`
  - `@BotName @AiNewsAggr "Сделай краткую сводку"`

## Полезные переменные окружения

- `CALL_DEBUG=1` — подробные логи (в т.ч. решения парсинга с префиксом [bot])
- `CALL_LOG_JSON=1` — JSON‑логи
- `CALL_LOG_FILE=logs/app.log` — запись логов в файл

## Диагностика

- Ошибки выбора:
  - `TOO_MANY_ROWS` — неоднозначный выбор (вернутся варианты)
  - `NO_DATA_FOUND` — ничего не найдено (уточните регистр/фильтры)
- Ограничения апстрима:
  - `REQUEST_FORBIDDEN` (403) — ограничения провайдера/трассинга
- Включите `CALL_DEBUG=1` и смотрите строки `[bot]`, чтобы понять, как бот распознал сообщение.

## CLI интерфейс

```bash
py -m call.cli.main --
usage: main.py [-h] [--json-logs] [--debug]
               {agents,list,projects,call,prompts,reload,scan,exec,clear-session} ...
main.py: error: the following arguments are required: cmd
(.venv) PS C:\home\strato-space> py -m call.cli.main --help
usage: main.py [-h] [--json-logs] [--debug]
               {agents,list,projects,call,prompts,reload,scan,exec,clear-session} ...

call — CLI for listing and invoking agents (keyword-only API)

positional arguments:
  {agents,list,projects,call,prompts,reload,scan,exec,clear-session}
    agents (list, projects)
                        List projects and agents (hierarchical)
    call                Call an agent with input text
    prompts             List prompts (flat)
    reload              Scan repositories and rebuild repo.db
    scan                Alias of reload (will be removed)
    exec                Execute via payload (best for content buckets)
    clear-session       Clear conversation session(s) for a chat/thread from SQLite

options:
  -h, --help            show this help message and exit
  --json-logs           Emit JSON logs (overrides CALL_LOG_JSON)
  --debug               Force DEBUG logging (overrides CALL_DEBUG)
```

```bash
py -m call.cli.main call --target AgentFab --parse-input "31-OnlineQuestionsBabook 32-InterviewSummary" --echo
{
  "target": "AgentFab",
  "input": "31-OnlineQuestionsBabook 32-InterviewSummary",
  "context": [
    {
      "type": "file",
      "name": "31-OnlineQuestionsBabook",
      "path": "prompt/draft/31-OnlineQuestionsBabook.md",
      "mutable": true
    },
    {
      "type": "file",
      "name": "32-InterviewSummary",
      "path": "prompt/draft/32-InterviewSummary.md",
      "mutable": true
    }
  ]
}
```

```
py -m call.cli.main exec --target AgentFab --parse-input "31-OnlineQuestionsBabook 32-InterviewSummary" --echo
{
  "target": "AgentFab",
  "input": "31-OnlineQuestionsBabook 32-InterviewSummary",
  "context": [
    {
      "type": "file",
      "name": "31-OnlineQuestionsBabook",
      "path": "prompt/draft/31-OnlineQuestionsBabook.md",
      "mutable": true
    },
    {
      "type": "file",
      "name": "32-InterviewSummary",
      "path": "prompt/draft/32-InterviewSummary.md",
      "mutable": true
    }
  ]
}
```

```bash
py -m call.cli.main prompts
id                                                                                   | name        
                                                                         | agent                 | 
project       | state | url

-------------------------------------------------------------------------------------+--------------------------------------------------------------------------------------+-----------------------+---------------+-------+------------------------------------------------------------------------------------------------
{'prompt': 'готовить протоколы по транскрибациям StratoVoice согласно structure.md'} | {'prompt': 'готовить протоколы по транскрибациям StratoVoice согласно structure.md'} | StratoSummarizer2     | 
FanFab        |       | https://github.com/strato-space/agent/blob/master/FanFab/StratoSummarizer2/agent.md
TempBadPrompt                                                                        | TempBadPrompt                                                                        |                       | 
              | ready | https://github.com/strato-space/prompt/blob/master/ready/TempBadPrompt.md  

1-Categorization                                                                     | 1-Categorization                                                                     | DialogChunk           | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/1-Categorization.md
10-HighlightOptimizationProposals                                                    | 10-HighlightOptimizationProposals                                                    | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/10-HighlightOptimizationProposals.md
10-SelfReflection                                                                    | 10-SelfReflection                                                                    | SelfReflection        | 
AgentFab      | draft | https://github.com/strato-space/prompt/blob/master/draft/10-SelfReflection.md
11-ExtractUserPain                                                                   | 11-ExtractUserPain                                                                   | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/11-ExtractUserPain.md
12-ExtractProblems                                                                   | 12-ExtractProblems                                                                   | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/12-ExtractProblems.md
13-GroupUserQuotes                                                                   | 13-GroupUserQuotes                                                                   | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/13-GroupUserQuotes.md
130-QAcriteriaDefinition                                                             | 130-QAcriteriaDefinition                                                             | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/130-QAcriteriaDefinition.md
131-DocumentationReview                                                              | 131-DocumentationReview                                                              | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/131-DocumentationReview.md
132-TestScenarioReview                                                               | 132-TestScenarioReview                                                               | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/132-TestScenarioReview.md
133-UXIssuesDetection                                                                | 133-UXIssuesDetection                                                                | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/133-UXIssuesDetection.md
134-QualityImprovementSuggestions                                                    | 134-QualityImprovementSuggestions                                                    | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/134-QualityImprovementSuggestions.md
135-RegulatoryComplianceCheck                                                        | 135-RegulatoryComplianceCheck                                                        | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/135-RegulatoryComplianceCheck.md
136-UserFeedbackAnalysis                                                             | 136-UserFeedbackAnalysis                                                             | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/136-UserFeedbackAnalysis.md
137-QAreportGeneration                                                               | 137-QAreportGeneration                                                               | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/137-QAreportGeneration.md
14-Stakeholderneeds                                                                  | 14-Stakeholderneeds                                                                  | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/14-Stakeholderneeds.md
15-Featurerequest                                                                    | 15-Featurerequest                                                                    | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/15-Featurerequest.md
16-Explicitreqs                                                                      | 16-Explicitreqs                                                                      | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/16-Explicitreqs.md
17-Implicitreqs                                                                      | 17-Implicitreqs                                                                      | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/17-Implicitreqs.md
18-Reqclassification                                                                 | 18-Reqclassification                                                                 | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/18-Reqclassification.md
19-Requirementssummary                                                               | 19-Requirementssummary                                                               | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/19-Requirementssummary.md
2-SplitByTopics                                                                      | 2-SplitByTopics                                                                      | DialogTopics          | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/2-SplitByTopics.md
20-Processextractor                                                                  | 20-Processextractor                                                                  | BusinessAnalyticAgent | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/20-Processextractor.md
202-ContextReminder                                                                  | 202-ContextReminder                                                                  |                       | 
              | draft | https://github.com/strato-space/prompt/blob/master/draft/202-ContextReminder.md
203-BacklogMaster                                                                    | 203-BacklogMaster                                                                    |                       | 
              | draft | https://github.com/strato-space/prompt/blob/master/draft/203-BacklogMaster.md
21-Roleextractor                                                                     | 21-Roleextractor                                                                     | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/21-Roleextractor.md
22-Systemextractor                                                                   | 22-Systemextractor                                                                   | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/22-Systemextractor.md
23-Artifactextractor                                                                 | 23-Artifactextractor                                                                 | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/23-Artifactextractor.md
24-Rolemodelindex                                                                    | 24-Rolemodelindex                                                                    | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/24-Rolemodelindex.md
25-SpecificGlossary                                                                  | 25-SpecificGlossary                                                                  | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/25-SpecificGlossary.md
3-OnlineChunkSummarization                                                           | 3-OnlineChunkSummarization                                                           | DialogOnlineAnalysis  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/3-OnlineChunkSummarization.md
30-OnlineChunkSummarization                                                          | 30-OnlineChunkSummarization                                                          | DialogOnlineAnalysis  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/30-OnlineChunkSummarization.md
31-OnlineQuestionsBabook                                                             | 31-OnlineQuestionsBabook                                                             | DialogOnlineAnalysis  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/31-OnlineQuestionsBabook.md
32-InterviewSummary                                                                  | 32-InterviewSummary                                                                  | DialogSummary         | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/32-InterviewSummary.md
33-Questioning                                                                       | 33-Questioning                                                                       | DialogPostAnalysis    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/33-Questioning.md 

34-CollectUnresolvedEscalationItems                                                  | 34-CollectUnresolvedEscalationItems                                                  | DialogPostAnalysis    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/34-CollectUnresolvedEscalationItems.md
35-Coremessage                                                                       | 35-Coremessage                                                                       | PresentMaker          | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/35-Coremessage.md 

36-Presentarchitect                                                                  | 36-Presentarchitect                                                                  | PresentMaker          | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/36-Presentarchitect.md
37-ContentScen                                                                       | 37-ContentScen                                                                       | PresentMaker          | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/37-ContentScen.md 

38-Visualslide                                                                       | 38-Visualslide                                                                       | PresentMaker          | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/38-Visualslide.md 

39-Controliteration                                                                  | 39-Controliteration                                                                  | PresentMaker          | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/39-Controliteration.md
4-OnlineQuestionsBabook                                                              | 4-OnlineQuestionsBabook                                                              | DialogOnlineAnalysis  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/4-OnlineQuestionsBabook.md
40-ChunkStakePrior                                                                   | 40-ChunkStakePrior                                                                   | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/40-ChunkStakePrior.md
42-RolesetBuilder                                                                    | 42-RolesetBuilder                                                                    | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/42-RolesetBuilder.md
43-DoccompletenessChecker                                                            | 43-DoccompletenessChecker                                                            | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/43-DoccompletenessChecker.md
44-ResearchgapAnalyzer                                                               | 44-ResearchgapAnalyzer                                                               | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/44-ResearchgapAnalyzer.md
49-BusinessAnalyticAgent                                                           | 49-BusinessAnalyticAgent                                                           | DialogOnlineAnalysis  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/49-BusinessagAntanalytic.md
5-InterviewSummary                                                                   | 5-InterviewSummary                                                                   | DialogSummary         | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/5-InterviewSummary.md
50-DiscoveryAgent                                                                    | 50-DiscoveryAgent                                                                    | DiscoveryAgent        | 
AgentFab      | draft | https://github.com/strato-space/prompt/blob/master/draft/50-Discoveryagent.md
6-Questioning                                                                        | 6-Questioning                                                                        | DialogPostAnalysis    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/6-Questioning.md  

7-CollectUnresolvedEscalationItems                                                   | 7-CollectUnresolvedEscalationItems                                                   | DialogPostAnalysis    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/7-CollectUnresolvedEscalationItems.md
8-ListOfInterview                                                                    | 8-ListOfInterview                                                                    | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/8-ListOfInterview.md
9-ExtractInsights                                                                    | 9-ExtractInsights                                                                    | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/9-ExtractInsights.md
AgentFab_Context_Rebuilder                                                           | AgentFab_Context_Rebuilder                                                           |                       | 
              | draft | https://github.com/strato-space/prompt/blob/master/draft/AgentFab_Context_Rebuilder.md
AIHaiku                                                                              | AIHaiku     
                                                                         | HaikuMaster           | 
CreativityLab | draft | https://github.com/strato-space/prompt/blob/master/draft/AIHaiku.md        

ArtifactAcceptanceChecklist                                                          | ArtifactAcceptanceChecklist                                                          | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ArtifactAcceptanceChecklist.md
ArtifactStatusTracer                                                                 | ArtifactStatusTracer                                                                 | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ArtifactStatusTracer.md
dynamic_reasoning_methods                                                            | dynamic_reasoning_methods                                                            | BusinessAnalyticAgent | 
AgentFab      | draft | https://github.com/strato-space/prompt/blob/master/draft/dynamic_reasoning_methods.md
e2eMetricsCollector                                                                  | e2eMetricsCollector                                                                  | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/e2eMetricsCollector.md
ErrorMessages                                                                        | ErrorMessages                                                                        | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ErrorMessages.md  

FeedbackBot                                                                          | FeedbackBot 
                                                                         | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/FeedbackBot.md    

FeedbackLoop                                                                         | FeedbackLoop                                                                         | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/FeedbackLoop.md   

FeedbackLoopIntegrator                                                               | FeedbackLoopIntegrator                                                               | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/FeedbackLoopIntegrator.md
FeedbackReactor                                                                      | FeedbackReactor                                                                      | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/FeedbackReactor.md
FeedbackToReqUpdater                                                                 | FeedbackToReqUpdater                                                                 | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/FeedbackToReqUpdater.md
GapToTaskGenerator                                                                   | GapToTaskGenerator                                                                   | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/GapToTaskGenerator.md
ImpactValidator                                                                      | ImpactValidator                                                                      | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ImpactValidator.md
InsightToActionTracker                                                               | InsightToActionTracker                                                               | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/InsightToActionTracker.md
LessonsLearned                                                                       | LessonsLearned                                                                       | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/LessonsLearned.md 

MasterFlow                                                                           | MasterFlow  
                                                                         | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/MasterFlow.md     

NotificationHub                                                                      | NotificationHub                                                                      | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/NotificationHub.md
PainTrendAnalyzer                                                                    | PainTrendAnalyzer                                                                    | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/PainTrendAnalyzer.md
PatternsAndAntiPatterns                                                              | PatternsAndAntiPatterns                                                              | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/PatternsAndAntiPatterns.md
QAFeedbackToTask                                                                     | QAFeedbackToTask                                                                     | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/QAFeedbackToTask.md
ReqActionNotifier                                                                    | ReqActionNotifier                                                                    | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ReqActionNotifier.md
ReqCrossTraceabilityMatrix                                                           | ReqCrossTraceabilityMatrix                                                           | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ReqCrossTraceabilityMatrix.md
ReqErrorTrendAnalyzer                                                                | ReqErrorTrendAnalyzer                                                                | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ReqErrorTrendAnalyzer.md
ReqSelfReviewLoop                                                                    | ReqSelfReviewLoop                                                                    | UxResearcherReq       | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/ReqSelfReviewLoop.md
RequirementTraceabilityQA                                                            | RequirementTraceabilityQA                                                            | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/RequirementTraceabilityQA.md
RiskAndResponsibilityMap                                                             | RiskAndResponsibilityMap                                                             | UxManager             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/RiskAndResponsibilityMap.md
RoleCrossTraceLinker                                                                 | RoleCrossTraceLinker                                                                 | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/RoleCrossTraceLinker.md
RoleDynamicVisualizer                                                                | RoleDynamicVisualizer                                                                | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/RoleDynamicVisualizer.md
RoleFeedbackIntegrator                                                               | RoleFeedbackIntegrator                                                               | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/RoleFeedbackIntegrator.md
RoleGapNotifier                                                                      | RoleGapNotifier                                                                      | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/RoleGapNotifier.md
RoleModelAutoUpdater                                                                 | RoleModelAutoUpdater                                                                 | UxRoleModelExtract    | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/RoleModelAutoUpdater.md
self-check                                                                           | self-check  
                                                                         | BusinessAnalyticAgent | 
AgentFab      | draft | https://github.com/strato-space/prompt/blob/master/draft/self-check.md     

SelfImproveChecklist                                                                 | SelfImproveChecklist                                                                 | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/SelfImproveChecklist.md
SelfReviewChecklist                                                                  | SelfReviewChecklist                                                                  | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/SelfReviewChecklist.md
SolutionPatternRecommender                                                           | SolutionPatternRecommender                                                           | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/SolutionPatternRecommender.md
StratoSammary                                                                        | StratoSammary                                                                        | Stratoslav            | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/StratoSammary.md  

TrendFeedbackAggregator                                                              | TrendFeedbackAggregator                                                              | UxQA                  | 
FanFab        | draft | https://github.com/strato-space/prompt/blob/master/draft/TrendFeedbackAggregator.md
UserHint                                                                             | UserHint    
                                                                         | UxCreator             | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/UserHint.md       

UserQuotePool                                                                        | UserQuotePool                                                                        | UxResearcherInsights  | 
UxFab         | draft | https://github.com/strato-space/prompt/blob/master/draft/UserQuotePool.md  

```

## Тесты

```bash
pytest call
====================================== test session starts ======================================= 
platform win32 -- Python 3.13.7, pytest-8.4.2, pluggy-1.6.0
benchmark: 5.1.0 (defaults: timer=time.perf_counter disable_gc=False min_rounds=5 min_time=0.000005 max_time=1.0 calibration_precision=10 warmup=False warmup_iterations=100000)
rootdir: C:\home\strato-space\call
configfile: pytest.ini
plugins: anyio-4.10.0, benchmark-5.1.0, cov-7.0.0
collected 108 items

call\app\tests\test_actions_api_unit.py .....                                               [  4%] 
call\app\tests\test_bot_filters.py ....                                                     [  8%] 
call\app\tests\test_bot_payload_builder.py ..                                               [ 10%] 
call\app\tests\test_bot_plain_text.py .                                                     [ 11%] 
call\app\tests\test_bot_token_prompt_context.py ..                                          [ 12%] 
call\app\tests\test_builder_config.py ....                                                  [ 16%] 
call\app\tests\test_call_async_selection.py ...                                             [ 19%] 
call\app\tests\test_cli_normalization_and_payload.py ...                                    [ 22%] 
call\app\tests\test_cli_payload_builder.py ..                                               [ 24%] 
call\app\tests\test_cli_prompts_and_exec.py ..................                              [ 40%]
call\app\tests\test_discovery.py ...                                                        [ 43%]
call\app\tests\test_html_sanitizer.py ...                                                   [ 46%]
call\app\tests\test_init_compat.py .                                                        [ 47%]
call\app\tests\test_list_agents.py .....                                                    [ 51%]
call\app\tests\test_mcp_config_yaml.py .                                                    [ 52%]
call\app\tests\test_model_settings.py .......                                               [ 59%]
call\app\tests\test_payload_wildcard_tokens.py .....                                        [ 63%]
call\app\tests\test_prompt_resolution_and_merge.py .....                                    [ 68%]
call\app\tests\test_prompts_listing.py ....                                                 [ 72%]
call\app\tests\test_send_digest_notification.py ...                                         [ 75%]
call\app\tests\test_session_id.py ...                                                       [ 77%]
call\app\tests\test_target_interpretation.py ....                                           [ 81%]
call\app\tests\test_target_resolution_via_target.py .....                                   [ 86%]
call\app\tests\test_telegram_bot_handlers.py .........                                      [ 94%]
call\app\tests\test_telegram_bot_logging.py .                                               [ 95%]
call\app\tests\test_telegram_send.py ..                                                     [ 97%]
call\app\tests\test_telegram_text.py ..                                                     [ 99%] 
call\app\tests\test_welcome_html.py .                                                       [100%]

====================================== 108 passed in 31.76s ====================================== 

```

## Ссылки

- Источник истины по поведению: `call/README.md`.