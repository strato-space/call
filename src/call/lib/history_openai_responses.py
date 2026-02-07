from __future__ import annotations

import json
from typing import Any

import mcp.types as mcp_types

from call.lib.history_models import HistoryMessage, normalize_role


def _is_image_mime_type(mime_type: str | None) -> bool:
    return bool(mime_type) and str(mime_type).lower().startswith("image/")


def _tool_result_to_text(result: Any) -> str:
    contents = getattr(result, "content", None) or []
    chunks: list[str] = []
    for item in contents:
        if isinstance(item, mcp_types.TextContent):
            if item.text:
                chunks.append(item.text)
            continue
        uri = getattr(item, "uri", None)
        if uri:
            chunks.append(f"[Resource]({uri})")
            continue
        chunks.append(f"[Unsupported content: {type(item).__name__}]")
    return "\n".join(c for c in chunks if c)


def _normalize_tool_ids(tool_use_id: str | None) -> tuple[str, str]:
    tool_use_id = str(tool_use_id or "")
    if tool_use_id.startswith("fc"):
        suffix = tool_use_id[3:] if tool_use_id.startswith("fc_") else tool_use_id[2:]
        call_id = f"call_{suffix}" if suffix else f"call_{tool_use_id}"
        return tool_use_id, call_id
    if tool_use_id.startswith("call_"):
        suffix = tool_use_id[len("call_") :]
        fc_id = f"fc_{suffix}" if suffix else f"fc_{tool_use_id}"
        return fc_id, tool_use_id
    return f"fc_{tool_use_id}", f"call_{tool_use_id}"


def _content_to_part(content: mcp_types.ContentBlock, role: str) -> dict[str, Any]:
    text_type = "output_text" if role == "assistant" else "input_text"

    if isinstance(content, mcp_types.TextContent):
        return {"type": text_type, "text": content.text or ""}

    if isinstance(content, mcp_types.ResourceLink):
        name = content.name or "resource"
        uri = content.uri
        return {"type": text_type, "text": f"[{name}]({uri})"}

    if isinstance(content, mcp_types.ImageContent):
        if _is_image_mime_type(content.mimeType):
            return {
                "type": "input_image",
                "image_url": f"data:{content.mimeType};base64,{content.data}",
            }
        return {"type": text_type, "text": f"[Image:{content.mimeType}]"}

    if isinstance(content, mcp_types.AudioContent):
        # Responses supports input_file; keep it simple and surface a placeholder for now.
        return {"type": text_type, "text": f"[Audio:{content.mimeType}]"}

    if isinstance(content, mcp_types.EmbeddedResource):
        uri = getattr(content.resource, "uri", None)
        mime_type = getattr(content.resource, "mimeType", None)
        blob = getattr(content.resource, "blob", None)
        if blob and _is_image_mime_type(mime_type):
            return {"type": "input_image", "image_url": f"data:{mime_type};base64,{blob}"}
        if uri:
            return {"type": text_type, "text": f"[Resource]({uri})"}
        return {"type": text_type, "text": f"[Resource:{mime_type or 'unknown'}]"}

    uri = getattr(content, "uri", None)
    if uri:
        return {"type": text_type, "text": f"[Resource]({uri})"}
    return {"type": text_type, "text": f"[Unsupported content: {type(content).__name__}]"}


def messages_to_response_items(messages: list[HistoryMessage]) -> list[dict[str, Any]]:
    """Convert call-native messages into OpenAI Responses input items."""
    items: list[dict[str, Any]] = []
    tool_call_id_map: dict[str, str] = {}

    for msg in messages:
        # Tool results first (matches typical Responses ordering).
        if msg.tool_results:
            for tool_use_id, result in msg.tool_results.items():
                fc_id, call_id = _normalize_tool_ids(tool_use_id)
                tool_call_id_map[tool_use_id] = call_id
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _tool_result_to_text(result),
                    }
                )

        if msg.content:
            parts = [_content_to_part(c, msg.role) for c in msg.content]
            if parts:
                items.append({"type": "message", "role": msg.role, "content": parts})

        if msg.tool_calls:
            for tool_use_id, request in msg.tool_calls.items():
                params = getattr(request, "params", None)
                name = getattr(params, "name", None) or "tool"
                arguments = getattr(params, "arguments", None) or {}
                fc_id, call_id = _normalize_tool_ids(tool_use_id)
                tool_call_id_map[tool_use_id] = call_id
                items.append(
                    {
                        "type": "function_call",
                        "id": fc_id,
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(arguments),
                    }
                )

    return items


