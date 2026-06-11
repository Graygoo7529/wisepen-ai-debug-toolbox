import json
import queue
import threading
import uuid
from datetime import datetime, timezone
from tkinter import END, BOTH, HORIZONTAL, LEFT, RIGHT, VERTICAL, X, StringVar, Tk, Text
from tkinter import messagebox, simpledialog
from tkinter import ttk
from typing import Any

from app.chat_client import create_session, delete_session, stream_chat_completions
from app.config import AppConfig, UserSettings, load_user_settings, save_user_settings
from app.event_reducer import apply_event
from app.models import DebugEvent, ToolCallView, TurnView
from app.skill_client import SkillClient
from app.sse_parser import parse_sse_frame
from app.trace_store import TraceStore


class AppWindow:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("WisePen Debug Toolbox")
        self.root.geometry("1280x780")
        self.config = AppConfig()
        self.user_settings = load_user_settings(self.config)
        self.store = TraceStore(self.config.db_path)
        self.event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.current_turn: TurnView | None = None
        self.current_tool_call_id: str | None = None
        self.history_turns: dict[str, dict[str, Any]] = {}
        self.seq = 0
        self.worker_thread: threading.Thread | None = None
        self.current_request: dict[str, Any] | None = None
        self.skill_upload_state: dict[str, dict[str, Any]] = {}
        self.skill_records: list[dict[str, str]] = list(self.user_settings.skill_records)
        self.skill_assets: dict[str, dict[str, Any]] = {}

        self.completions_url_var = StringVar(value=self.user_settings.completions_url)
        self.session_id_var = StringVar(value=self.user_settings.session_id)
        self.model_id_var = StringVar(value=self.user_settings.model_id)
        self.from_source_var = StringVar(value=self.user_settings.from_source)
        self.user_id_var = StringVar(value=self.user_settings.user_id)
        self.user_defined_on_demand_skill_ids_var = StringVar(
            value=self.user_settings.user_defined_on_demand_skill_ids
        )
        self.skill_base_url_var = StringVar(value=self.user_settings.skill_base_url)
        self.resource_base_url_var = StringVar(value=self.user_settings.resource_base_url)
        self.identity_type_var = StringVar(value=self.user_settings.identity_type)
        self.group_role_map_var = StringVar(value=self.user_settings.group_role_map)
        self.developer_var = StringVar(value=self.user_settings.developer)
        self.x_developer_var = StringVar(value=self.user_settings.x_developer)
        self.skill_resource_id_var = StringVar(value=self.user_settings.skill_resource_id)
        self.skill_title_var = StringVar(value="AI 调试 Skill")
        self.skill_name_var = StringVar(value="ai-debug-skill")
        self.skill_description_var = StringVar(value="最简 Skill/references 流程")
        self.skill_draft_version_var = StringVar(value="1")
        self.current_asset_key: str | None = None
        self.status_var = StringVar(value="Idle")
        self.tool_status_var = StringVar(value="No turn loaded")

        self._build_layout()
        self._refresh_history()
        self.root.after(50, self._poll_queue)

    def _build_layout(self) -> None:
        self.main_tabs = ttk.Notebook(self.root)
        self.main_tabs.pack(fill=BOTH, expand=True)

        chat_tab = ttk.Frame(self.main_tabs)
        skill_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(chat_tab, text="Chat Debug")
        self.main_tabs.add(skill_tab, text="Skill Ops")

        self._build_chat_tab(chat_tab)
        self._build_skill_tab(skill_tab)

    def _build_chat_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        toolbar.pack(fill=X)

        self._add_labeled_entry(toolbar, "Completions URL", self.completions_url_var, 48)
        self._add_labeled_entry(toolbar, "Session ID", self.session_id_var, 32)
        self._add_labeled_entry(toolbar, "Model ID", self.model_id_var, 28)
        ttk.Button(toolbar, text="New Session", command=self._create_chat_session).pack(side=LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Delete Session", command=self._delete_chat_session).pack(side=LEFT)

        header_bar = ttk.Frame(parent, padding=(8, 0, 8, 4))
        header_bar.pack(fill=X)
        self._add_labeled_entry(header_bar, "X-From-Source", self.from_source_var, 32)
        self._add_labeled_entry(header_bar, "X-User-Id", self.user_id_var, 24)
        self._add_labeled_entry(header_bar, "developer", self.developer_var, 10)
        self._add_labeled_entry(header_bar, "X-Developer", self.x_developer_var, 18)

        skill_bar = ttk.Frame(parent, padding=(8, 0, 8, 4))
        skill_bar.pack(fill=X)
        self._add_labeled_entry(
            skill_bar,
            "user_defined_on_demand_skill_ids",
            self.user_defined_on_demand_skill_ids_var,
            72,
        )

        query_bar = ttk.Frame(parent, padding=(8, 0, 8, 8))
        query_bar.pack(fill=X)
        ttk.Label(query_bar, text="Query").pack(side=LEFT)
        self.query_text = Text(query_bar, height=3, wrap="word")
        self.query_text.pack(side=LEFT, fill=X, expand=True, padx=(6, 8))
        self.send_button = ttk.Button(query_bar, text="Send", command=self._send)
        self.send_button.pack(side=RIGHT)

        panes = ttk.PanedWindow(parent, orient=HORIZONTAL)
        panes.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(panes)
        middle = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=2)
        panes.add(middle, weight=2)
        panes.add(right, weight=3)

        self._build_left_panel(left)
        self._build_middle_panel(middle)
        self._build_right_panel(right)

        status = ttk.Frame(parent, padding=(8, 0, 8, 8))
        status.pack(fill=X)
        ttk.Label(status, textvariable=self.status_var).pack(side=LEFT)

    def _build_skill_tab(self, parent: ttk.Frame) -> None:
        top = ttk.PanedWindow(parent, orient=HORIZONTAL)
        top.pack(fill=X, padx=8, pady=(8, 4))

        config_frame = ttk.Frame(top)
        meta_frame = ttk.Frame(top)
        log_frame = ttk.Frame(top)
        top.add(config_frame, weight=2)
        top.add(meta_frame, weight=2)
        top.add(log_frame, weight=3)

        self._build_skill_config_panel(config_frame)
        self._build_skill_meta_panel(meta_frame)
        self._build_skill_log_panel(log_frame)

        action_bar = ttk.Frame(parent, padding=(8, 0, 8, 8))
        action_bar.pack(fill=X)
        ttk.Button(action_bar, text="Create Skill", command=self._skill_create).pack(side=LEFT, padx=(0, 6))
        ttk.Button(action_bar, text="Save Skill Record", command=self._skill_save_record).pack(side=LEFT, padx=(0, 12))
        ttk.Button(action_bar, text="Init Upload", command=self._skill_init_upload).pack(side=LEFT, padx=(0, 6))
        ttk.Button(action_bar, text="PUT Selected Asset", command=self._skill_put_selected_asset).pack(side=LEFT, padx=(0, 6))
        ttk.Button(action_bar, text="Query Skill Info", command=self._skill_query_info).pack(side=LEFT, padx=(0, 6))
        ttk.Button(action_bar, text="Query Draft", command=lambda: self._skill_query(published=False)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(action_bar, text="Publish", command=self._skill_publish).pack(side=LEFT, padx=(0, 6))
        ttk.Button(action_bar, text="Query Published", command=lambda: self._skill_query(published=True)).pack(side=LEFT, padx=(0, 6))

        panes = ttk.PanedWindow(parent, orient=HORIZONTAL)
        panes.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))

        skill_left = ttk.Frame(panes)
        skill_middle = ttk.Frame(panes)
        skill_right = ttk.Frame(panes)
        panes.add(skill_left, weight=2)
        panes.add(skill_middle, weight=2)
        panes.add(skill_right, weight=3)

        self._build_skill_records_panel(skill_left)
        self._build_skill_assets_panel(skill_middle)
        self._build_skill_detail_panel(skill_right)
        self._refresh_skill_records()
        self._ensure_default_assets()
        self._refresh_skill_asset_tree()

    def _build_skill_config_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="User / Service").pack(anchor="w")
        row1 = ttk.Frame(parent)
        row1.pack(fill=X)
        self._add_labeled_entry(row1, "Skill URL", self.skill_base_url_var, 26)
        self._add_labeled_entry(row1, "Resource URL", self.resource_base_url_var, 26)
        row2 = ttk.Frame(parent)
        row2.pack(fill=X)
        self._add_labeled_entry(row2, "X-User-Id", self.user_id_var, 24)
        self._add_labeled_entry(row2, "X-Identity-Type", self.identity_type_var, 8)
        row3 = ttk.Frame(parent)
        row3.pack(fill=X)
        self._add_labeled_entry(row3, "X-From-Source", self.from_source_var, 26)
        self._add_labeled_entry(row3, "X-Group-Role-Map", self.group_role_map_var, 16)
        row4 = ttk.Frame(parent)
        row4.pack(fill=X)
        self._add_labeled_entry(row4, "developer", self.developer_var, 10)
        self._add_labeled_entry(row4, "X-Developer", self.x_developer_var, 18)
        ttk.Button(parent, text="Init Personal Space", command=self._skill_init_personal_space).pack(anchor="w", pady=(4, 0))

    def _build_skill_meta_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Skill Meta").pack(anchor="w")
        row1 = ttk.Frame(parent)
        row1.pack(fill=X)
        self._add_labeled_entry(row1, "Resource ID", self.skill_resource_id_var, 34)
        self._add_labeled_entry(row1, "Draft", self.skill_draft_version_var, 6)
        row2 = ttk.Frame(parent)
        row2.pack(fill=X)
        self._add_labeled_entry(row2, "Title", self.skill_title_var, 24)
        self._add_labeled_entry(row2, "Name", self.skill_name_var, 24)
        row3 = ttk.Frame(parent)
        row3.pack(fill=X)
        self._add_labeled_entry(row3, "Description", self.skill_description_var, 56)

    def _build_skill_log_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="HTTP Log").pack(anchor="w")
        self.skill_log_text = Text(parent, wrap="word", height=8)
        self.skill_log_text.pack(fill=BOTH, expand=True)

    def _add_labeled_entry(
        self,
        parent: ttk.Frame,
        label: str,
        variable: StringVar,
        width: int,
        show: str | None = None,
    ) -> None:
        frame = ttk.Frame(parent)
        frame.pack(side=LEFT, padx=(0, 8))
        ttk.Label(frame, text=label).pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=variable, width=width, show=show)
        entry.pack()

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        history_header = ttk.Frame(parent)
        history_header.pack(fill=X)
        ttk.Label(history_header, text="Query History").pack(side=LEFT)
        ttk.Button(history_header, text="Load", command=self._load_selected_history).pack(side=RIGHT)
        ttk.Button(history_header, text="Copy", command=self._copy_selected_history).pack(side=RIGHT, padx=(0, 4))
        ttk.Button(history_header, text="Delete", command=self._delete_selected_history).pack(side=RIGHT, padx=(0, 4))
        ttk.Button(history_header, text="Clear", command=self._clear_history).pack(side=RIGHT, padx=(0, 4))

        columns = ("status", "query")
        self.history_tree = ttk.Treeview(parent, columns=columns, show="headings", height=8, selectmode="browse")
        self.history_tree.heading("status", text="Status")
        self.history_tree.heading("query", text="Query")
        self.history_tree.column("status", width=80, anchor="center")
        self.history_tree.column("query", width=260)
        self.history_tree.pack(fill=X, pady=(2, 8))
        self.history_tree.bind("<<TreeviewSelect>>", self._on_history_selected)

        ttk.Label(parent, text="Conversation").pack(anchor="w")
        self.conversation_text = Text(parent, wrap="word", height=10)
        self.conversation_text.pack(fill=BOTH, expand=True)

    def _build_middle_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Tool Calls").pack(anchor="w")
        ttk.Label(parent, textvariable=self.tool_status_var).pack(anchor="w")
        columns = ("step", "tool", "status", "duration")
        self.tool_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        self.tool_tree.heading("step", text="Step")
        self.tool_tree.heading("tool", text="Tool")
        self.tool_tree.heading("status", text="Status")
        self.tool_tree.heading("duration", text="Duration")
        self.tool_tree.column("step", width=52, anchor="center")
        self.tool_tree.column("tool", width=180)
        self.tool_tree.column("status", width=90, anchor="center")
        self.tool_tree.column("duration", width=90, anchor="center")
        self.tool_tree.pack(fill=BOTH, expand=True)
        self.tool_tree.bind("<<TreeviewSelect>>", self._on_tool_selected)

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Tool Call Detail").pack(anchor="w")
        self.detail_tabs = ttk.Notebook(parent)
        self.detail_tabs.pack(fill=BOTH, expand=True)

        self.input_text = self._add_text_tab("Input")
        self.output_text = self._add_text_tab("Observed Output")
        self.raw_text = self._add_text_tab("Raw SSE")
        self.timing_text = self._add_text_tab("Timing")

    def _add_text_tab(self, title: str) -> Text:
        frame = ttk.Frame(self.detail_tabs)
        text = Text(frame, wrap="none")
        y_scroll = ttk.Scrollbar(frame, orient=VERTICAL, command=text.yview)
        x_scroll = ttk.Scrollbar(frame, orient=HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.detail_tabs.add(frame, text=title)
        return text

    def _add_text_tab_to(self, notebook: ttk.Notebook, title: str) -> Text:
        frame = ttk.Frame(notebook)
        text = Text(frame, wrap="none")
        y_scroll = ttk.Scrollbar(frame, orient=VERTICAL, command=text.yview)
        x_scroll = ttk.Scrollbar(frame, orient=HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        notebook.add(frame, text=title)
        return text

    def _build_skill_records_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill=X)
        ttk.Label(header, text="Skill Records").pack(side=LEFT)
        ttk.Button(header, text="Load", command=self._load_selected_skill_record).pack(side=RIGHT)
        ttk.Button(header, text="Copy ID", command=self._copy_selected_skill_id).pack(side=RIGHT, padx=(0, 4))
        ttk.Button(header, text="Delete", command=self._delete_selected_skill_record).pack(side=RIGHT, padx=(0, 4))

        columns = ("title", "resource_id")
        self.skill_record_tree = ttk.Treeview(parent, columns=columns, show="headings", height=10, selectmode="browse")
        self.skill_record_tree.heading("title", text="Title")
        self.skill_record_tree.heading("resource_id", text="Resource ID")
        self.skill_record_tree.column("title", width=150)
        self.skill_record_tree.column("resource_id", width=220)
        self.skill_record_tree.pack(fill=BOTH, expand=True, pady=(2, 0))
        self.skill_record_tree.bind("<<TreeviewSelect>>", self._on_skill_record_selected)

    def _build_skill_assets_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill=X)
        ttk.Label(header, text="Assets").pack(side=LEFT)
        ttk.Button(header, text="Add Reference", command=self._skill_add_reference).pack(side=RIGHT)
        columns = ("kind", "path", "status")
        self.skill_asset_tree = ttk.Treeview(parent, columns=columns, show="headings", height=10, selectmode="browse")
        self.skill_asset_tree.heading("kind", text="Kind")
        self.skill_asset_tree.heading("path", text="Path")
        self.skill_asset_tree.heading("status", text="Status")
        self.skill_asset_tree.column("kind", width=88)
        self.skill_asset_tree.column("path", width=180)
        self.skill_asset_tree.column("status", width=100)
        self.skill_asset_tree.pack(fill=BOTH, expand=True)
        self.skill_asset_tree.bind("<<TreeviewSelect>>", self._on_skill_asset_selected)

    def _build_skill_detail_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill=X)
        ttk.Label(header, text="Asset Editor").pack(side=LEFT)
        ttk.Button(header, text="Save Asset Content", command=self._skill_save_current_asset).pack(side=RIGHT)
        self.skill_detail_tabs = ttk.Notebook(parent)
        self.skill_detail_tabs.pack(fill=BOTH, expand=True)
        self.skill_preview_text = self._add_text_tab_to(self.skill_detail_tabs, "Content")
        self.skill_json_text = self._add_text_tab_to(self.skill_detail_tabs, "Version JSON")

    def _create_chat_session(self) -> None:
        title = simpledialog.askstring(
            "New Session",
            "Input session title:",
            initialvalue="New Chat",
            parent=self.root,
        )
        if title is None:
            return
        title = title.strip() or "New Chat"
        try:
            response = create_session(
                completions_url=self.completions_url_var.get(),
                title=title,
                from_source=self.from_source_var.get(),
                user_id=self.user_id_var.get(),
                developer=self.developer_var.get(),
                x_developer=self.x_developer_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Create session failed", str(exc))
            self.status_var.set(f"Create session failed: {exc}")
            return

        session_id = _extract_session_id(response)
        if not session_id:
            messagebox.showerror("Create session failed", f"Missing session id in response:\n{_pretty(response)}")
            self.status_var.set("Create session failed: missing session id")
            return

        self.session_id_var.set(session_id)
        self._save_current_settings()
        self.status_var.set(f"Created session: {session_id}")

    def _delete_chat_session(self) -> None:
        session_id = self.session_id_var.get().strip()
        if not session_id:
            messagebox.showerror("Missing field", "Session ID is required.")
            return
        if not messagebox.askyesno(
            "Delete session",
            f"Delete current session?\n\n{session_id}",
        ):
            return
        try:
            delete_session(
                completions_url=self.completions_url_var.get(),
                session_id=session_id,
                from_source=self.from_source_var.get(),
                user_id=self.user_id_var.get(),
                developer=self.developer_var.get(),
                x_developer=self.x_developer_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Delete session failed", str(exc))
            self.status_var.set(f"Delete session failed: {exc}")
            return

        self.session_id_var.set("")
        self._save_current_settings()
        self.status_var.set(f"Deleted session: {session_id}")

    def _send(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Request running", "A request is already running.")
            return

        session_id = self.session_id_var.get().strip()
        query = self.query_text.get("1.0", END).strip()
        model_id = self.model_id_var.get().strip()
        completions_url = self.completions_url_var.get().strip()
        from_source = self.from_source_var.get().strip()
        user_id = self.user_id_var.get().strip()
        developer = self.developer_var.get().strip()
        x_developer = self.x_developer_var.get().strip()
        try:
            user_defined_on_demand_skill_ids = _parse_skill_ids(
                self.user_defined_on_demand_skill_ids_var.get()
            )
        except ValueError as exc:
            messagebox.showerror("Invalid user_defined_on_demand_skill_ids", str(exc))
            return

        if not session_id:
            messagebox.showerror("Missing field", "Session ID is required.")
            return
        if not query:
            messagebox.showerror("Missing field", "Query is required.")
            return
        if not model_id:
            messagebox.showerror("Missing field", "Model ID is required.")
            return
        self._save_current_settings()
        self.current_request = {
            "completions_url": completions_url,
            "session_id": session_id,
            "query": query,
            "model_id": model_id,
            "from_source": from_source,
            "user_id": user_id,
            "developer": developer,
            "x_developer": x_developer,
            "user_defined_on_demand_skill_ids": user_defined_on_demand_skill_ids,
        }

        self.current_turn = TurnView(
            turn_id=f"turn_{uuid.uuid4().hex}",
            session_id=session_id,
            query=query,
        )
        self.current_tool_call_id = None
        self.seq = 0
        self._clear_tool_details()
        self._refresh_all()

        self.store.create_turn(
            self.current_turn,
            model_id=model_id,
        )
        self._refresh_history(select_turn_id=self.current_turn.turn_id)

        self.send_button.configure(state="disabled")
        self.status_var.set("Streaming...")
        self.worker_thread = threading.Thread(
            target=self._worker_stream,
            args=(self.current_request,),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker_stream(self, request: dict[str, Any]) -> None:
        try:
            for frame in stream_chat_completions(
                completions_url=request["completions_url"],
                session_id=request["session_id"],
                query=request["query"],
                model_id=request["model_id"],
                from_source=request["from_source"],
                user_id=request["user_id"],
                developer=request["developer"],
                x_developer=request["x_developer"],
                user_defined_on_demand_skill_ids=request["user_defined_on_demand_skill_ids"],
            ):
                self.event_queue.put({"kind": "frame", "frame": frame})
        except Exception as exc:
            self.event_queue.put({"kind": "exception", "error": str(exc)})
        finally:
            self.event_queue.put({"kind": "worker_done"})

    def _poll_queue(self) -> None:
        handled = False
        while True:
            try:
                item = self.event_queue.get_nowait()
            except queue.Empty:
                break
            handled = True
            self._handle_queue_item(item)

        if handled:
            self._refresh_all()
        self.root.after(50, self._poll_queue)

    def _handle_queue_item(self, item: dict[str, Any]) -> None:
        if self.current_turn is None:
            return

        kind = item.get("kind")
        if kind == "frame":
            raw_frame = item["frame"]
            payload = parse_sse_frame(raw_frame)
            if payload is None:
                return

            self.seq += 1
            event_type = payload.get("type", "unknown") if isinstance(payload, dict) else "raw"
            event = DebugEvent(
                seq=self.seq,
                event_type=event_type,
                payload=payload,
                raw_frame=raw_frame,
                received_at=datetime.now(timezone.utc),
            )
            apply_event(self.current_turn, event)
            self.store.save_event(self.current_turn.turn_id, event)
            return

        if kind == "exception":
            self.current_turn.status = "error"
            self.current_turn.error = item.get("error", "Unknown error")
            self.status_var.set(f"Error: {self.current_turn.error}")
            self.store.finish_turn(self.current_turn)
            return

        if kind == "worker_done":
            if self.current_turn.status == "running":
                self.current_turn.status = "finished"
            self.store.finish_turn(self.current_turn)
            self._refresh_history(select_turn_id=self.current_turn.turn_id)
            self.status_var.set(f"Done: {self.current_turn.status}")
            self.send_button.configure(state="normal")

    def _refresh_all(self) -> None:
        self._refresh_conversation()
        self._refresh_tool_tree()
        self._refresh_selected_tool()

    def _refresh_history(self, select_turn_id: str | None = None) -> None:
        if not hasattr(self, "history_tree"):
            return
        selected = select_turn_id or self._selected_history_turn_id()
        self.history_turns = {}
        for item_id in self.history_tree.get_children():
            self.history_tree.delete(item_id)

        for row in self.store.list_turns():
            turn_id = row["id"]
            self.history_turns[turn_id] = row
            self.history_tree.insert(
                "",
                END,
                iid=turn_id,
                values=(row.get("status") or "", _preview(row.get("query") or "")),
            )

        if selected and selected in self.history_turns:
            self.history_tree.selection_set(selected)

    def _refresh_conversation(self) -> None:
        self.conversation_text.delete("1.0", END)
        if self.current_turn is None:
            return

        turn = self.current_turn
        self.conversation_text.insert(END, f"Turn: {turn.turn_id}\n")
        self.conversation_text.insert(END, f"Session: {turn.session_id}\n")
        self.conversation_text.insert(END, f"Status: {turn.status}\n\n")
        self.conversation_text.insert(END, f"User:\n{turn.query}\n\n")
        if turn.reasoning:
            self.conversation_text.insert(END, f"Reasoning:\n{turn.reasoning}\n\n")
        self.conversation_text.insert(END, f"Assistant:\n{turn.text}")
        if turn.error:
            self.conversation_text.insert(END, f"\n\nError:\n{turn.error}")

    def _refresh_tool_tree(self) -> None:
        existing_selection = self.current_tool_call_id
        for item_id in self.tool_tree.get_children():
            self.tool_tree.delete(item_id)

        if self.current_turn is None:
            self.tool_status_var.set("No turn loaded")
            return

        if not self.current_turn.tool_calls:
            self.tool_status_var.set(
                "No tool calls observed. Events: " + _event_summary(self.current_turn)
            )
            return

        self.tool_status_var.set(
            f"{len(self.current_turn.tool_calls)} tool call(s). Events: "
            + _event_summary(self.current_turn)
        )
        for call_id, tool in self.current_turn.tool_calls.items():
            self.tool_tree.insert(
                "",
                END,
                iid=call_id,
                values=(
                    tool.step_index,
                    tool.tool_name or "(unknown)",
                    tool.status,
                    _format_duration(tool),
                ),
            )

        if existing_selection and existing_selection in self.current_turn.tool_calls:
            self.tool_tree.selection_set(existing_selection)

    def _on_tool_selected(self, _event: object) -> None:
        selected = self.tool_tree.selection()
        self.current_tool_call_id = selected[0] if selected else None
        self._refresh_selected_tool()

    def _refresh_selected_tool(self) -> None:
        self._clear_tool_details()
        if self.current_turn is None or not self.current_tool_call_id:
            return
        tool = self.current_turn.tool_calls.get(self.current_tool_call_id)
        if tool is None:
            return

        self._set_text(self.input_text, _pretty(tool.input))
        self._set_text(self.output_text, _pretty(tool.output))
        self._set_text(self.raw_text, "\n".join(event.raw_frame.rstrip() for event in tool.raw_events))
        timing = {
            "call_id": tool.call_id,
            "tool_name": tool.tool_name,
            "step_index": tool.step_index,
            "status": tool.status,
            "started_at": tool.started_at.isoformat() if tool.started_at else None,
            "finished_at": tool.finished_at.isoformat() if tool.finished_at else None,
            "duration": _format_duration(tool),
        }
        self._set_text(self.timing_text, _pretty(timing))

    def _clear_tool_details(self) -> None:
        for widget in (self.input_text, self.output_text, self.raw_text, self.timing_text):
            self._set_text(widget, "")

    def _set_text(self, widget: Text, value: str) -> None:
        widget.delete("1.0", END)
        widget.insert(END, value)

    def _selected_history_turn_id(self) -> str | None:
        if not hasattr(self, "history_tree"):
            return None
        selected = self.history_tree.selection()
        return selected[0] if selected else None

    def _on_history_selected(self, _event: object) -> None:
        turn_id = self._selected_history_turn_id()
        if turn_id:
            self._load_history_turn(turn_id)

    def _load_selected_history(self) -> None:
        turn_id = self._selected_history_turn_id()
        if turn_id:
            self._load_history_turn(turn_id)

    def _load_history_turn(self, turn_id: str) -> None:
        row = self.store.get_turn(turn_id)
        if row is None:
            return

        turn = TurnView(
            turn_id=row["id"],
            session_id=row["session_id"],
            query=row["query"],
            status=row.get("status") or "finished",
            error=row.get("error"),
        )
        for event in self.store.list_events(turn_id):
            apply_event(turn, event)
        turn.status = row.get("status") or turn.status
        turn.error = row.get("error") or turn.error

        self.current_turn = turn
        self.current_tool_call_id = None
        self.seq = max((event.seq for event in turn.events), default=0)
        self._clear_tool_details()
        self._refresh_all()
        self.status_var.set(f"Loaded history: {turn_id}")

    def _copy_selected_history(self) -> None:
        turn_id = self._selected_history_turn_id()
        if not turn_id:
            return
        row = self.history_turns.get(turn_id) or self.store.get_turn(turn_id)
        if row is None:
            return
        query = row.get("query") or ""
        self.root.clipboard_clear()
        self.root.clipboard_append(query)
        self.status_var.set("Copied query to clipboard")

    def _delete_selected_history(self) -> None:
        turn_id = self._selected_history_turn_id()
        if not turn_id:
            return
        if not messagebox.askyesno("Delete history", "Delete the selected query history?"):
            return
        self.store.delete_turn(turn_id)
        if self.current_turn and self.current_turn.turn_id == turn_id:
            self.current_turn = None
            self.current_tool_call_id = None
            self._clear_tool_details()
            self._refresh_all()
        self._refresh_history()
        self.status_var.set("Deleted query history")

    def _clear_history(self) -> None:
        if not messagebox.askyesno("Clear history", "Clear all query history and raw events?"):
            return
        self.store.clear_turns()
        self.current_turn = None
        self.current_tool_call_id = None
        self._clear_tool_details()
        self._refresh_all()
        self._refresh_history()
        self.status_var.set("Cleared query history")

    def _skill_client(self) -> SkillClient:
        self._save_current_settings()
        return SkillClient(
            base_url=self.skill_base_url_var.get(),
            from_source=self.from_source_var.get(),
            user_id=self.user_id_var.get(),
            identity_type=self.identity_type_var.get(),
            group_role_map=self.group_role_map_var.get(),
            developer=self.developer_var.get(),
            x_developer=self.x_developer_var.get(),
        )

    def _skill_init_personal_space(self) -> None:
        try:
            response = self._skill_client().init_personal_tag_tree(self.resource_base_url_var.get())
            self._save_current_settings()
            self._log_skill("resource/tag/getTagTree", response)
        except Exception as exc:
            self._log_skill_error("resource/tag/getTagTree", exc)

    def _skill_create(self) -> None:
        try:
            client = self._skill_client()
            response = client.create_skill(
                title=self.skill_title_var.get().strip(),
                name=self.skill_name_var.get().strip(),
                description=self.skill_description_var.get().strip(),
            )
            resource_id = _response_data(response)
            if isinstance(resource_id, str):
                self.skill_resource_id_var.set(resource_id)
                self._upsert_skill_record(resource_id)
            self._save_current_settings()
            self._refresh_skill_records(select_resource_id=self.skill_resource_id_var.get().strip())
            self._log_skill("createSkill", response)
            self._select_skill_record(self.skill_resource_id_var.get().strip())
        except Exception as exc:
            self._log_skill_error("createSkill", exc)

    def _skill_save_record(self) -> None:
        try:
            resource_id = self._require_skill_resource_id()
            self._upsert_skill_record(resource_id)
            self._save_current_settings()
            self._refresh_skill_records(select_resource_id=resource_id)
            self._log_plain(f"Saved skill record: {self.skill_title_var.get().strip()} ({resource_id})")
        except Exception as exc:
            self._log_skill_error("Save Record", exc)

    def _skill_init_upload(self) -> None:
        try:
            resource_id = self._require_skill_resource_id()
            draft_version = int(self.skill_draft_version_var.get().strip() or "1")
            self._skill_save_current_asset()
            skill_md = self._asset_by_key("skill_md")
            if not skill_md:
                raise ValueError("SKILL.md asset is required.")
            references = [asset for key, asset in self.skill_assets.items() if key != "skill_md"]
            client = self._skill_client()
            response = client.init_upload_skill_assets(
                resource_id=resource_id,
                draft_version=draft_version,
                skill_md_size=len(_asset_content_bytes(skill_md)),
                references=[
                    {
                        "name": asset["name"],
                        "path": asset["path"],
                        "skillAssetResourceType": asset.get("skillAssetResourceType") or "MD",
                        "expectedSize": len(_asset_content_bytes(asset)),
                    }
                    for asset in references
                ],
            )
            self.skill_upload_state = _merge_upload_state(self.skill_assets, _extract_upload_state(response))
            self._refresh_skill_assets_from_upload_state()
            self._upsert_skill_record(resource_id)
            self._save_current_settings()
            self._log_skill("initUploadSkillAssets", response)
            if self.skill_upload_state:
                self._log_plain("Parsed upload slots: " + json.dumps(self.skill_upload_state, ensure_ascii=False, indent=2))
        except Exception as exc:
            self._log_skill_error("initUploadSkillAssets", exc)

    def _skill_put_asset(self, asset_key: str) -> None:
        try:
            slot = self.skill_upload_state.get(asset_key)
            if not slot:
                raise ValueError("Run Init Upload first; upload slot is missing.")
            if slot.get("flashUploaded"):
                self._log_plain(f"{asset_key}: flashUploaded=true, PUT skipped.")
                return
            put_url = slot.get("putUrl")
            callback_header = slot.get("callbackHeader")
            if not put_url or not callback_header:
                raise ValueError(f"{asset_key}: putUrl/callbackHeader missing.")

            asset = self._asset_by_key(asset_key)
            if not asset:
                raise ValueError(f"No asset content found for {asset_key}.")
            content = _asset_content_bytes(asset)

            response = self._skill_client().put_asset(put_url, callback_header, content)
            self._log_skill(f"PUT {asset_key}", response)
        except Exception as exc:
            self._log_skill_error(f"PUT {asset_key}", exc)

    def _skill_put_selected_asset(self) -> None:
        selected = self.skill_asset_tree.selection() if hasattr(self, "skill_asset_tree") else ()
        if not selected:
            messagebox.showinfo("No asset selected", "Select an asset from the Assets list first.")
            return
        self._skill_put_asset(selected[0])

    def _skill_query(self, published: bool) -> None:
        try:
            resource_id = self._require_skill_resource_id()
            version = None if published else int(self.skill_draft_version_var.get().strip() or "1")
            response = self._skill_client().get_skill_version_info(resource_id, version=version)
            self._save_current_settings()
            self._refresh_skill_assets_from_version(response)
            self._set_text(self.skill_json_text, _pretty(response))
            self._log_skill(
                "getSkillVersionBundleInfo published" if published else "getSkillVersionBundleInfo draft",
                response,
            )
        except Exception as exc:
            self._log_skill_error("getSkillVersionBundleInfo", exc)

    def _skill_query_info(self) -> None:
        try:
            resource_id = self._require_skill_resource_id()
            response = self._skill_client().get_skill_info(resource_id)
            self._save_current_settings()
            self._set_text(self.skill_json_text, _pretty(response))
            self._log_skill("getSkillInfo", response)
        except Exception as exc:
            self._log_skill_error("getSkillInfo", exc)

    def _skill_publish(self) -> None:
        try:
            resource_id = self._require_skill_resource_id()
            response = self._skill_client().publish_skill_version(resource_id)
            self._save_current_settings()
            self._log_skill("publishSkillVersion", response)
        except Exception as exc:
            self._log_skill_error("publishSkillVersion", exc)

    def _require_skill_resource_id(self) -> str:
        resource_id = self.skill_resource_id_var.get().strip()
        if not resource_id:
            raise ValueError("Resource ID is required. Run Create first or paste an existing resourceId.")
        return resource_id

    def _upsert_skill_record(self, resource_id: str) -> None:
        record = {
            "resource_id": resource_id,
            "title": self.skill_title_var.get().strip() or resource_id,
            "name": self.skill_name_var.get().strip(),
            "description": self.skill_description_var.get().strip(),
            "assets": _assets_to_records(self.skill_assets),
        }
        self.skill_records = [r for r in self.skill_records if r.get("resource_id") != resource_id]
        self.skill_records.insert(0, record)

    def _refresh_skill_records(self, select_resource_id: str | None = None) -> None:
        if not hasattr(self, "skill_record_tree"):
            return
        selected = select_resource_id or self._selected_skill_resource_id()
        for item_id in self.skill_record_tree.get_children():
            self.skill_record_tree.delete(item_id)
        for record in self.skill_records:
            resource_id = record.get("resource_id", "")
            self.skill_record_tree.insert(
                "",
                END,
                iid=resource_id,
                values=(_preview(record.get("title", ""), 40), resource_id),
            )
        if selected and any(r.get("resource_id") == selected for r in self.skill_records):
            self.skill_record_tree.selection_set(selected)

    def _select_skill_record(self, resource_id: str) -> None:
        if not resource_id or not hasattr(self, "skill_record_tree"):
            return
        if resource_id in self.skill_record_tree.get_children():
            self.skill_record_tree.selection_set(resource_id)
            self.skill_record_tree.see(resource_id)

    def _selected_skill_resource_id(self) -> str | None:
        if not hasattr(self, "skill_record_tree"):
            return None
        selected = self.skill_record_tree.selection()
        return selected[0] if selected else None

    def _on_skill_record_selected(self, _event: object) -> None:
        self._load_selected_skill_record()

    def _load_selected_skill_record(self) -> None:
        resource_id = self._selected_skill_resource_id()
        if not resource_id:
            return
        record = next((r for r in self.skill_records if r.get("resource_id") == resource_id), None)
        if not record:
            return
        self.skill_resource_id_var.set(resource_id)
        self.skill_title_var.set(record.get("title", ""))
        self.skill_name_var.set(record.get("name", ""))
        self.skill_description_var.set(record.get("description", ""))
        self.skill_assets = _assets_from_records(record.get("assets"))
        self._ensure_default_assets()
        self._refresh_skill_asset_tree()
        self._save_current_settings()
        self._log_plain(f"Loaded skill record: {record.get('title')} ({resource_id})")

    def _copy_selected_skill_id(self) -> None:
        resource_id = self._selected_skill_resource_id()
        if not resource_id:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(resource_id)
        self._log_plain(f"Copied resourceId: {resource_id}")

    def _delete_selected_skill_record(self) -> None:
        resource_id = self._selected_skill_resource_id()
        if not resource_id:
            return
        self.skill_records = [r for r in self.skill_records if r.get("resource_id") != resource_id]
        if self.skill_resource_id_var.get().strip() == resource_id:
            self.skill_resource_id_var.set("")
        self._save_current_settings()
        self._refresh_skill_records()
        self._log_plain(f"Deleted skill record: {resource_id}")

    def _refresh_skill_assets_from_upload_state(self) -> None:
        merged = dict(self.skill_assets)
        for key, item in self.skill_upload_state.items():
            existing = dict(merged.get(key) or {})
            existing.update(item)
            existing["asset_key"] = key
            merged[key] = existing
        self.skill_assets = merged
        self._refresh_skill_asset_tree()

    def _refresh_skill_assets_from_version(self, response: dict[str, Any]) -> None:
        version_assets = _extract_assets_from_version(response)
        merged = dict(self.skill_assets)
        for key, item in version_assets.items():
            existing = dict(merged.get(key) or {})
            existing.update(item)
            existing["asset_key"] = key
            merged[key] = existing
        self.skill_assets = merged
        self._refresh_skill_asset_tree()

    def _refresh_skill_asset_tree(self) -> None:
        if not hasattr(self, "skill_asset_tree"):
            return
        for item_id in self.skill_asset_tree.get_children():
            self.skill_asset_tree.delete(item_id)
        for key, asset in self.skill_assets.items():
            self.skill_asset_tree.insert(
                "",
                END,
                iid=key,
                values=(
                    _asset_kind(asset),
                    asset.get("path") or "",
                    asset.get("uploadStatus") or asset.get("status") or ("flashUploaded" if asset.get("flashUploaded") else ""),
                ),
            )
        if "skill_md" in self.skill_assets:
            self.skill_asset_tree.selection_set("skill_md")
            self.skill_asset_tree.see("skill_md")
            self._on_skill_asset_selected(None)

    def _on_skill_asset_selected(self, _event: object) -> None:
        self._skill_save_current_asset(silent=True)
        selected = self.skill_asset_tree.selection()
        if not selected:
            return
        key = selected[0]
        self.current_asset_key = key
        asset = self.skill_assets.get(key)
        if not asset:
            return
        preview = self._preview_asset(key, asset)
        self._set_text(self.skill_preview_text, preview)
        self._set_text(self.skill_json_text, _pretty(asset))

    def _preview_asset(self, key: str, asset: dict[str, Any]) -> str:
        return str(asset.get("content") or "")

    def _skill_add_reference(self) -> None:
        self._skill_save_current_asset(silent=True)
        index = 1
        while f"reference_{index}" in self.skill_assets:
            index += 1
        key = f"reference_{index}"
        self.skill_assets[key] = {
            "asset_key": key,
            "name": f"refs{index}.md" if index > 1 else "refs.md",
            "path": "/references",
            "skillAssetResourceType": "MD",
            "content": default_refs_md(),
        }
        self._refresh_skill_asset_tree()
        self.skill_asset_tree.selection_set(key)
        self._on_skill_asset_selected(None)

    def _skill_save_current_asset(self, silent: bool = False) -> None:
        key = self.current_asset_key
        if not key or key not in self.skill_assets:
            return
        self.skill_assets[key]["content"] = self.skill_preview_text.get("1.0", END).rstrip()
        if self.skill_resource_id_var.get().strip():
            self._upsert_skill_record(self.skill_resource_id_var.get().strip())
            self._save_current_settings()
        if not silent:
            self._log_plain(f"Saved asset content: {key}")

    def _asset_by_key(self, asset_key: str) -> dict[str, Any] | None:
        return self.skill_assets.get(asset_key)

    def _ensure_default_assets(self) -> None:
        if "skill_md" not in self.skill_assets:
            self.skill_assets["skill_md"] = {
                "asset_key": "skill_md",
                "name": "SKILL.md",
                "path": "/",
                "skillAssetResourceType": "MD",
                "content": default_skill_md(),
            }
        if not any(key != "skill_md" for key in self.skill_assets):
            self.skill_assets["refs"] = {
                "asset_key": "refs",
                "name": "refs.md",
                "path": "/references",
                "skillAssetResourceType": "MD",
                "content": default_refs_md(),
            }

    def _log_skill(self, title: str, payload: Any) -> None:
        self._log_plain(f"\n=== {title} ===\n{_pretty(payload)}\n")

    def _log_skill_error(self, title: str, exc: Exception) -> None:
        self._log_plain(f"\n=== {title} ERROR ===\n{type(exc).__name__}: {exc}\n")

    def _log_plain(self, text: str) -> None:
        self.skill_log_text.insert(END, text + "\n")
        self.skill_log_text.see(END)

    def _save_current_settings(self) -> None:
        try:
            save_user_settings(
                self.config,
                UserSettings(
                    completions_url=self.completions_url_var.get(),
                    session_id=self.session_id_var.get(),
                    model_id=self.model_id_var.get(),
                    from_source=self.from_source_var.get(),
                    user_id=self.user_id_var.get(),
                    user_defined_on_demand_skill_ids=self.user_defined_on_demand_skill_ids_var.get(),
                    skill_base_url=self.skill_base_url_var.get(),
                    resource_base_url=self.resource_base_url_var.get(),
                    identity_type=self.identity_type_var.get(),
                    group_role_map=self.group_role_map_var.get(),
                    developer=self.developer_var.get(),
                    x_developer=self.x_developer_var.get(),
                    skill_resource_id=self.skill_resource_id_var.get(),
                    skill_records=self.skill_records,
                ),
            )
        except OSError as exc:
            self.status_var.set(f"Config save failed: {exc}")


def _pretty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _extract_session_id(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    data = response.get("data")
    if isinstance(data, dict):
        return str(data.get("id") or data.get("session_id") or data.get("sessionId") or "").strip()
    if isinstance(data, str):
        return data.strip()
    return str(response.get("id") or response.get("session_id") or response.get("sessionId") or "").strip()


def _preview(value: str, limit: int = 80) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _event_summary(turn: TurnView) -> str:
    counts: dict[str, int] = {}
    for event in turn.events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    if not counts:
        return "none"
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{name}={count}" for name, count in ordered[:8])


def _parse_skill_ids(value: str) -> list[str]:
    try:
        parsed = json.loads(value.strip() or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("Must be a JSON array, for example: []") from exc
    if not isinstance(parsed, list):
        raise ValueError("Must be a JSON array, for example: []")
    invalid = [item for item in parsed if not isinstance(item, str)]
    if invalid:
        raise ValueError("Every skill id must be a string.")
    return parsed


def _response_data(response: dict[str, Any]) -> Any:
    return response.get("data") if isinstance(response, dict) else None


def _extract_upload_state(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = _response_data(response)
    found: dict[str, dict[str, Any]] = {}
    for item in _walk_values(data):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("fileName") or item.get("assetName") or "")
        path = str(item.get("path") or "")
        key = _asset_key(name, path)
        if "SKILL.md" in name or path == "/":
            found["skill_md"] = item
        elif item.get("putUrl") or item.get("callbackHeader") or path.startswith("/references"):
            found[key] = item
    return found


def _merge_upload_state(
    current_assets: dict[str, dict[str, Any]],
    upload_state: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for key, item in upload_state.items():
        existing = dict(current_assets.get(key) or {})
        existing.update(item)
        existing["asset_key"] = key
        merged[key] = existing
    return merged


def _extract_assets_from_version(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = _response_data(response)
    assets: dict[str, dict[str, Any]] = {}
    for item in _walk_values(data):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("fileName") or item.get("assetName") or "")
        path = str(item.get("path") or "")
        if not name and not path:
            continue
        if name == "SKILL.md" or path == "/":
            key = "skill_md"
        elif path.startswith("/references") or name.endswith(".md"):
            key = _asset_key(name, path)
        else:
            continue
        assets[key] = dict(item)
        assets[key]["asset_key"] = key
    return assets


def _asset_key(name: str, path: str) -> str:
    if name == "SKILL.md" or path == "/":
        return "skill_md"
    if name == "refs.md" and path == "/references":
        return "refs"
    raw = (path.strip("/") + "/" + name).strip("/")
    safe = "".join(ch if ch.isalnum() else "_" for ch in raw)
    return safe or "asset"


def _asset_kind(asset: dict[str, Any]) -> str:
    key = str(asset.get("asset_key") or "")
    if key == "skill_md":
        return "SKILL.md"
    return "reference"


def _asset_content_bytes(asset: dict[str, Any]) -> bytes:
    return str(asset.get("content") or "").encode("utf-8")


def _assets_to_records(assets: dict[str, dict[str, Any]]) -> list[dict]:
    records: list[dict] = []
    for key, asset in assets.items():
        records.append(
            {
                "asset_key": key,
                "name": asset.get("name") or ("SKILL.md" if key == "skill_md" else "refs.md"),
                "path": asset.get("path") or ("/" if key == "skill_md" else "/references"),
                "skillAssetResourceType": asset.get("skillAssetResourceType") or "MD",
                "content": asset.get("content") or "",
            }
        )
    return records


def _assets_from_records(records: object) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    if not isinstance(records, list):
        return assets
    for item in records:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        path = str(item.get("path") or "").strip()
        key = str(item.get("asset_key") or _asset_key(name, path)).strip()
        if not key:
            continue
        assets[key] = {
            "asset_key": key,
            "name": name,
            "path": path,
            "skillAssetResourceType": str(item.get("skillAssetResourceType") or "MD"),
            "content": str(item.get("content") or ""),
        }
    return assets


def _walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_walk_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_walk_values(child))
    return values


def _reference_spec_from_path(path: str, default_name: str | None = None) -> dict[str, Any]:
    from pathlib import Path

    file_path = Path(path)
    name = default_name or file_path.name
    key = "refs" if name == "refs.md" else _asset_key(name, "/references")
    return {
        "asset_key": key,
        "name": name,
        "path": "/references",
        "content": file_path.read_bytes(),
        "local_path": str(file_path),
    }


def default_skill_md() -> str:
    return """# AI 调试 Skill

## Goal
根据 references 回答问题。

## Instructions
优先读取 /references/refs.md。
"""


def default_refs_md() -> str:
    return """# References

WisePen 是一个文档、笔记、AI Skill 资产管理系统。
"""


def _format_duration(tool: ToolCallView) -> str:
    if tool.started_at is None:
        return ""
    end = tool.finished_at or datetime.now(timezone.utc)
    seconds = (end - tool.started_at).total_seconds()
    return f"{seconds * 1000:.0f} ms"


def run_app() -> None:
    root = Tk()
    AppWindow(root)
    root.mainloop()
