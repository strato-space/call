import importlib


def test_group_bot_mention_treats_rest_as_input(monkeypatch):
    bot = importlib.import_module("call.telegram_bot.bot")
    monkeypatch.setattr(bot, "SELECTED_BOT_NAME", "MediaGenBlenderBot", raising=False)
    monkeypatch.setattr(bot, "PROJECT_NAME", "MediaGenBlender", raising=False)

    name, text, should_handle = bot._resolve_agent_and_input(
        "@MediaGenBlenderBot google videos generate\n- wait\n- 8 sec",
        "MediaGenBlender",
        is_private=False,
    )

    assert should_handle is True
    assert name == ""
    assert text.startswith("google videos generate")


def test_group_bot_mention_accepts_explicit_at_target(monkeypatch):
    bot = importlib.import_module("call.telegram_bot.bot")
    monkeypatch.setattr(bot, "SELECTED_BOT_NAME", "MediaGenBlenderBot", raising=False)
    monkeypatch.setattr(bot, "PROJECT_NAME", "MediaGenBlender", raising=False)

    name, text, should_handle = bot._resolve_agent_and_input(
        "@MediaGenBlenderBot @PM-2 weekly status",
        "MediaGenBlender",
        is_private=False,
    )

    assert should_handle is True
    assert name == "PM-2"
    assert text == "weekly status"

