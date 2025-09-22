import json
import types
import pytest
from pathlib import Path

pytestmark = pytest.mark.anyio("asyncio")

@pytest.fixture
def anyio_backend():
    return "asyncio"

class DummyMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.caption = ""
        self.document = None
        self.message_thread_id = None

class DummyUpdate:
    def __init__(self, text: str):
        self.message = DummyMessage(text=text)
        # Minimal attrs used by builder
        class _Chat:
            id = 123
            type = "private"
        self.effective_chat = _Chat()

class DummyContext:
    def __init__(self):
        class _Bot:
            async def get_file(self, file_id: str):
                raise RuntimeError("not used in this test")
        self.bot = _Bot()


async def test_main_text_prompt_token_included_via_fs_fallback(tmp_path, monkeypatch):
    """
    Ensure that when main_text contains '@PromptId', the builder adds a
    context item of type 'file' with the prompt content using the filesystem
    fallback under prompt/{draft|ready}/PromptId.md
    """
    # Arrange: create a temporary prompt repo structure
    prompt_repo = tmp_path / "prompt"
    draft_dir = prompt_repo / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    p = draft_dir / "3-OnlineChunkSummarization.md"
    p.write_text("""<!-- METADATA:START -->\n```yaml\nid: 3-OnlineChunkSummarization\nproject: UxFab\nagent: DialogOnlineAnalysis\n```\n<!-- METADATA:END -->\n\n<!-- PROMPT:START -->\nbody\n<!-- PROMPT:END -->\n""", encoding="utf-8")

    # Monkeypatch discovery to point to our temp repo
    disc = __import__('importlib').import_module('call.lib.discovery')
    monkeypatch.setattr(disc, 'discover_prompt_repo', lambda: prompt_repo, raising=True)

    # Import builder
    from call.telegram_bot.bot import build_input_payload_from_reply

    # Act: main_text carries the prompt token
    update = DummyUpdate(text="/call @AgentFab @3-OnlineChunkSummarization")
    ctx = DummyContext()
    arg, payload = await build_input_payload_from_reply("AgentFab", "@3-OnlineChunkSummarization", update, ctx)

    # Assert: JSON payload contains file context with our prompt path and content
    parsed = json.loads(arg)
    ctx_items = parsed.get("context") or []
    assert any(it.get("type") == "file" and it.get("name") == "3-OnlineChunkSummarization.md" for it in ctx_items)
    item = next(it for it in ctx_items if it.get("type") == "file")
    assert item.get("path") and Path(item["path"]).name == "3-OnlineChunkSummarization.md"
    assert isinstance(item.get("content"), str) and "METADATA" in item.get("content")
