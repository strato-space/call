from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CallCard(BaseModel):
    """Engine-agnostic card DTO used by call's middleware layer."""

    model_config = ConfigDict(extra="allow")

    agent_name: str
    engine: str = "fast-agent"
    model: Optional[str] = None
    instructions: str = ""

    agents: List[str] = Field(default_factory=list)
    mcp_servers: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)

    raw_metadata: Dict[str, Any] = Field(default_factory=dict)
    source_path: Optional[str] = None
    source_url: Optional[str] = None