def _part_to_content_block(part: dict[str, Any]) -> mcp_types.ContentBlock | None:
    if not isinstance(part, dict):
        return None
    ptype = str(part.get("type") or "")
    if ptype in {"input_text", "output_text", "text"}:
        return mcp_types.TextContent(type="text", text=str(part.get("text") or ""))

    if ptype == "input_image":
        url = str(part.get("image_url") or "")
        if url.startswith("data:") and ";base64," in url:
            header, b64 = url.split(";base64,", 1)
            mime_type = header[len("data:") :] or "image/png"
            return mcp_types.ImageContent(type="image", data=b64, mimeType=mime_type)
        if url:
            return mcp_types.ResourceLink(
                type="resource_link",
                name="image",
                uri=url,
                mimeType="image/*",
            )
        return None

    if ptype in {"input_file"}:
        file_url = part.get("file_url")
        if file_url:
            return mcp_types.ResourceLink(
                type="resource_link",
                name="file",
                uri=str(file_url),
            )
        file_data = part.get("file_data")
        if file_data:
            # Best-effort: store as embedded blob resource.
            return mcp_types.EmbeddedResource(
                type="resource",
                resource=mcp_types.BlobResourceContents(
                    uri="file:///embedded",
                    mimeType=str(part.get("mime_type") or "application/octet-stream"),
                    blob=str(file_data),
                ),
            )
        return None

    return mcp_types.TextContent(type="text", text=f"[unsupported-part:{ptype}]")


def response_items_to_messages(items: list[dict[str, Any]]) -> list[HistoryMessage]:
    """Convert OpenAI Responses items into call-native messages.

    This parser is intentionally tolerant: unknown items are preserved as text.
    """
    out: list[HistoryMessage] = []

    def _ensure_assistant_for_tool_calls() -> HistoryMessage:
        if out and out[-1].role == "assistant":
            return out[-1]
        msg = HistoryMessage(role="assistant", content=[])
        out.append(msg)
        return msg

    def _ensure_assistant_for_tool_results() -> HistoryMessage:
        # Prefer a trailing synthetic assistant message (empty content + no tool_calls)
        if out and out[-1].role == "assistant" and (not out[-1].content) and not out[-1].tool_calls:
            return out[-1]
        msg = HistoryMessage(role="assistant", content=[])
        out.append(msg)
        return msg

    for item in items:
        if not isinstance(item, dict):
            continue
        itype = str(item.get("type") or "")

        if itype == "message":
            role = normalize_role(item.get("role"))
            parts = item.get("content") or []
            blocks: list[mcp_types.ContentBlock] = []
            if isinstance(parts, list):
                for part in parts:
                    try:
                        b = _part_to_content_block(part)
                        if b is not None:
                            blocks.append(b)
                    except Exception:
                        continue
            out.append(HistoryMessage(role=role, content=blocks))
            continue

        if itype == "function_call":
            call_id = str(item.get("call_id") or "")
            name = str(item.get("name") or "tool")
            args_raw = item.get("arguments")
            try:
                arguments = (
                    json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                )
            except Exception:
                arguments = {"_raw": str(args_raw)}
            req = mcp_types.CallToolRequest(
                method="tools/call",
                params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
            )
            msg = _ensure_assistant_for_tool_calls()
            if msg.tool_calls is None:
                msg.tool_calls = {}
            key = call_id or str(item.get("id") or "") or f"call_{len(msg.tool_calls)}"
            msg.tool_calls[key] = req
            continue

        if itype == "function_call_output":
            call_id = str(item.get("call_id") or "")
            output = str(item.get("output") or "")
            result = mcp_types.CallToolResult(
                content=[mcp_types.TextContent(type="text", text=output)],
                isError=bool(item.get("isError") or item.get("is_error") or False),
            )
            msg = _ensure_assistant_for_tool_results()
            if msg.tool_results is None:
                msg.tool_results = {}
            key = call_id or f"call_{len(msg.tool_results)}"
            msg.tool_results[key] = result
            continue

        # Unknown item: keep a compact placeholder.
        out.append(
            HistoryMessage(
                role="assistant",
                content=[mcp_types.TextContent(type="text", text=f"[unsupported-item:{itype}]")],
            )
        )

    return out

