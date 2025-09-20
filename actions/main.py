from __future__ import annotations

import time, uuid
from typing import Any, Optional, Literal
from pydantic import BaseModel
from fastapi import FastAPI, Depends, Query, Request, Body
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from .deps import bearer_guard

# Library imports
from call.lib.api import call as call_lib
from call.lib.api import list as list_lib
from call.lib.api import interpret_exec_payload
from call.lib import repo as call_repo


app = FastAPI(title="Call Actions API", version="1.0.0")


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
            exec_schema.setdefault("description", "Execute with a single selector. Exactly one of project|agent|prompt|target must be provided.")
            exec_schema["oneOf"] = [
                {"required": ["project"]},
                {"required": ["agent"]},
                {"required": ["prompt"]},
                {"required": ["target"]},
            ]
            # Hint for generators
            exec_schema["x-exactly-one"] = ["project", "agent", "prompt", "target"]
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
        print(f"[{rid}] {req.method} {req.url.path}?{req.url.query} -> {resp.status_code} {ms}ms")
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
)
def agents(
    project: str = Query("", description="Filter by project (supports * wildcard)"),
    agent: str = Query("", description="Filter by agent (supports * wildcard)"),
    prompt: str = Query("", description="Filter by prompt (supports * wildcard)"),
):
    return list_lib(project=(project or None), agent=(agent or None), prompt=(prompt or None))


@app.get(
    "/call",
    dependencies=[Depends(bearer_guard)],
    operation_id="call",
    summary="Call single agent/prompt via library",
)
def call(
    name: str = Query(..., description="The name used as 'target' for selection"),
    input: str = Query(..., description="Input text"),
    echo: bool = Query(False, description="If true, return structured JSON from library"),
    session_id: str | None = Query(None, description="Override session id: AgentName:chat or AgentName:chat:thread"),
):
    res = call_lib(project=None, agent=None, prompt=None, target=name, input=input, session_id=session_id, echo=echo)
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
    echo: Optional[bool] = True
    session_id: Optional[str] = None


@app.post(
    "/exec",
    dependencies=[Depends(bearer_guard)],
    operation_id="exec_post",
    summary="Execute with a JSON payload (target|prompt|agent + context)",
)
def exec_action_post(payload: ExecPayload = Body(...)):
    # Normalize via library helper
    kwargs, err = interpret_exec_payload({
        "project": payload.project,
        "agent": payload.agent,
        "prompt": payload.prompt,
        "target": payload.target,
        "context": payload.context,
        "echo": payload.echo,
        "session_id": payload.session_id,
    })
    if err:
        return JSONResponse(content=err, status_code=int(err.get("error_code", 400)))
    res = call_lib(**kwargs)
    try:
        if isinstance(res, dict) and res.get("ok") is False:
            return JSONResponse(content=res, status_code=int(res.get("error_code", 400)))
    except Exception:
        pass
    return res


@app.get(
    "/prompts",
    dependencies=[Depends(bearer_guard)],
    operation_id="prompts",
    summary="List prompts (ready/draft)",
)
def prompts(
    project: str = Query("", description="Filter by project (exact)"),
    agent: str = Query("", description="Filter by agent (exact)"),
    prompt: str = Query("", description="Filter by prompt id or name (supports *)"),
    state: str = Query("", description="Filter by state: ready|draft|<empty for both>"),
):
    st = (state or "").strip() or None
    if st not in (None, "ready", "draft"):
        err = {"ok": False, "error_code": 400, "description": "Invalid state; use 'ready' or 'draft'", "code": "BAD_REQUEST"}
        return JSONResponse(content=err, status_code=400)
    items = call_repo.list_prompts(project=(project or None), agent=(agent or None), prompt=(prompt or None), state=st)
    return items if isinstance(items, list) else ([items] if items else [])
