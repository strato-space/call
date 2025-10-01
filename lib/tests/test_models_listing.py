def test_models_uses_openai_client(monkeypatch):
    from call.lib import api as call_api

    captured = {}

    class DummyModel:
        def __init__(self, ident: str):
            self.id = ident

        def model_dump(self):
            return {"id": self.id, "type": "test", "modes": ["text"]}

    class DummyModels:
        def list(self):
            captured["listed"] = True
            return type("Resp", (), {"data": [DummyModel("gpt-alpha"), DummyModel("gpt-beta")]})()

    class DummyClient:
        def __init__(self):
            self.models = DummyModels()

    import openai

    monkeypatch.setattr(openai, "OpenAI", DummyClient, raising=True)

    items = call_api.models()

    assert captured.get("listed") is True
    assert isinstance(items, list)
    assert items[0]["id"] == "gpt-alpha"
    assert items[1]["id"] == "gpt-beta"


def test_models_filters_out_non_text_and_snapshot_entries(monkeypatch):
    from call.lib import api as call_api

    class DummyModel:
        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return dict(self._payload)

    response_items = [
        DummyModel({"id": "gpt-text", "modes": ["text", "audio"]}),
        DummyModel({"id": "gpt-vision", "modes": ["vision"]}),
        DummyModel({"id": "gpt-mini-2024-07-18", "modes": ["text"]}),
        DummyModel({"id": "gpt-chat", "modes": ["chat.completions"]}),
    ]

    class DummyModels:
        def list(self):
            return type("Resp", (), {"data": response_items})()

    class DummyClient:
        def __init__(self):
            self.models = DummyModels()

    import openai

    monkeypatch.setattr(openai, "OpenAI", DummyClient, raising=True)

    items = call_api.models()

    ids = [item["id"] for item in items]
    assert ids == ["gpt-text", "gpt-chat"]
