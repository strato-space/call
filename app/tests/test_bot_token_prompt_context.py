import json
import pytest

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class DummyDoc:
    def __init__(self, file_id: str, file_name: str):
        self.file_id = file_id
        self.file_name = file_name


class DummyMessage:
    def __init__(
        self,
        text: str = "",
        *,
        reply_to: "DummyMessage | None" = None,
        document: DummyDoc | None = None
    ):
        self.text = text
        self.caption = ""
        self.document = document
        self.message_thread_id = None
        self.reply_to_message = reply_to


class DummyUpdate:
    def __init__(self, text: str, reply: DummyMessage | None = None):
        self.message = DummyMessage(text=text, reply_to=reply)

        class _Chat:
            id = 123
            type = "private"

        self.effective_chat = _Chat()


class DummyContext:
    def __init__(self, file_path: str | None = None):
        class _File:
            def __init__(self, path: str):
                self.file_path = path

        class _Bot:
            def __init__(self, path: str | None):
                self._path = path or "documents/file_1.pdf"

            async def get_file(self, file_id: str):
                return _File(self._path)

        self.bot = _Bot(file_path)


async def test_payload_field_ordering_with_reply_and_context():
    from call.telegram_bot.bot import build_input_payload_from_reply

    # Arrange: a reply with text and a document for context
    reply_doc = DummyDoc(file_id="1", file_name="test.pdf")
    reply_msg = DummyMessage(text="Reply content", document=reply_doc)
    update = DummyUpdate(text="/call @AgentFab Hello", reply=reply_msg)
    ctx = DummyContext(file_path="documents/file_1.pdf")

    # Act
    arg, payload = await build_input_payload_from_reply(
        "AgentFab", "Hello", update, ctx
    )

    # Assert key ordering in JSON string (Python preserves insertion order)
    assert arg.startswith("{")
    tpos = arg.find('"target"')
    rpos = arg.find('"replay"')
    ipos = arg.find('"input"')
    cpos = arg.find('"context"')
    assert all(x >= 0 for x in (tpos, rpos, ipos, cpos))
    assert tpos < rpos < ipos < cpos

    parsed = json.loads(arg)
    assert parsed["target"] == "AgentFab"
    assert parsed["replay"] == "Reply content"
    assert parsed["input"] == "Hello"
    assert (
        isinstance(parsed.get("context"), list) and parsed["context"]
    ), "expected context from document"


async def test_no_fs_fallback_for_prompt_token():
    from call.telegram_bot.bot import build_input_payload_from_reply

    # Arrange: no reply, a non-existing prompt token in main_text
    update = DummyUpdate(text="/call @AgentFab @DefinitelyNotExistingPromptToken")
    ctx = DummyContext()

    # Act
    arg, payload = await build_input_payload_from_reply(
        "AgentFab", "@DefinitelyNotExistingPromptToken", update, ctx
    )

    # Assert: no context created from filesystem fallback
    parsed = json.loads(arg) if payload else {}
    ctx_items = (parsed.get("context") or []) if parsed else []
    assert ctx_items == [] or all(
        it.get("type") != "file" or "path" not in it for it in ctx_items
    )
