# TODO: Call Repo Updates

## Контекст
✅ Voice + Prompt repos обновлены и запушены
⏳ Call repo нужно обновить

## Задача 1: Repo Scanner (repo_fs.py)

**Проблема**: Scanner не читает плоские PM-*.md файлы

**Решение**:
1. Добавить чтение `<Project>/*.md` (не только `*/agent.md`)
2. Проверять `type: prompt` в metadata → включать в prompts
3. Проверять `type: project` → НЕ включать в prompts
4. Если type отсутствует → считать prompt (совместимость)

**Код** (~строки 200-300 в repo_fs.py):
```python
# ДОБАВИТЬ после сканирования agent.md:
for md_file in pdir.glob("*.md"):
    if md_file.name == "project.md":
        continue  # Skip, check metadata later
    
    metadata = _read_metadata(md_file)
    if metadata.get('type') != 'project':
        prompts.append(...)  # Add to prompts
```

**Результат**: StratoProject должен показывать 15 prompts (без project.md)

## Задача 2: MCP Logging (mcp_hook.py)

**Добавить вывод аргументов**:
```python
logger.debug(f"[MCP Hook] Arguments (YAML):\n{yaml.dump(args)}")
```

## Задача 3: Тесты

**Файлы**:
- `test_target_resolution_via_target.py` (2 failed)
- `test_telegram_bot_logging.py` (1 failed)

**Действие**: Обновить expectations под новый type: project/prompt

## Запуск
```bash
cd call
pytest -xvs
```
