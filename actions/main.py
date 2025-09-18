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
from call.lib.discovery import prompts as list_prompts


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
):
    res = call_lib(project=None, agent=None, prompt=None, target=name, input=input, echo=echo)
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


@app.post(
    "/exec",
    dependencies=[Depends(bearer_guard)],
    operation_id="exec_post",
    summary="Execute with a JSON payload (target|prompt|agent + context)",
)
def exec_action_post(payload: ExecPayload = Body(...)):
    # Enforce mutual exclusivity among agent/prompt/target when more than one provided
    fields = [f for f in [payload.target, payload.prompt, payload.agent] if (f or "").strip()]
    if len(fields) > 1:
        return JSONResponse(content={"ok": False, "error": "Provide exactly one of 'target' or 'prompt' or 'agent'"}, status_code=400)
    # Build input from context
    try:
        import json as _json
        inp = _json.dumps(payload.context) if payload.context is not None else ""
    except Exception:
        inp = str(payload.context)
    t = (payload.target or payload.prompt or payload.agent or None)
    res = call_lib(project=(payload.project or None), agent=None, prompt=None, target=t, input=inp, echo=bool(payload.echo))
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
        return JSONResponse(content={"error": "Invalid state; use 'ready' or 'draft'"}, status_code=400)
    items = list_prompts(project=(project or None), agent=(agent or None), prompt=(prompt or None), state=st)
    return items if isinstance(items, list) else ([items] if items else [])
