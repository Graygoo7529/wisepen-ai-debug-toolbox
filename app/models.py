from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DebugEvent:
    seq: int
    event_type: str
    payload: dict[str, Any] | str
    raw_frame: str
    received_at: datetime


@dataclass
class ToolCallView:
    call_id: str
    tool_name: str
    step_index: int
    status: str = "pending"
    input: dict[str, Any] | None = None
    output: Any | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    raw_events: list[DebugEvent] = field(default_factory=list)


@dataclass
class TurnView:
    turn_id: str
    session_id: str
    query: str
    status: str = "running"
    step_index: int = 0
    text: str = ""
    reasoning: str = ""
    error: str | None = None
    events: list[DebugEvent] = field(default_factory=list)
    tool_calls: dict[str, ToolCallView] = field(default_factory=dict)

