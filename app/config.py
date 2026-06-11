import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    default_completions_url: str = "http://127.0.0.1:8000/chat/completions"
    default_skill_base_url: str = "http://localhost:19910"
    default_resource_base_url: str = "http://localhost:19905"
    default_from_source: str = ""
    default_user_id: str = ""
    default_identity_type: str = "1"
    default_group_role_map: str = "{}"
    default_developer: str = ""
    default_x_developer: str = ""
    db_path: Path = Path("debug_traces.sqlite3")
    user_config_path: Path = Path("debug_toolbox_config.json")


@dataclass(frozen=True)
class UserSettings:
    completions_url: str
    session_id: str
    model_id: str
    from_source: str
    user_id: str
    user_defined_on_demand_skill_ids: str
    skill_base_url: str
    resource_base_url: str
    identity_type: str
    group_role_map: str
    developer: str
    x_developer: str
    skill_resource_id: str
    skill_records: list[dict]


def load_user_settings(config: AppConfig) -> UserSettings:
    if not config.user_config_path.exists():
        return UserSettings(
            completions_url=config.default_completions_url,
            session_id="",
            model_id="",
            from_source=config.default_from_source,
            user_id=config.default_user_id,
            user_defined_on_demand_skill_ids="[]",
            skill_base_url=config.default_skill_base_url,
            resource_base_url=config.default_resource_base_url,
            identity_type=config.default_identity_type,
            group_role_map=config.default_group_role_map,
            developer=config.default_developer,
            x_developer=config.default_x_developer,
            skill_resource_id="",
            skill_records=[],
        )

    try:
        data = json.loads(config.user_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserSettings(
            completions_url=config.default_completions_url,
            session_id="",
            model_id="",
            from_source=config.default_from_source,
            user_id=config.default_user_id,
            user_defined_on_demand_skill_ids="[]",
            skill_base_url=config.default_skill_base_url,
            resource_base_url=config.default_resource_base_url,
            identity_type=config.default_identity_type,
            group_role_map=config.default_group_role_map,
            developer=config.default_developer,
            x_developer=config.default_x_developer,
            skill_resource_id="",
            skill_records=[],
        )

    completions_url = str(
        data.get("completions_url")
        or _legacy_completions_url(data.get("base_url"))
        or config.default_completions_url
    ).strip()
    session_id = str(data.get("session_id") or "").strip()
    model_id = str(data.get("model_id") or "").strip()
    from_source = str(data.get("from_source") or config.default_from_source).strip()
    user_id = str(data.get("user_id") or config.default_user_id).strip()
    user_defined_on_demand_skill_ids = _skill_ids_to_text(
        data.get("user_defined_on_demand_skill_ids")
    )
    skill_base_url = str(data.get("skill_base_url") or config.default_skill_base_url).strip()
    resource_base_url = str(data.get("resource_base_url") or config.default_resource_base_url).strip()
    identity_type = str(data.get("identity_type") or config.default_identity_type).strip()
    group_role_map = str(data.get("group_role_map") or config.default_group_role_map).strip()
    developer = str(
        data.get("developer") or data.get("developer_enable") or config.default_developer
    ).strip()
    x_developer = str(
        data.get("x_developer") or data.get("developer_name") or config.default_x_developer
    ).strip()
    skill_resource_id = str(data.get("skill_resource_id") or "").strip()
    skill_records = _skill_records(data.get("skill_records"))
    return UserSettings(
        completions_url=completions_url,
        session_id=session_id,
        model_id=model_id,
        from_source=from_source,
        user_id=user_id,
        user_defined_on_demand_skill_ids=user_defined_on_demand_skill_ids,
        skill_base_url=skill_base_url,
        resource_base_url=resource_base_url,
        identity_type=identity_type,
        group_role_map=group_role_map,
        developer=developer,
        x_developer=x_developer,
        skill_resource_id=skill_resource_id,
        skill_records=skill_records,
    )


def save_user_settings(config: AppConfig, settings: UserSettings) -> None:
    data = {
        "completions_url": settings.completions_url.strip(),
        "session_id": settings.session_id.strip(),
        "model_id": settings.model_id.strip(),
        "from_source": settings.from_source.strip(),
        "user_id": settings.user_id.strip(),
        "user_defined_on_demand_skill_ids": _skill_ids_from_text(
            settings.user_defined_on_demand_skill_ids
        ),
        "skill_base_url": settings.skill_base_url.strip(),
        "resource_base_url": settings.resource_base_url.strip(),
        "identity_type": settings.identity_type.strip(),
        "group_role_map": settings.group_role_map.strip(),
        "developer": settings.developer.strip(),
        "x_developer": settings.x_developer.strip(),
        "skill_resource_id": settings.skill_resource_id.strip(),
        "skill_records": _skill_records(settings.skill_records),
    }
    config.user_config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _legacy_completions_url(base_url: object) -> str | None:
    if not base_url:
        return None
    url = str(base_url).strip().rstrip("/")
    if not url:
        return None
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _skill_ids_to_text(value: object) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "[]"


def _skill_ids_from_text(value: str) -> list:
    try:
        parsed = json.loads(value.strip() or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _skill_records(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    records: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        resource_id = str(item.get("resource_id") or item.get("resourceId") or "").strip()
        title = str(item.get("title") or "").strip()
        if not resource_id:
            continue
        records.append(
            {
                "resource_id": resource_id,
                "title": title or resource_id,
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "assets": _skill_assets(item.get("assets")),
            }
        )
    return records


def _skill_assets(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    assets: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        path = str(item.get("path") or "").strip()
        if not name:
            continue
        assets.append(
            {
                "name": name,
                "path": path or ("/" if name == "SKILL.md" else "/references"),
                "skillAssetResourceType": str(item.get("skillAssetResourceType") or "MD").strip(),
                "content": str(item.get("content") or ""),
            }
        )
    return assets
