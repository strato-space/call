from __future__ import annotations

import logging
import time, uuid
import os
import sys
from typing import Any, Optional, Literal
from pydantic import BaseModel
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Body, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, PlainTextResponse

from .deps import bearer_guard

# Library imports (API-only)
from call.lib.api import call as api_call
from call.lib.api import call_async as api_call_async
from call.lib.api import list as api_list
from call.lib.api import list_prompts as api_list_prompts
from call.lib.api import models as api_models
from call.lib.api import read as api_read
from call.lib.api import write as api_write
from call.lib.api import api_interpret_exec_payload as api_interpret_exec_payload
from call.lib.logging import configure_logging
from call.lib import repo_db as repo_db_module
from contextlib import asynccontextmanager
from call.app.call import start_mcp_owner_task, stop_mcp_owner_task


# Expose thin wrappers for test monkeypatching (delegate to API)
def list_prompts(
    *,
    project: str | None = None,
    agent: str | None = None,
    prompt: str | None = None,
    state: str | None = None,
    target: str | None = None,
):
    return api_list_prompts(
        project=project, agent=agent, prompt=prompt, state=state, target=target
    )


# Configure logging once so CALL_DEBUG and related envs take effect under Uvicorn
configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MCP servers at startup to avoid cold start during first requests."""
    logger = logging.getLogger("call.actions")

    await start_mcp_owner_task("actions")
    logger.info("MCP owner task started for Actions lifespan")

    yield

    await stop_mcp_owner_task()
    logger.info("MCP owner task stopped for Actions lifespan")


_PYTEST = bool(os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules)

# When running unit tests we disable the lifespan entirely to avoid background MCP startup.
if _PYTEST:
    app = FastAPI(title="Call Actions API", version="2.0.2")
else:
    app = FastAPI(title="Call Actions API", version="2.0.2", lifespan=lifespan)


def custom_openapi():
    if getattr(app, "openapi_schema", None):
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema["servers"] = [
        {"url": "https://call-actions.stratospace.fun"},
    ]
    # bearer security
    comps = schema.setdefault("components", {})
    sec_schemes = comps.setdefault("securitySchemes", {})
    sec_schemes["bearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    schema["security"] = [{"bearerAuth": []}]
    # Enforce (in schema) that ExecPayload must specify exactly one of project|agent|prompt|target
    try:
        schemas = comps.setdefault("schemas", {})
        exec_schema = schemas.get("ExecPayload")
        if isinstance(exec_schema, dict):
            # Add oneOf alternatives; each alternative requires exactly one selector
            exec_schema.setdefault(
                "description",
                "Execute with a single selector. Exactly one of project|agent|prompt|target must be provided.",
            )
            exec_schema["oneOf"] = [
                {"required": ["project"]},
                {"required": ["agent"]},
                {"required": ["prompt"]},
                {"required": ["target"]},
            ]
            # Hint for generators
            exec_schema["x-exactly-one"] = ["project", "agent", "prompt", "target"]
        notify_schema = schemas.get("NotifyPayload")
        if isinstance(notify_schema, dict):
            notify_schema.setdefault(
                "description",
                "Notify the system about an event. 'event' is required; project/agent/prompt/target selectors are not accepted.",
            )
            notify_schema.setdefault("required", ["event"])
    except Exception:
        # Schema patching best-effort only
        pass
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[assignment]


@app.middleware("http")
async def access_log(req: Request, call_next):
    rid = req.headers.get("X-Request-Id") or str(uuid.uuid4())
    t0 = time.time()
    try:
        resp = await call_next(req)
        ms = int((time.time() - t0) * 1000)
        print(
            f"[{rid}] {req.method} {req.url.path}?{req.url.query} -> {resp.status_code} {ms}ms"
        )
        resp.headers["X-Request-Id"] = rid
        return resp
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        print(f"[{rid}] ERROR {req.method} {req.url} {ms}ms {e}")
        raise


@app.get(
    "/agents",
    dependencies=[Depends(bearer_guard)],
    operation_id="agents",
    summary="List available agents (hierarchical)",
    openapi_extra={"x-openai-isConsequential": False},
)
def agents(
    project: str = Query("", description="Filter by project (supports * wildcard)"),
    agent: str = Query("", description="Filter by agent (supports * wildcard)"),
    prompt: str = Query("", description="Filter by prompt (supports * wildcard)"),
):
    return api_list(
        project=(project or None), agent=(agent or None), prompt=(prompt or None)
    )


@app.get(
    "/call",
    dependencies=[Depends(bearer_guard)],
    operation_id="call",
    summary="Call single agent/prompt via library",
)
def call(
    name: str = Query(..., description="The name used as 'target' for selection"),
    input: str = Query(..., description="Input text"),
    echo: bool = Query(
        False, description="If true, return structured JSON from library"
    ),
    session_id: str | None = Query(
        None, description="Override session id (format: chat or chat:thread)"
    ),
    event: str | None = Query(
        None,
        description="Optional event name to acknowledge without pipeline execution",
    ),
    model: str | None = Query(None, description="Override model for this call"),
):
    attrs = None
    if model is not None:
        model_str = str(model).strip()
        if model_str:
            attrs = {"model": model_str}
    res = api_call(
        project=None,
        agent=None,
        prompt=None,
        target=name,
        input=input,
        event=event,
        session_id=session_id,
        echo=echo,
        attributes=attrs,
    )
    try:
        if isinstance(res, dict) and res.get("ok") is False:
            status = int(res.get("error_code", 400))
            return JSONResponse(content=res, status_code=status)
    except Exception:
        pass
    return res


class ExecPayload(BaseModel):
    project: Optional[str] = None
    agent: Optional[str] = None
    prompt: Optional[str] = None
    target: Optional[str] = None
    context: Optional[Any] = None
    echo: Optional[bool] = False
    session_id: Optional[str] = None
    model: Optional[str] = None


class NotifyPayload(BaseModel):
    event: str
    echo: Optional[bool] = False
    session_id: Optional[str] = None


@app.post(
    "/exec",
    dependencies=[Depends(bearer_guard)],
    operation_id="exec_post",
    summary="Execute with a JSON payload (project|agent|prompt|target + optional context)",
)
async def exec_action_post(payload: ExecPayload = Body(...)):
    # Normalize via library helper
    payload_dict = payload.model_dump(exclude_unset=True)
    kwargs, err = api_interpret_exec_payload(payload_dict)
    if err:
        return JSONResponse(content=err, status_code=int(err.get("error_code", 400)))
    # During unit tests we prefer the synchronous stub-friendly path.
    if _PYTEST:
        res = api_call(**kwargs)
    else:
        res = await api_call_async(**kwargs)
    try:
        if isinstance(res, dict) and res.get("ok") is False:
            return JSONResponse(
                content=res, status_code=int(res.get("error_code", 400))
            )
    except Exception:
        pass
    return res


@app.post(
    "/notify",
    dependencies=[Depends(bearer_guard)],
    operation_id="notify_post",
    summary="Acknowledge an event with a JSON payload (event required)",
    description="Notify payloads must include an 'event' field and may not specify project/agent/prompt/target selectors.",
)
async def notify_action_post(request: Request, payload: NotifyPayload = Body(...)):
    raw_body = (await request.body()).decode("utf-8", "replace")
    payload_dict = payload.model_dump(exclude_unset=True)
    kwargs, err = api_interpret_exec_payload(payload_dict)
    if err:
        return JSONResponse(content=err, status_code=int(err.get("error_code", 400)))
    kwargs["input"] = raw_body
    res = await api_call_async(**kwargs)
    try:
        if isinstance(res, dict) and res.get("ok") is False:
            return JSONResponse(
                content=res, status_code=int(res.get("error_code", 400))
            )
    except Exception:
        pass
    return res


@app.get(
    "/read/{id}",
    dependencies=[Depends(bearer_guard)],
    operation_id="read",
    summary="Read raw card text from repo.db for any entity project/agent/prompt",
    openapi_extra={"x-openai-isConsequential": False},
)
def read(id: str):
    try:
        text = api_read(id)
        return PlainTextResponse(text)
    except repo_db_module.CardNotFoundError:
        err = {
            "ok": False,
            "error_code": 404,
            "description": f"Card '{id}' not found",
            "code": "NO_DATA_FOUND",
        }
        return JSONResponse(content=err, status_code=404)
    except ValueError as exc:
        err = {
            "ok": False,
            "error_code": 400,
            "description": str(exc),
            "code": "BAD_REQUEST",
        }
        return JSONResponse(content=err, status_code=400)


@app.post(
    "/write/{id}",
    dependencies=[Depends(bearer_guard)],
    operation_id="write",
    summary="Write card text to repo.db and filesystem for any entity project/agent/prompt",
)
def write(id: str, payload: str = Body(..., media_type="text/plain")):
    try:
        api_write(id, str(payload))
        return PlainTextResponse("ok")
    except repo_db_module.CardNotFoundError:
        err = {
            "ok": False,
            "error_code": 404,
            "description": f"Card '{id}' not found",
            "code": "NO_DATA_FOUND",
        }
        return JSONResponse(content=err, status_code=404)
    except ValueError as exc:
        err = {
            "ok": False,
            "error_code": 400,
            "description": str(exc),
            "code": "BAD_REQUEST",
        }
        return JSONResponse(content=err, status_code=400)


@app.get(
    "/prompts",
    dependencies=[Depends(bearer_guard)],
    operation_id="prompts",
    summary="List prompts (ready/draft)",
    openapi_extra={"x-openai-isConsequential": False},
)
def prompts(
    project: str = Query("", description="Filter by project (exact)"),
    agent: str = Query("", description="Filter by agent (exact)"),
    prompt: str = Query("", description="Filter by prompt id or name (supports *)"),
    state: str = Query("", description="Filter by state: ready|draft|<empty for both>"),
):
    st = (state or "").strip() or None
    if st not in (None, "ready", "draft"):
        err = {
            "ok": False,
            "error_code": 400,
            "description": "Invalid state; use 'ready' or 'draft'",
            "code": "BAD_REQUEST",
        }
        return JSONResponse(content=err, status_code=400)
    items = list_prompts(
        project=(project or None),
        agent=(agent or None),
        prompt=(prompt or None),
        state=st,
    )
    return items if isinstance(items, list) else ([items] if items else [])


@app.get(
    "/models",
    dependencies=[Depends(bearer_guard)],
    operation_id="models",
    summary="List available OpenAI models",
    openapi_extra={"x-openai-isConsequential": False},
)
def models_endpoint():
    return api_models()


@app.get(
    "/reload",
    dependencies=[Depends(bearer_guard)],
    operation_id="reload",
    summary="Reload repository indices (agent/prompt) and rebuild repo.db",
)
def reload():
    # Read repos list from .env via repo_fs implementation (no client-provided parameters)
    res = api_reload()
    try:
        if isinstance(res, dict) and not res.get("ok", False):
            return JSONResponse(
                content=res, status_code=int(res.get("error_code", 500))
            )
    except Exception:
        pass
    return res
