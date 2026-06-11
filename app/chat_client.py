from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import httpx


def chat_headers(
    from_source: str,
    user_id: str,
    developer: str,
    x_developer: str,
    accept: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if accept:
        headers["Accept"] = accept
    if from_source.strip():
        headers["X-From-Source"] = from_source.strip()
    if user_id.strip():
        headers["X-User-Id"] = user_id.strip()
    if developer.strip():
        headers["developer"] = developer.strip()
    if x_developer.strip():
        headers["X-Developer"] = x_developer.strip()
    return headers


def stream_chat_completions(
    completions_url: str,
    session_id: str,
    query: str,
    model_id: str,
    from_source: str,
    user_id: str,
    developer: str,
    x_developer: str,
    user_defined_on_demand_skill_ids: list[str],
    states: list[dict] | None = None,
) -> Iterator[str]:
    headers = chat_headers(
        from_source,
        user_id,
        developer,
        x_developer,
        accept="text/event-stream",
    )

    payload: dict = {
        "session_id": session_id,
        "query": query,
        "model": model_id or None,
        "user_defined_on_demand_skill_ids": user_defined_on_demand_skill_ids,
        "states": states or None,
    }

    with httpx.stream(
        "POST",
        completions_url.strip(),
        json=payload,
        headers=headers,
        timeout=None,
    ) as response:
        response.raise_for_status()

        buffer = ""
        for chunk in response.iter_text():
            if not chunk:
                continue
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                if frame.strip():
                    yield frame + "\n\n"

        if buffer.strip():
            yield buffer


def create_session(
    completions_url: str,
    title: str,
    from_source: str,
    user_id: str,
    developer: str,
    x_developer: str,
) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            chat_session_url(completions_url, "createSession"),
            json={"title": title},
            headers=chat_headers(from_source, user_id, developer, x_developer),
        )
        response.raise_for_status()
        return response.json()


def delete_session(
    completions_url: str,
    session_id: str,
    from_source: str,
    user_id: str,
    developer: str,
    x_developer: str,
) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            chat_session_url(completions_url, "deleteSession"),
            params={"session_id": session_id},
            headers=chat_headers(from_source, user_id, developer, x_developer),
        )
        response.raise_for_status()
        if not response.text:
            return {"status_code": response.status_code}
        return response.json()


def chat_session_url(completions_url: str, action: str) -> str:
    parsed = urlsplit(completions_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/completions"):
        base_path = path[: -len("/completions")]
    else:
        base_path = path
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            base_path.rstrip("/") + f"/session/{action}",
            "",
            "",
        )
    )
