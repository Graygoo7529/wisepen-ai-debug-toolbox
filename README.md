# WisePen Debug Toolbox

Standalone Tkinter client for debugging WisePen AI chat flows and Skill asset operations.

The toolbox is intentionally non-invasive: it does not embed server code and only talks to existing HTTP endpoints, similar to a lightweight local Apifox-style client with stateful UI panels.

## Features

- Send `POST /chat/completions` requests and inspect Vercel AI SDK style SSE events.
- Create and delete chat sessions through `/chat/session/createSession` and `/chat/session/deleteSession`.
- Persist local query history and reload related conversation, tool calls, and tool-call details.
- Edit `user_defined_on_demand_skill_ids` for AI chat requests.
- Manage multiple Skill records locally by `title` and `resourceId`.
- Edit local `SKILL.md` and reference assets before uploading.
- Run the Skill flow against the AI asset service:

```text
GET  /resource/tag/getTagTree
POST /skill/createSkill
POST /skill/getSkillInfo
POST /skill/initUploadSkillAssets
PUT  selected asset upload URL
POST /skill/getSkillVersionBundleInfo?resourceId=...&version=1
POST /skill/publishSkillVersion
POST /skill/getSkillVersionBundleInfo?resourceId=...
```

## Requirements

- Python 3.11+ with Tkinter available
- `httpx`

The project has been used with a Conda environment named `tkinter`, but any Python environment with Tkinter should work.

## Run

```powershell
conda activate tkinter
pip install -r requirements.txt
python -m app.main
```

## Runtime Files

The app creates local runtime files in the project directory:

```text
debug_toolbox_config.json
debug_traces.sqlite3
```

They store local UI configuration, Skill records, query history, and raw debug traces. These files may contain internal URLs, user IDs, headers, queries, and model/tool output. They are ignored by `.gitignore` and should not be committed.

## Configuration

The first launch starts with neutral defaults for identity-sensitive fields. Fill them in from the UI:

- `Completions URL`, for example `http://127.0.0.1:8000/chat/completions`
- `Session ID`
- `Model ID`
- `X-From-Source`
- `X-User-Id`
- `developer`
- `X-Developer`
- `user_defined_on_demand_skill_ids`
- Skill service URL, for example `http://localhost:19910`
- Resource service URL, for example `http://localhost:19905`
- `X-Identity-Type`
- `X-Group-Role-Map`

The current values are saved automatically to `debug_toolbox_config.json`.

## Chat Debug

The Chat panel sends chat completion requests, parses streamed SSE frames, and reduces them into:

- conversation text
- tool calls
- selected tool-call input/output details
- raw events
- timing/status information

The left history column is local debug history. Deleting or clearing it does not delete server-side chat sessions.

## Skill Ops

The Skill panel targets the WisePen AI asset and resource services.

Typical flow:

1. Fill user/service headers and URLs.
2. Click `Init Personal Space` to call `/resource/tag/getTagTree`.
3. Fill Skill metadata and click `Create Skill`.
4. Edit `SKILL.md` and reference assets in the asset editor.
5. Click `Init Upload`.
6. Select each asset and click `PUT Selected Asset`.
7. Click `Query Draft`.
8. Click `Publish`.
9. Click `Query Published`.

`Save Skill Record` only saves the current Skill metadata and edited asset content locally. It does not call the backend.
