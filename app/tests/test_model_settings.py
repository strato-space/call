import types

from call.app.call import _model_settings_from_attributes, ModelSettings


def _ms_dict(ms: ModelSettings) -> dict:
    return {
        "temperature": ms.temperature,
        "top_p": ms.top_p,
        "frequency_penalty": ms.frequency_penalty,
        "presence_penalty": ms.presence_penalty,
        "max_tokens": ms.max_tokens,
        "verbosity": ms.verbosity,
        "reasoning": (getattr(ms.reasoning, "effort", None) if ms.reasoning else None),
    }


def test_model_params_prefers_model_specific_over_generic():
    attrs = {
        "model": "gpt-5",
        "model-params": {
            "temperature": 0.9,
        },
        "model-params-gpt-5": {
            "temperature": 0.1,
            "reasoning": {"effort": "medium"},
        },
    }
    ms = _model_settings_from_attributes(attrs)
    d = _ms_dict(ms)
    assert d["temperature"] == 0.1
    assert d["reasoning"] == "medium"


def test_model_params_generic_used_when_specific_missing():
    attrs = {
        "model": "gpt-5",
        "model-params": {
            "temperature": 0.7,
            "top_p": 0.8,
        },
    }
    ms = _model_settings_from_attributes(attrs)
    d = _ms_dict(ms)
    assert d["temperature"] == 0.7
    assert d["top_p"] == 0.8


def test_legacy_keys_are_ignored():
    attrs = {
        "model": "gpt-5",
        # Legacy forms that should be ignored now
        "model_params": {"temperature": 0.2},
        "modelParams": {"temperature": 0.3},
        "model_params_gpt-5": {"temperature": 0.4},
        "modelParamsgpt-5": {"temperature": 0.5},
    }
    ms = _model_settings_from_attributes(attrs)
    d = _ms_dict(ms)
    # No canonical keys present -> all None
    assert d["temperature"] is None


def test_model_can_come_from_cfg_object():
    cfg = types.SimpleNamespace(
        model="gpt-4.1",
        attributes={
            "model-params-gpt-4.1": {"temperature": 0.2, "top_p": 0.9},
        },
    )
    ms = _model_settings_from_attributes(cfg)
    d = _ms_dict(ms)
    assert d["temperature"] == 0.2
    assert d["top_p"] == 0.9

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
        "model-params": {
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
