from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .batch import QueueItem, create_queue, run_batch_queue
from .engine import AudioProcessorError, get_environment_report, list_audio_files
from .i18n import normalize_language, translate, translate_message, translate_status
from .model_runtime import get_model_runtime_report
from .settings import ProcessingSettings, load_settings, save_settings


AUDIO_PATTERN = "*.aac *.aiff *.alac *.flac *.m4a *.mp3 *.ogg *.opus *.wav *.wma"
LYRICS_EXTENSIONS = {".txt", ".doc", ".docx", ".lrc", ".srt"}
LYRICS_PATTERN = "*.txt *.doc *.docx *.lrc *.srt"


class AudioProcessorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.settings = load_settings()
        self.language = normalize_language(self.settings.language)
        self.queue_items: list[QueueItem] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_requested = threading.Event()
        self.translated_widgets: list[tuple[tk.Widget, str]] = []
        self.status_state: tuple[str, dict[str, Any]] = ("key", {"key": "ready", "values": {}})

        self.minsize(980, 680)
        self._build_variables()
        self._build_ui()
        self._apply_settings_to_vars(self.settings)
        self._apply_language()
        self._poll_events()

    def _build_variables(self) -> None:
        self.output_directory_var = tk.StringVar()
        self.material_directory_var = tk.StringVar()
        self.lyrics_file_var = tk.StringVar()
        self.daw_timeline_export_var = tk.BooleanVar()
        self.output_extension_var = tk.StringVar()
        self.overwrite_var = tk.BooleanVar()
        self.gain_db_var = tk.StringVar()
        self.normalize_var = tk.BooleanVar()
        self.highpass_var = tk.StringVar()
        self.lowpass_var = tk.StringVar()
        self.sample_rate_var = tk.StringVar()
        self.channels_var = tk.StringVar()
        self.codec_var = tk.StringVar()
        self.status_var = tk.StringVar(value=self._t("ready"))
        self.item_progress_var = tk.DoubleVar(value=0)
        self.queue_progress_var = tk.DoubleVar(value=0)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_toolbar(root)
        self._build_main_area(root)
        self._build_status_area(root)

    def _build_toolbar(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.check_button = self._button(toolbar, "check_tools", self.check_tools)
        self.check_button.pack(side="left", padx=(8, 0))

        self.start_button = self._button(toolbar, "start_batch", self.start_batch)
        self.start_button.pack(side="right")
        self.cancel_button = self._button(toolbar, "cancel", self.cancel_batch)
        self.cancel_button.configure(state="disabled")
        self.cancel_button.pack(side="right", padx=(0, 8))

        self.language_button = ttk.Menubutton(toolbar)
        self.language_menu = tk.Menu(self.language_button, tearoff=False)
        self.language_button.configure(menu=self.language_menu)
        self.language_button.pack(side="right", padx=(0, 8))

    def _build_main_area(self, parent: ttk.Frame) -> None:
        main = ttk.PanedWindow(parent, orient="horizontal")
        main.grid(row=1, column=0, sticky="nsew")

        source_frame = ttk.Frame(main)
        settings_frame = ttk.Frame(main)
        main.add(source_frame, weight=3)
        main.add(settings_frame, weight=2)

        self._build_sources(source_frame)
        self._build_settings(settings_frame)

    def _build_sources(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        audio_frame = self._labelframe(parent, "original_audio")
        audio_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self._build_audio_source(audio_frame)

        material_frame = self._labelframe(parent, "material_set")
        material_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self._build_material_source(material_frame)

        lyrics_frame = self._labelframe(parent, "lyrics_file")
        lyrics_frame.grid(row=2, column=0, sticky="ew")
        self._build_lyrics_source(lyrics_frame)

    def _build_audio_source(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        self.audio_hint_label = ttk.Label(parent)
        self.audio_hint_label.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        controls = ttk.Frame(parent)
        controls.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.add_button = self._button(controls, "select_original_audio", self.add_files)
        self.add_button.pack(side="left")
        self.remove_button = self._button(controls, "remove_selected", self.remove_selected)
        self.remove_button.pack(side="left", padx=(8, 0))
        self.clear_button = self._button(controls, "clear", self.clear_queue)
        self.clear_button.pack(side="left", padx=(8, 0))

        columns = ("input", "output", "status", "progress")
        self.queue_table = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        self.queue_table.column("input", width=260, anchor="w")
        self.queue_table.column("output", width=260, anchor="w")
        self.queue_table.column("status", width=110, anchor="w")
        self.queue_table.column("progress", width=90, anchor="e")
        self.queue_table.grid(row=2, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))

        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.queue_table.yview)
        scrollbar.grid(row=2, column=1, sticky="ns", padx=(0, 8), pady=(0, 8))
        self.queue_table.configure(yscrollcommand=scrollbar.set)

    def _build_material_source(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(1, weight=1)

        self.material_hint_label = ttk.Label(parent)
        self.material_hint_label.grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 4))
        self._label(parent, "material_directory").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Entry(parent, textvariable=self.material_directory_var, state="readonly").grid(
            row=1, column=1, sticky="ew", pady=(0, 8)
        )
        self.material_button = self._button(parent, "select_material_folder", self.choose_material_directory)
        self.material_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=(0, 8))
        self.clear_material_button = self._button(parent, "clear_material", self.clear_material_directory)
        self.clear_material_button.grid(row=1, column=3, sticky="ew", padx=8, pady=(0, 8))

    def _build_lyrics_source(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(1, weight=1)

        self.lyrics_hint_label = ttk.Label(parent)
        self.lyrics_hint_label.grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 4))
        self._label(parent, "lyrics_path").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Entry(parent, textvariable=self.lyrics_file_var, state="readonly").grid(
            row=1, column=1, sticky="ew", pady=(0, 8)
        )
        self.lyrics_button = self._button(parent, "select_lyrics_file", self.choose_lyrics_file)
        self.lyrics_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=(0, 8))
        self.clear_lyrics_button = self._button(parent, "clear_lyrics", self.clear_lyrics_file)
        self.clear_lyrics_button.grid(row=1, column=3, sticky="ew", padx=8, pady=(0, 8))

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        row = 0
        self._label(parent, "output_directory").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.output_directory_var).grid(row=row, column=1, sticky="ew", pady=4)
        self.browse_button = self._button(parent, "browse", self.choose_output_directory)
        self.browse_button.grid(row=row, column=2, sticky="ew", padx=(8, 0), pady=4)

        row += 1
        self._label(parent, "extension").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(
            parent,
            textvariable=self.output_extension_var,
            values=[".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"],
            width=12,
        ).grid(row=row, column=1, sticky="w", pady=4)
        self.overwrite_check = self._checkbutton(parent, "overwrite", self.overwrite_var)
        self.overwrite_check.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=4)

        row += 1
        self.daw_timeline_export_check = self._checkbutton(
            parent,
            "daw_timeline_export",
            self.daw_timeline_export_var,
        )
        self.daw_timeline_export_check.configure(command=self._update_outputs_from_settings)
        self.daw_timeline_export_check.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)

        self._label(parent, "gain_db").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.gain_db_var).grid(row=row, column=1, sticky="ew", pady=4)
        self.normalize_check = self._checkbutton(parent, "normalize", self.normalize_var)
        self.normalize_check.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=4)

        row += 1
        self._label(parent, "highpass_hz").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.highpass_var).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        self._label(parent, "lowpass_hz").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.lowpass_var).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        self._label(parent, "sample_rate").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.sample_rate_var).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        self._label(parent, "channels").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.channels_var).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        self._label(parent, "codec").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=self.codec_var).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.save_button = self._button(actions, "save_settings", self.save_current_settings)
        self.save_button.pack(side="left")
        self.reload_button = self._button(actions, "reload_settings", self.reload_settings)
        self.reload_button.pack(side="left", padx=(8, 0))

        row += 1
        self._label(parent, "log").grid(row=row, column=0, sticky="nw", pady=(16, 4))
        self.log_text = tk.Text(parent, height=12, wrap="word")
        self.log_text.grid(row=row, column=1, columnspan=2, sticky="nsew", pady=(16, 4))
        parent.rowconfigure(row, weight=1)

    def _build_status_area(self, parent: ttk.Frame) -> None:
        status = ttk.Frame(parent)
        status.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        status.columnconfigure(1, weight=1)

        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Progressbar(status, variable=self.item_progress_var, maximum=100).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Progressbar(status, variable=self.queue_progress_var, maximum=100).grid(
            row=1, column=1, sticky="ew", pady=(6, 0)
        )

    def add_files(self) -> None:
        file_names = filedialog.askopenfilenames(filetypes=self._filetypes())
        if not file_names:
            return

        settings = self._settings_from_vars()
        new_items = create_queue([Path(name) for name in file_names], settings)
        self.queue_items.extend(new_items)
        self._refresh_queue_table()
        self._log(self._t("added_files", count=len(new_items)))

    def remove_selected(self) -> None:
        selected = set(self.queue_table.selection())
        if not selected:
            return
        self.queue_items = [
            item for index, item in enumerate(self.queue_items) if str(index) not in selected
        ]
        self._refresh_queue_table()

    def clear_queue(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.queue_items.clear()
        self._refresh_queue_table()
        self.item_progress_var.set(0)
        self.queue_progress_var.set(0)
        self._set_status_key("ready")

    def choose_output_directory(self) -> None:
        directory = filedialog.askdirectory()
        if directory:
            self.output_directory_var.set(directory)
            self._update_outputs_from_settings()

    def choose_material_directory(self) -> None:
        directory = filedialog.askdirectory()
        if directory:
            self.material_directory_var.set(directory)
            self._update_outputs_from_settings()
            self._log(self._t("material_selected", path=directory))

    def clear_material_directory(self) -> None:
        self.material_directory_var.set("")
        self._update_outputs_from_settings()
        self._log(self._t("material_cleared"))

    def choose_lyrics_file(self) -> None:
        file_name = filedialog.askopenfilename(filetypes=self._lyrics_filetypes())
        if file_name:
            self.lyrics_file_var.set(file_name)
            self._log(self._t("lyrics_selected", path=file_name))

    def clear_lyrics_file(self) -> None:
        self.lyrics_file_var.set("")
        self._log(self._t("lyrics_cleared"))

    def check_tools(self) -> None:
        try:
            report = "\n".join([*get_environment_report(), "", *get_model_runtime_report()])
        except AudioProcessorError as exc:
            messagebox.showerror(self._t("tool_check_failed_title"), str(exc))
            return
        self._log(report)
        messagebox.showinfo(self._t("tool_check_title"), report)

    def save_current_settings(self) -> None:
        try:
            self.settings = self._settings_from_vars()
            self._validate_source_inputs(self.settings, require_material=False)
            path = save_settings(self.settings)
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_settings_title"), str(exc))
            return
        self._update_outputs_from_settings()
        self._log(self._t("settings_saved", path=path))

    def reload_settings(self) -> None:
        self.settings = load_settings()
        self.language = normalize_language(self.settings.language)
        self._apply_settings_to_vars(self.settings)
        self._update_outputs_from_settings()
        self._apply_language()
        self._log(self._t("settings_reloaded"))

    def start_batch(self) -> None:
        if not self.queue_items:
            messagebox.showwarning(self._t("empty_queue_title"), self._t("empty_queue_message"))
            return

        try:
            self.settings = self._settings_from_vars()
            self._validate_source_inputs(self.settings, require_material=True)
            save_settings(self.settings)
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_settings_title"), str(exc))
            return

        self._update_outputs_from_settings()
        self.cancel_requested.clear()
        self._set_running_state(True)
        self._set_status_key("processing")
        self.item_progress_var.set(0)
        self.queue_progress_var.set(0)
        self._log(self._t("batch_started"))
        self._log_active_source_paths(self.settings)

        self.worker = threading.Thread(target=self._run_batch_worker, daemon=True)
        self.worker.start()

    def cancel_batch(self) -> None:
        self.cancel_requested.set()
        self._set_status_key("cancelling")
        self._log(self._t("cancel_requested"))

    def _run_batch_worker(self) -> None:
        try:
            summary = run_batch_queue(
                self.queue_items,
                self.settings,
                on_item_update=lambda index, item: self.events.put(("item", (index, item))),
                on_queue_progress=lambda progress, message: self.events.put(
                    ("queue", (progress, message))
                ),
                should_cancel=self.cancel_requested.is_set,
            )
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("error", exc))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "item":
                index, item = payload
                self._update_queue_row(index, item)
                self.item_progress_var.set(item.progress * 100)
                self._set_item_status(item)
                if item.message:
                    self._log(self._name_message(item.input_path.name, item.message))

            elif event == "queue":
                progress, message = payload
                self.queue_progress_var.set(progress * 100)
                self._set_queue_status(str(message))

            elif event == "done":
                summary = payload
                self._set_running_state(False)
                self._set_status_key(
                    "summary",
                    completed=summary.completed,
                    failed=summary.failed,
                    cancelled=summary.cancelled,
                )
                self.queue_progress_var.set(100 if summary.total else 0)
                self._log(self.status_var.get())

            elif event == "error":
                self._set_running_state(False)
                self._set_status_key("failed")
                messagebox.showerror(self._t("batch_failed_title"), str(payload))
                self._log(self._t("batch_failed_log", error=payload))

        self.after(100, self._poll_events)

    def _settings_from_vars(self) -> ProcessingSettings:
        return ProcessingSettings(
            language=self.language,
            material_directory=self.material_directory_var.get().strip(),
            lyrics_file=self.lyrics_file_var.get().strip(),
            daw_timeline_export=self.daw_timeline_export_var.get(),
            output_directory=self.output_directory_var.get().strip(),
            output_extension=self.output_extension_var.get().strip(),
            overwrite=self.overwrite_var.get(),
            trim_start=None,
            duration=None,
            gain_db=self._optional_float(self.gain_db_var, "gain_db"),
            normalize=self.normalize_var.get(),
            highpass_hz=self._optional_float(self.highpass_var, "highpass_hz"),
            lowpass_hz=self._optional_float(self.lowpass_var, "lowpass_hz"),
            sample_rate=self._optional_int(self.sample_rate_var, "sample_rate"),
            channels=self._optional_int(self.channels_var, "channels"),
            codec=self._optional_text(self.codec_var),
        )

    def _apply_settings_to_vars(self, settings: ProcessingSettings) -> None:
        self.language = normalize_language(settings.language)
        self.material_directory_var.set(settings.material_directory)
        self.lyrics_file_var.set(settings.lyrics_file)
        self.daw_timeline_export_var.set(settings.daw_timeline_export)
        self.output_directory_var.set(settings.output_directory)
        self.output_extension_var.set(settings.output_extension)
        self.overwrite_var.set(settings.overwrite)
        self.gain_db_var.set("" if settings.gain_db is None else str(settings.gain_db))
        self.normalize_var.set(settings.normalize)
        self.highpass_var.set("" if settings.highpass_hz is None else str(settings.highpass_hz))
        self.lowpass_var.set("" if settings.lowpass_hz is None else str(settings.lowpass_hz))
        self.sample_rate_var.set("" if settings.sample_rate is None else str(settings.sample_rate))
        self.channels_var.set("" if settings.channels is None else str(settings.channels))
        self.codec_var.set(settings.codec or "")

    def _set_language(self, language: str) -> None:
        self.language = normalize_language(language)
        self.settings = ProcessingSettings.from_dict({**self.settings.to_dict(), "language": self.language})
        save_settings(self.settings)
        self._apply_language()
        self._refresh_queue_table()

    def _apply_language(self) -> None:
        self.title(self._t("app_title"))
        for widget, key in self.translated_widgets:
            widget.configure(text=self._t(key))

        self.language_button.configure(text=self._t("language_menu"))
        self.audio_hint_label.configure(
            text=self._t("supported_formats", formats=self._t("audio_format_hint"))
        )
        self.material_hint_label.configure(
            text=self._t("supported_formats", formats=self._t("material_format_hint"))
        )
        self.lyrics_hint_label.configure(
            text=self._t("supported_formats", formats=self._t("lyrics_format_hint"))
        )
        self.language_menu.delete(0, "end")
        self.language_menu.add_command(label=self._t("language_zh"), command=lambda: self._set_language("zh"))
        self.language_menu.add_command(label=self._t("language_en"), command=lambda: self._set_language("en"))

        self.queue_table.heading("input", text=self._t("input"))
        self.queue_table.heading("output", text=self._t("output"))
        self.queue_table.heading("status", text=self._t("status"))
        self.queue_table.heading("progress", text=self._t("progress"))
        self._render_status_state()
        self._refresh_queue_table()

    def _update_outputs_from_settings(self) -> None:
        settings = self._settings_from_vars()
        for item in self.queue_items:
            if item.status in {"Queued", "Failed", "Cancelled"}:
                item.output_path = settings.output_path_for(item.input_path)
                if item.status in {"Failed", "Cancelled"}:
                    item.status = "Queued"
                    item.message = ""
                    item.progress = 0.0
        self._refresh_queue_table()

    def _refresh_queue_table(self) -> None:
        self.queue_table.delete(*self.queue_table.get_children())
        for index, item in enumerate(self.queue_items):
            self.queue_table.insert(
                "",
                "end",
                iid=str(index),
                values=self._row_values(item),
            )

    def _update_queue_row(self, index: int, item: QueueItem) -> None:
        row_id = str(index)
        if self.queue_table.exists(row_id):
            self.queue_table.item(row_id, values=self._row_values(item))
        else:
            self._refresh_queue_table()

    def _row_values(self, item: QueueItem) -> tuple[str, str, str, str]:
        return (
            str(item.input_path),
            str(item.output_path),
            self._status_text(item.status),
            f"{item.progress * 100:.0f}%",
        )

    def _set_running_state(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")

    def _set_status_key(self, key: str, **values: object) -> None:
        self.status_state = ("key", {"key": key, "values": values})
        self._render_status_state()

    def _set_item_status(self, item: QueueItem) -> None:
        self.status_state = (
            "item",
            {"status": item.status, "name": item.input_path.name},
        )
        self._render_status_state()

    def _set_queue_status(self, message: str) -> None:
        self.status_state = ("queue", {"message": message})
        self._render_status_state()

    def _render_status_state(self) -> None:
        kind, payload = self.status_state
        if kind == "item":
            self.status_var.set(
                self._t(
                    "status_item",
                    status=self._status_text(str(payload["status"])),
                    name=payload["name"],
                )
            )
            return

        if kind == "queue":
            self.status_var.set(self._queue_message(str(payload["message"])))
            return

        self.status_var.set(self._t(str(payload["key"]), **payload["values"]))

    def _queue_message(self, message: str) -> str:
        match = re.match(r"^(\d+)/(\d+): (.+)$", message)
        if not match:
            return self._message_text(message)
        index, total, raw_message = match.groups()
        return self._t(
            "queue_progress",
            index=index,
            total=total,
            message=self._message_text(raw_message),
        )

    def _name_message(self, name: str, message: str) -> str:
        separator = "：" if self.language == "zh" else ": "
        return f"{name}{separator}{self._message_text(message)}"

    def _status_text(self, status: str) -> str:
        return translate_status(self.language, status)

    def _message_text(self, message: str) -> str:
        return translate_message(self.language, message)

    def _log(self, message: str) -> None:
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")

    def _button(self, parent: tk.Widget, key: str, command: Any) -> ttk.Button:
        button = ttk.Button(parent, command=command)
        self.translated_widgets.append((button, key))
        return button

    def _checkbutton(self, parent: tk.Widget, key: str, variable: tk.BooleanVar) -> ttk.Checkbutton:
        button = ttk.Checkbutton(parent, variable=variable)
        self.translated_widgets.append((button, key))
        return button

    def _label(self, parent: tk.Widget, key: str) -> ttk.Label:
        label = ttk.Label(parent)
        self.translated_widgets.append((label, key))
        return label

    def _labelframe(self, parent: tk.Widget, key: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent)
        self.translated_widgets.append((frame, key))
        return frame

    def _filetypes(self) -> list[tuple[str, str]]:
        return [(self._t("audio_files"), AUDIO_PATTERN), (self._t("all_files"), "*.*")]

    def _lyrics_filetypes(self) -> list[tuple[str, str]]:
        return [(self._t("lyrics_file"), LYRICS_PATTERN), (self._t("all_files"), "*.*")]

    def _validate_source_inputs(
        self,
        settings: ProcessingSettings,
        *,
        require_material: bool,
    ) -> None:
        if require_material and not settings.material_directory:
            raise ValueError(self._t("missing_material_directory"))

        if settings.material_directory:
            material_path = Path(settings.material_directory).expanduser()
            if not material_path.is_dir():
                raise ValueError(self._t("invalid_material_directory", path=material_path))
            if not list_audio_files(material_path):
                raise ValueError(self._t("empty_material_directory", path=material_path))

        if settings.lyrics_file:
            lyrics_path = Path(settings.lyrics_file).expanduser()
            if not lyrics_path.is_file():
                raise ValueError(self._t("invalid_lyrics_file", path=lyrics_path))
            if lyrics_path.suffix.lower() not in LYRICS_EXTENSIONS:
                raise ValueError(self._t("unsupported_lyrics_format", path=lyrics_path))

    def _log_active_source_paths(self, settings: ProcessingSettings) -> None:
        if settings.material_directory:
            self._log(self._t("assembly_mode_active"))
            self._log(self._t("model_assisted_ordering_active"))
            if settings.daw_timeline_export:
                self._log(self._t("daw_timeline_mode_active"))
            self._log(self._t("material_active", path=settings.material_directory))
        if settings.lyrics_file:
            self._log(self._t("lyrics_active", path=settings.lyrics_file))

    def _t(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    @staticmethod
    def _optional_text(variable: tk.StringVar) -> str | None:
        text = variable.get().strip()
        return text or None

    def _optional_float(self, variable: tk.StringVar, label_key: str) -> float | None:
        text = variable.get().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(
                self._t("must_be_number", label=self._t(label_key))
            ) from exc

    def _optional_int(self, variable: tk.StringVar, label_key: str) -> int | None:
        text = variable.get().strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(
                self._t("must_be_integer", label=self._t(label_key))
            ) from exc


def main() -> int:
    app = AudioProcessorApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
