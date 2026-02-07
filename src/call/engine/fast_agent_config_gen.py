from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from call.lib.paths import default_cache_dir, default_mcp_config_path


def _clean_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def generate_fast_agent_config(*, output_path: Path | None = None) -> Path:
    """Generate a fast-agent `fastagent.config.yaml` from call's `mcp_config.yaml`.

    The goal is to reuse call's single MCP config as the source of truth.
    """
    src_path = default_mcp_config_path()
    data = _clean_dict(yaml.safe_load(src_path.read_text(encoding="utf-8")) or {})
    servers = _clean_dict(data.get("mcpServers") or {})

    out_servers: Dict[str, Dict[str, Any]] = {}
    api_access_token = os.getenv("API_ACCESS_TOKEN", "")

    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        if not bool(spec.get("enabled", False)):
            continue

        out: Dict[str, Any] = {}

        if spec.get("command"):
            out["command"] = str(spec.get("command"))
        if isinstance(spec.get("args"), list):
            out["args"] = [str(a) for a in (spec.get("args") or [])]
        if spec.get("cwd"):
            out["cwd"] = str(spec.get("cwd"))
        if isinstance(spec.get("env"), dict):
            out["env"] = {str(k): str(v) for k, v in (spec.get("env") or {}).items()}

        server_url = spec.get("serverUrl") or spec.get("url")
        if server_url:
            out["url"] = str(server_url)

        if isinstance(spec.get("headers"), dict):
            headers = {}
            for k, v in (spec.get("headers") or {}).items():
                val = str(v)
                if "{API_ACCESS_TOKEN}" in val:
                    val = val.replace("{API_ACCESS_TOKEN}", api_access_token)
                headers[str(k)] = val
            out["headers"] = headers

        # Timeout mapping (best-effort).
        if spec.get("timeoutSeconds") is not None:
            try:
                out["read_timeout_seconds"] = int(float(spec.get("timeoutSeconds")))
            except Exception:
                pass
        if spec.get("sseReadTimeoutSeconds") is not None:
            try:
                out["read_transport_sse_timeout_seconds"] = int(
                    float(spec.get("sseReadTimeoutSeconds"))
                )
            except Exception:
                pass

        if spec.get("auth") is not None:
            out["auth"] = spec.get("auth")

        if out:
            out_servers[str(name)] = out

    default_model = str(os.environ.get("LLM_MODEL", "gpt-5") or "gpt-5").strip() or "gpt-5"

    payload: Dict[str, Any] = {
        "execution_engine": "asyncio",
        "default_model": default_model,
        "mcp": {"servers": out_servers},
    }

    if output_path is None:
        output_path = default_cache_dir() / "fastagent.generated.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return output_path

