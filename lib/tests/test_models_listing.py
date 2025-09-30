def test_models_uses_openai_client(monkeypatch):
    from call.lib import api as call_api

    captured = {}

    class DummyModel:
        def __init__(self, ident: str):
            self.id = ident

        def model_dump(self):
            return {"id": self.id, "type": "test"}

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
