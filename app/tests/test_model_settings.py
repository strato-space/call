import importlib


def test_model_settings_from_cfg_reasoning_effort_medium():
    call_mod = importlib.import_module("call.app.call")

    class _Cfg:
        def __init__(self):
            self.attributes = {
                "model-params": {
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "reasoning": {"effort": "medium"},
                }
            }

    cfg = _Cfg()
    ms = call_mod._model_settings_from_attributes(cfg)
    # Basic numeric params parsed as floats
    assert ms.temperature == 0.2
    assert ms.top_p == 0.9
    # Reasoning object exists and has correct effort
    r = getattr(ms, "reasoning", None)
    assert r is not None
    eff = getattr(r, "effort", None)
    assert eff == "medium"


def test_model_settings_from_dict_with_synonyms():
    call_mod = importlib.import_module("call.app.call")
    attrs = {
        "model_params": {
            "temperature": 0.1,
            "top-p": 0.7,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.2,
        }
    }
    ms = call_mod._model_settings_from_attributes(attrs)
    assert ms.temperature == 0.1
    assert ms.top_p == 0.7
    assert ms.presence_penalty == 0.5
    assert ms.frequency_penalty == 0.2


def test_model_settings_reasoning_summary_detailed():
    call_mod = importlib.import_module("call.app.call")

    class _Cfg:
        def __init__(self):
            self.attributes = {
                "model-params": {
                    "reasoning": {
                        "effort": "high",
                        "summary": "detailed",
                    }
                }
            }

    cfg = _Cfg()
    ms = call_mod._model_settings_from_attributes(cfg)
    r = getattr(ms, "reasoning", None)
    assert r is not None
    eff = getattr(r, "effort", None)
    summ = getattr(r, "summary", None)
    assert eff == "high"
    assert summ == "detailed"
