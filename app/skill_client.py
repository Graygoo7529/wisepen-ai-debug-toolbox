from typing import Any

import httpx


def skill_headers(
    from_source: str,
    user_id: str,
    identity_type: str,
    group_role_map: str,
    developer: str,
    x_developer: str,
) -> dict[str, str]:
    return {
        "X-From-Source": from_source.strip(),
        "X-User-Id": user_id.strip(),
        "X-Identity-Type": identity_type.strip(),
        "X-Group-Role-Map": group_role_map.strip() or "{}",
        "developer": developer.strip(),
        "X-Developer": x_developer.strip(),
        "Content-Type": "application/json",
    }


class SkillClient:
    def __init__(
        self,
        base_url: str,
        from_source: str,
        user_id: str,
        identity_type: str,
        group_role_map: str,
        developer: str,
        x_developer: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = skill_headers(
            from_source,
            user_id,
            identity_type,
            group_role_map,
            developer,
            x_developer,
        )

    def create_skill(
        self,
        title: str,
        name: str,
        description: str,
        source_type: str = "MANUAL",
    ) -> dict[str, Any]:
        return self._post_json(
            "/skill/createSkill",
            {
                "title": title,
                "name": name,
                "description": description,
                "sourceType": source_type,
            },
        )

    def init_upload_skill_assets(
        self,
        resource_id: str,
        draft_version: int,
        skill_md_size: int,
        references: list[dict[str, Any]],
    ) -> dict[str, Any]:
        assets = [
            {
                "name": "SKILL.md",
                "path": "/",
                "skillAssetResourceType": "MD",
                "expectedSize": skill_md_size,
            }
        ]
        assets.extend(references)
        return self._post_json(
            "/skill/initUploadSkillAssets",
            {
                "resourceId": resource_id,
                "draftVersion": draft_version,
                "assets": assets,
            },
        )

    def get_skill_version_info(
        self,
        resource_id: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"resourceId": resource_id}
        if version is not None:
            params["version"] = version
        return self._post_json("/skill/getSkillVersionBundleInfo", params=params)

    def get_skill_info(self, resource_id: str) -> dict[str, Any]:
        return self._post_json("/skill/getSkillInfo", params={"resourceId": resource_id})

    def publish_skill_version(self, resource_id: str) -> dict[str, Any]:
        return self._post_json("/skill/publishSkillVersion", {"resourceId": resource_id})

    def init_personal_tag_tree(self, resource_base_url: str) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                resource_base_url.rstrip("/") + "/resource/tag/getTagTree",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    def put_asset(self, put_url: str, callback_header: str, content: bytes) -> dict[str, Any] | str:
        headers = {
            "Content-Type": "application/octet-stream",
            "x-oss-callback": callback_header,
        }
        with httpx.Client(timeout=60.0) as client:
            response = client.put(put_url, content=content, headers=headers)
            response.raise_for_status()
            text = response.text
            if not text:
                return {"status_code": response.status_code}
            try:
                return response.json()
            except ValueError:
                return text

    def _post_json(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                self.base_url + path,
                json=body,
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()
