import json
from typing import Any


def parse_sse_frame(frame: str) -> dict[str, Any] | str | None:
    lines = [line for line in frame.splitlines() if line.startswith("data:")]
    if not lines:
        return None

    data = "\n".join(line.removeprefix("data:").strip() for line in lines)
    if data == "[DONE]":
        return {"type": "done"}

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {"type": "raw", "data": data}

