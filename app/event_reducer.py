from app.models import DebugEvent, ToolCallView, TurnView


def apply_event(turn: TurnView, event: DebugEvent) -> None:
    turn.events.append(event)
    payload = event.payload
    if not isinstance(payload, dict):
        return

    event_type = payload.get("type")

    if event_type == "start-step":
        turn.step_index += 1
        return

    if event_type == "text-delta":
        turn.text += payload.get("delta", "")
        return

    if event_type == "reasoning-delta":
        turn.reasoning += payload.get("delta", "")
        return

    if event_type == "tool-input-start":
        call_id = payload.get("toolCallId", "")
        if not call_id:
            return
        turn.tool_calls[call_id] = ToolCallView(
            call_id=call_id,
            tool_name=payload.get("toolName", ""),
            step_index=max(turn.step_index, 1),
            status="running",
            started_at=event.received_at,
            raw_events=[event],
        )
        return

    if event_type == "tool-input-available":
        call_id = payload.get("toolCallId", "")
        tool = _get_or_create_tool(turn, call_id, payload.get("toolName", ""), event)
        if tool is None:
            return
        tool.input = payload.get("input")
        tool.status = "running"
        tool.raw_events.append(event)
        return

    if event_type == "tool-output-available":
        call_id = payload.get("toolCallId", "")
        tool = _get_or_create_tool(turn, call_id, "", event)
        if tool is None:
            return
        tool.output = payload.get("output")
        tool.status = "success"
        tool.finished_at = event.received_at
        tool.raw_events.append(event)
        return

    if event_type == "error":
        turn.status = "error"
        turn.error = payload.get("errorText") or "Unknown stream error"
        return

    if event_type == "abort":
        turn.status = "aborted"
        turn.error = payload.get("reason") or "aborted"
        return

    if event_type in {"finish", "done"}:
        if turn.status == "running":
            turn.status = "finished"


def _get_or_create_tool(
    turn: TurnView,
    call_id: str,
    tool_name: str,
    event: DebugEvent,
) -> ToolCallView | None:
    if not call_id:
        return None
    tool = turn.tool_calls.get(call_id)
    if tool is not None:
        if tool_name and not tool.tool_name:
            tool.tool_name = tool_name
        return tool

    tool = ToolCallView(
        call_id=call_id,
        tool_name=tool_name,
        step_index=max(turn.step_index, 1),
        status="running",
        started_at=event.received_at,
        raw_events=[],
    )
    turn.tool_calls[call_id] = tool
    return tool

