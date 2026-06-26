#!/usr/bin/env python3
"""
Desktop GUI for legitimate radio outreach.

This front-end wraps the shared sending logic in send_pitches.py and gives you
a simple way to configure SMTP, load the station catalog, preview the message,
and start or stop a batch run without using the console.
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

try:
    import keyring
except ImportError:  # pragma: no cover - handled gracefully at runtime.
    keyring = None

from send_pitches import (
    DEFAULT_DRIVE_UPLOAD_THRESHOLD_BYTES,
    MAX_SAFE_ATTACHMENT_BYTES,
    Config,
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    format_bytes,
    load_contacts,
    merge_context,
    render_text,
    run_campaign,
    split_attachments_by_size,
    validate_attachment_size_limit,
)


DEFAULT_SOURCE_URL = "https://college.crockpotcartel.com/"
SETTINGS_PATH = Path(__file__).with_name("sendmail_settings.json")
KEYRING_SERVICE = "SendMailOutreach"
ICON_DIR = Path(__file__).with_name("assets")
ICON_PNG = ICON_DIR / "sendmail_icon.png"
ICON_ICO = ICON_DIR / "sendmail_icon.ico"

BG = "#e9edf5"
PANEL = "#f7f9fc"
CARD = "#ffffff"
HEADER_BG = "#0b1220"
HEADER_ACCENT = "#f59e0b"
TEXT = "#18212f"
MUTED = "#607084"
PRIMARY = "#f59e0b"
PRIMARY_HOVER = "#fbbf24"
DANGER = "#ef4444"
SOFT = "#dbe4f0"


class SendMailApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SendMail Outreach")
        self.geometry("1440x920")
        self.minsize(1240, 780)
        self._set_app_icon()

        self.contacts: list[dict[str, str]] = []
        self.selected_row_id: str | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        self._build_style()
        self._build_vars()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(0, self.load_settings_from_disk)
        self.after(120, self._drain_queue)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=HEADER_BG, foreground="#f8fafc", font=("Segoe UI", 22, "bold"))
        style.configure("HeaderSub.TLabel", background=HEADER_BG, foreground="#cbd5e1", font=("Segoe UI", 10))
        style.configure("Section.TFrame", background=BG)
        style.configure("Section.TLabelframe", background=PANEL, foreground=TEXT)
        style.configure("Section.TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", background=PRIMARY, foreground="#111827", padding=(14, 8))
        style.map(
            "Accent.TButton",
            background=[("active", PRIMARY_HOVER), ("pressed", "#d97706")],
            foreground=[("disabled", "#52525b")],
        )
        style.configure("Soft.TButton", background=SOFT, foreground=TEXT, padding=(12, 7))
        style.map("Soft.TButton", background=[("active", "#cbd5e1"), ("pressed", "#94a3b8")])
        style.configure("Danger.TButton", background=DANGER, foreground="#ffffff", padding=(12, 7))
        style.map("Danger.TButton", background=[("active", "#dc2626"), ("pressed", "#b91c1c")])
        style.configure("Ghost.TButton", background=BG, foreground=TEXT, padding=(10, 6))
        style.map("Ghost.TButton", background=[("active", "#dbe4f0"), ("pressed", "#cbd5e1")])
        style.configure("TLabelframe", background=PANEL, foreground=TEXT)
        style.configure("TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 9), background="#ffffff", fieldbackground="#ffffff", foreground=TEXT)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#dbe4f0", foreground=TEXT)
        style.map("Treeview", background=[("selected", "#fef3c7")], foreground=[("selected", TEXT)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 8), background="#dbe4f0", foreground=TEXT)
        style.map(
            "TNotebook.Tab",
            background=[("selected", CARD), ("active", "#e2e8f0")],
            padding=[("selected", (26, 14)), ("!selected", (10, 7))],
            font=[("selected", ("Segoe UI", 10, "bold")), ("!selected", ("Segoe UI", 9))],
        )

    def _build_vars(self) -> None:
        self.source_mode = tk.StringVar(value="url")
        self.source_url = tk.StringVar(value=DEFAULT_SOURCE_URL)
        self.csv_path = tk.StringVar(value="")
        self.smtp_provider = tk.StringVar(value="Gmail")
        self.smtp_host = tk.StringVar(value="smtp.gmail.com")
        self.smtp_port = tk.StringVar(value="587")
        self.smtp_user = tk.StringVar(value="")
        self.smtp_password = tk.StringVar(value="")
        self.from_name = tk.StringVar(value="Roman Tishkov")
        self.from_email = tk.StringVar(value="")
        self.use_ssl = tk.BooleanVar(value=False)
        self.delay_seconds = tk.StringVar(value="1.5")
        self.max_per_run = tk.StringVar(value="")
        self.sent_log_path = tk.StringVar(value="sent_log.csv")
        self.subject_template = tk.StringVar(value=DEFAULT_SUBJECT)
        self.search_query = tk.StringVar(value="")
        self.status_text = tk.StringVar(value="Ready.")
        self.contact_count_text = tk.StringVar(value="0 contacts loaded")
        self.provider_note_text = tk.StringVar(value="Use an app password or SMTP credentials provided by your mail client.")
        self.password_status_text = tk.StringVar(value="Password storage: system keychain")
        self.attachment_summary_text = tk.StringVar(value="No attachments selected.")
        self.drive_client_secrets_path = tk.StringVar(value="")
        self.drive_token_path = tk.StringVar(value="")
        self.drive_folder_id = tk.StringVar(value="")
        self.drive_threshold_mb = tk.StringVar(value=str(DEFAULT_DRIVE_UPLOAD_THRESHOLD_BYTES // (1024 * 1024)))
        self.drive_make_public = tk.BooleanVar(value=True)
        self.attachment_paths: list[str] = []
        self.template_vars: dict[str, tk.StringVar] = {
            "artist_name": tk.StringVar(value="Roman Tishkov"),
            "song_title": tk.StringVar(value="Burn the System"),
            "stream_url": tk.StringVar(value="https://open.spotify.com/album/5hHoVRrEfTD62Yrju9RNgl"),
            "spotify_artist_url": tk.StringVar(value="https://open.spotify.com/artist/54ukzXc5sUbZdKsUTDKJvY"),
            "youtube_url": tk.StringVar(value="https://youtu.be/ykIasUhIAGI"),
            "genre_description": tk.StringVar(value="dark cinematic industrial rock / alternative track with a rebellious, high-energy atmosphere"),
            "theme_description": tk.StringVar(value="frustration with broken systems, social pressure, and the feeling of fighting back against control"),
            "artist_blurb_1": tk.StringVar(value="I create cinematic, dark and emotionally charged music blending alternative rock, gothic rock, industrial energy, dark pop and electronic elements."),
            "artist_blurb_2": tk.StringVar(value="My songs often explore themes of inner conflict, rebellion, freedom, personal transformation and the fight against fear or control."),
            "artist_blurb_3": tk.StringVar(value="As an independent artist, I build each release as a full visual and musical concept with strong storytelling and atmosphere."),
        }

    def _build_layout(self) -> None:
        self.configure(background=BG)

        hero = tk.Frame(self, bg=HEADER_BG, highlightthickness=0)
        hero.pack(fill="x")
        hero_inner = tk.Frame(hero, bg=HEADER_BG)
        hero_inner.pack(fill="x", padx=18, pady=16)

        title_row = tk.Frame(hero_inner, bg=HEADER_BG)
        title_row.pack(fill="x")
        accent = tk.Frame(title_row, bg=HEADER_ACCENT, width=10, height=48)
        accent.pack(side="left", padx=(0, 14))
        accent.pack_propagate(False)

        title_block = tk.Frame(title_row, bg=HEADER_BG)
        title_block.pack(side="left", fill="x", expand=True)
        tk.Label(title_block, text="SendMail Outreach", bg=HEADER_BG, fg="#f8fafc", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(
            title_block,
            text="Load the station catalog, tune your mail client, preview the pitch, and send in controlled batches.",
            bg=HEADER_BG,
            fg="#cbd5e1",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        main_wrap = tk.Frame(self, bg=BG)
        main_wrap.pack(fill="both", expand=True, padx=14, pady=(14, 14))

        main = ttk.Panedwindow(main_wrap, orient=tk.HORIZONTAL)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, padding=12, style="Section.TFrame")
        right = ttk.Frame(main, padding=12, style="Section.TFrame")
        main.add(left, weight=3)
        main.add(right, weight=4)
        self._main_split = main

        self._build_settings_panel(left)
        self._build_right_panel(right)
        self.after_idle(self._apply_initial_pane_sizes)

    def _set_app_icon(self) -> None:
        try:
            if not ICON_PNG.exists() or not ICON_ICO.exists():
                from make_icon import main as build_icon

                build_icon()
            if ICON_PNG.exists():
                icon = tk.PhotoImage(file=str(ICON_PNG))
                self.iconphoto(True, icon)
                self._window_icon = icon
            elif ICON_ICO.exists():
                self.iconbitmap(default=str(ICON_ICO))
        except Exception:
            pass

    def _apply_initial_pane_sizes(self) -> None:
        try:
            width = self.winfo_width() or 1440
            if hasattr(self, "_main_split"):
                self._main_split.sashpos(0, int(width * 0.34))
        except tk.TclError:
            pass

    def _build_settings_panel(self, parent: ttk.Frame) -> None:
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=3)
        container.rowconfigure(1, weight=2)

        nb = ttk.Notebook(container)
        nb.grid(row=0, column=0, sticky="nsew")

        source_tab = ttk.Frame(nb, padding=12)
        smtp_tab = ttk.Frame(nb, padding=12)
        template_tab = ttk.Frame(nb, padding=12)
        vars_tab = ttk.Frame(nb, padding=12)
        files_tab = ttk.Frame(nb, padding=12)
        drive_tab = ttk.Frame(nb, padding=12)
        nb.add(source_tab, text="Source")
        nb.add(smtp_tab, text="Mail")
        nb.add(template_tab, text="Template")
        nb.add(vars_tab, text="Variables")
        nb.add(files_tab, text="Files")
        nb.add(drive_tab, text="Drive")
        preview_tab = ttk.Frame(nb, padding=12)
        log_tab = ttk.Frame(nb, padding=12)
        nb.add(preview_tab, text="Preview")
        nb.add(log_tab, text="Log")

        # Source
        source_group = ttk.LabelFrame(source_tab, text="Station source", padding=12, style="Section.TLabelframe")
        source_group.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(source_group, text="Public station directory URL", variable=self.source_mode, value="url").grid(row=0, column=0, sticky="w")
        ttk.Entry(source_group, textvariable=self.source_url).grid(row=1, column=0, sticky="ew", pady=(4, 8))
        ttk.Radiobutton(source_group, text="Local CSV file", variable=self.source_mode, value="csv").grid(row=2, column=0, sticky="w")
        csv_row = ttk.Frame(source_group)
        csv_row.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        ttk.Entry(csv_row, textvariable=self.csv_path).pack(side="left", fill="x", expand=True)
        ttk.Button(csv_row, text="Browse", command=self._browse_csv, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        source_group.columnconfigure(0, weight=1)

        controls_group = ttk.LabelFrame(source_tab, text="Run controls", padding=12, style="Section.TLabelframe")
        controls_group.pack(fill="x")
        self._labeled_entry(controls_group, "Delay per email (sec)", self.delay_seconds, 0)
        self._labeled_entry(controls_group, "Max per run", self.max_per_run, 1)
        self._labeled_entry(controls_group, "Sent log path", self.sent_log_path, 2)

        button_row = ttk.Frame(source_tab)
        button_row.pack(fill="x", pady=12)
        ttk.Button(button_row, text="Load stations", command=self.load_contacts_async, style="Accent.TButton").pack(side="left")
        ttk.Button(button_row, text="Preview first", command=self.preview_selected_or_first, style="Soft.TButton").pack(side="left", padx=8)
        ttk.Button(button_row, text="Reset templates", command=self.reset_templates, style="Ghost.TButton").pack(side="right")

        # Mail
        provider_box = ttk.LabelFrame(smtp_tab, text="Mail client preset", padding=12, style="Section.TLabelframe")
        provider_box.pack(fill="x", pady=(0, 10))
        provider_choice = ttk.Combobox(provider_box, textvariable=self.smtp_provider, values=["Gmail", "Outlook", "Yahoo", "Custom"], state="readonly")
        provider_choice.grid(row=0, column=0, sticky="ew")
        provider_choice.bind("<<ComboboxSelected>>", lambda _e: self.apply_provider_preset())
        ttk.Label(provider_box, textvariable=self.provider_note_text, foreground="#506070", wraplength=360).grid(row=1, column=0, sticky="w", pady=(8, 0))
        provider_box.columnconfigure(0, weight=1)

        smtp_group = ttk.LabelFrame(smtp_tab, text="SMTP settings", padding=12, style="Section.TLabelframe")
        smtp_group.pack(fill="x")
        self._labeled_entry(smtp_group, "SMTP host", self.smtp_host, 0)
        self._labeled_entry(smtp_group, "SMTP port", self.smtp_port, 1)
        self._labeled_entry(smtp_group, "SMTP user", self.smtp_user, 2)
        self._password_entry(smtp_group, "SMTP password", self.smtp_password, 3)
        self._labeled_entry(smtp_group, "From name", self.from_name, 4)
        self._labeled_entry(smtp_group, "From email", self.from_email, 5)
        ttk.Checkbutton(smtp_group, text="Use SSL / TLS wrapper", variable=self.use_ssl).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(smtp_group, textvariable=self.password_status_text, foreground="#506070").grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        smtp_group.columnconfigure(1, weight=1)

        smtp_actions = ttk.Frame(smtp_tab)
        smtp_actions.pack(fill="x", pady=10)
        ttk.Button(smtp_actions, text="Save settings", command=self.save_settings_to_disk, style="Accent.TButton").pack(side="left")
        ttk.Button(smtp_actions, text="Load settings", command=self.load_settings_from_disk, style="Soft.TButton").pack(side="left", padx=8)
        ttk.Button(smtp_actions, text="Apply preset", command=self.apply_provider_preset, style="Ghost.TButton").pack(side="right")
        ttk.Button(smtp_actions, text="Clear password", command=self.clear_saved_password, style="Danger.TButton").pack(side="right", padx=8)

        # Template
        subj_group = ttk.LabelFrame(template_tab, text="Subject template", padding=12, style="Section.TLabelframe")
        subj_group.pack(fill="x", pady=(0, 10))
        ttk.Entry(subj_group, textvariable=self.subject_template).pack(fill="x")

        body_group = ttk.LabelFrame(template_tab, text="Body template", padding=12, style="Section.TLabelframe")
        body_group.pack(fill="both", expand=True)
        self.body_text = tk.Text(body_group, wrap="word", height=24, font=("Consolas", 10), bg="white", fg="#18212f", relief="flat")
        body_scroll = ttk.Scrollbar(body_group, orient="vertical", command=self.body_text.yview)
        self.body_text.configure(yscrollcommand=body_scroll.set)
        self.body_text.pack(side="left", fill="both", expand=True)
        body_scroll.pack(side="right", fill="y")
        self.body_text.insert("1.0", DEFAULT_BODY)

        vars_group = ttk.LabelFrame(vars_tab, text="Template values", padding=12, style="Section.TLabelframe")
        vars_group.pack(fill="both", expand=True)
        vars_canvas = tk.Canvas(vars_group, highlightthickness=0, bg="#f5f7fb")
        vars_scroll = ttk.Scrollbar(vars_group, orient="vertical", command=vars_canvas.yview)
        vars_inner = ttk.Frame(vars_canvas)
        vars_inner.bind(
            "<Configure>",
            lambda _e: vars_canvas.configure(scrollregion=vars_canvas.bbox("all")),
        )
        vars_canvas.create_window((0, 0), window=vars_inner, anchor="nw")
        vars_canvas.configure(yscrollcommand=vars_scroll.set)
        vars_canvas.pack(side="left", fill="both", expand=True)
        vars_scroll.pack(side="right", fill="y")
        self._build_template_var_form(vars_inner)

        files_tab.columnconfigure(0, weight=1)
        files_tab.rowconfigure(1, weight=1)
        files_group = ttk.LabelFrame(files_tab, text="Attachments", padding=12, style="Section.TLabelframe")
        files_group.grid(row=0, column=0, sticky="nsew")
        files_group.columnconfigure(0, weight=1)
        ttk.Label(files_group, text="Files added here will be attached to every email in this campaign.", foreground="#506070").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(files_group, textvariable=self.attachment_summary_text, foreground="#506070").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.attach_listbox = tk.Listbox(files_group, height=6, selectmode="extended", bg="#ffffff", fg="#18212f", activestyle="none")
        attach_scroll = ttk.Scrollbar(files_group, orient="vertical", command=self.attach_listbox.yview)
        self.attach_listbox.configure(yscrollcommand=attach_scroll.set)
        self.attach_listbox.grid(row=2, column=0, columnspan=2, sticky="nsew")
        attach_scroll.grid(row=2, column=2, sticky="ns")
        files_group.rowconfigure(2, weight=1)
        attach_buttons = ttk.Frame(files_group)
        attach_buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(attach_buttons, text="Add files", command=self.add_attachments, style="Accent.TButton").pack(side="left")
        ttk.Button(attach_buttons, text="Remove selected", command=self.remove_selected_attachments, style="Soft.TButton").pack(side="left", padx=8)
        ttk.Button(attach_buttons, text="Clear all", command=self.clear_attachments, style="Ghost.TButton").pack(side="left")

        drive_tab.columnconfigure(0, weight=1)
        drive_group = ttk.LabelFrame(drive_tab, text="Google Drive uploads", padding=12, style="Section.TLabelframe")
        drive_group.pack(fill="x")
        drive_group.columnconfigure(1, weight=1)
        ttk.Label(drive_group, text="Oversized files will be uploaded to Drive and the link will be inserted into the email body.", foreground="#506070").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        self._labeled_entry(drive_group, "Client secrets JSON", self.drive_client_secrets_path, 1)
        ttk.Button(drive_group, text="Browse", command=self._browse_drive_client_secrets, style="Ghost.TButton").grid(row=1, column=2, sticky="w", padx=(8, 0))
        self._labeled_entry(drive_group, "Token cache JSON", self.drive_token_path, 2)
        ttk.Button(drive_group, text="Browse", command=self._browse_drive_token_path, style="Ghost.TButton").grid(row=2, column=2, sticky="w", padx=(8, 0))
        self._labeled_entry(drive_group, "Drive folder ID", self.drive_folder_id, 3)
        self._labeled_entry(drive_group, "Drive threshold MB", self.drive_threshold_mb, 4)
        ttk.Checkbutton(drive_group, text="Make uploaded files public", variable=self.drive_make_public).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

        preview_tab.columnconfigure(0, weight=1)
        preview_tab.rowconfigure(0, weight=1)
        preview_group = ttk.LabelFrame(preview_tab, text="Preview", padding=10, style="Section.TLabelframe")
        preview_group.grid(row=0, column=0, sticky="nsew")
        preview_group.columnconfigure(0, weight=1)
        preview_group.rowconfigure(0, weight=1)
        self.preview_text = tk.Text(preview_group, wrap="word", font=("Consolas", 10), bg="#f8fafc", fg="#18212f", relief="flat")
        preview_scroll = ttk.Scrollbar(preview_group, orient="vertical", command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=preview_scroll.set)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        preview_scroll.grid(row=0, column=1, sticky="ns")

        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        log_group = ttk.LabelFrame(log_tab, text="Activity log", padding=10, style="Section.TLabelframe")
        log_group.grid(row=0, column=0, sticky="nsew")
        log_group.columnconfigure(0, weight=1)
        log_group.rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(log_group, wrap="word", font=("Consolas", 9), bg="#111827", fg="#e5e7eb", insertbackground="white")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.insert("end", "Ready.\n")
        self.log_text.configure(state="disabled")

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        top_row = ttk.Frame(parent)
        top_row.pack(fill="x")
        ttk.Label(top_row, textvariable=self.contact_count_text, font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Label(top_row, textvariable=self.status_text, foreground="#506070").pack(side="right")

        filter_row = ttk.Frame(parent)
        filter_row.pack(fill="x", pady=(10, 6))
        ttk.Label(filter_row, text="Search").pack(side="left")
        search_entry = ttk.Entry(filter_row, textvariable=self.search_query)
        search_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        search_entry.bind("<KeyRelease>", lambda _e: self.refresh_station_view())
        ttk.Button(filter_row, text="Refresh view", command=self.refresh_station_view).pack(side="left", padx=8)

        table_frame = ttk.LabelFrame(parent, text="Stations", padding=10, style="Section.TLabelframe")
        table_frame.pack(fill="both", expand=True, pady=(0, 10))
        columns = ("station", "email", "contact", "city", "state", "genres")
        self.station_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", height=16)
        self.station_tree.heading("station", text="Station")
        self.station_tree.heading("email", text="Email")
        self.station_tree.heading("contact", text="Director")
        self.station_tree.heading("city", text="City")
        self.station_tree.heading("state", text="State")
        self.station_tree.heading("genres", text="Genres")
        self.station_tree.column("station", width=260, anchor="w")
        self.station_tree.column("email", width=240, anchor="w")
        self.station_tree.column("contact", width=150, anchor="w")
        self.station_tree.column("city", width=130, anchor="w")
        self.station_tree.column("state", width=70, anchor="center")
        self.station_tree.column("genres", width=240, anchor="w")
        station_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.station_tree.yview)
        self.station_tree.configure(yscrollcommand=station_scroll.set)
        self.station_tree.pack(side="left", fill="both", expand=True)
        station_scroll.pack(side="right", fill="y")
        self.station_tree.bind("<<TreeviewSelect>>", lambda _e: self.preview_selected_or_first())

        action_row = ttk.Frame(parent)
        action_row.pack(fill="x", pady=(0, 10))
        ttk.Button(action_row, text="Load from source", command=self.load_contacts_async, style="Soft.TButton").pack(side="left")
        ttk.Button(action_row, text="Dry run", command=lambda: self.start_send(dry_run=True), style="Accent.TButton").pack(side="left", padx=8)
        ttk.Button(action_row, text="Send now", command=lambda: self.start_send(dry_run=False), style="Accent.TButton").pack(side="left")
        self.stop_button = ttk.Button(action_row, text="Stop", command=self.stop_run, style="Danger.TButton")
        self.stop_button.pack(side="right")

    def _labeled_entry(self, parent: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)

    def _password_entry(self, parent: ttk.Frame, label: str, var: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        field = ttk.Frame(parent)
        field.grid(row=row, column=1, sticky="ew", pady=4)
        field.columnconfigure(0, weight=1)
        entry = ttk.Entry(field, textvariable=var, show="*")
        entry.grid(row=0, column=0, sticky="ew")
        self._enable_password_paste(entry)
        ttk.Button(field, text="Paste", width=8, command=lambda: self._paste_to_var(var), style="Ghost.TButton").grid(row=0, column=1, padx=(8, 0))

    def _enable_password_paste(self, entry: ttk.Entry) -> None:
        entry.bind("<Control-v>", lambda _e: self._paste_to_entry(entry))
        entry.bind("<Control-V>", lambda _e: self._paste_to_entry(entry))
        entry.bind("<Shift-Insert>", lambda _e: self._paste_to_entry(entry))
        entry.bind("<Button-3>", self._show_entry_menu)
        menu = tk.Menu(entry, tearoff=False)
        menu.add_command(label="Paste", command=lambda: self._paste_to_entry(entry))
        entry._context_menu = menu  # type: ignore[attr-defined]

    def _show_entry_menu(self, event: tk.Event) -> str:
        menu = getattr(event.widget, "_context_menu", None)
        if menu is not None:
            menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _paste_to_entry(self, entry: ttk.Entry) -> str:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"
        entry.insert(tk.INSERT, text)
        return "break"

    def _paste_to_var(self, var: tk.StringVar) -> None:
        try:
            var.set(self.clipboard_get())
        except tk.TclError:
            messagebox.showwarning("Clipboard", "No text found in the clipboard.")

    def _build_template_var_form(self, parent: ttk.Frame) -> None:
        fields = [
            ("artist_name", "Artist name"),
            ("song_title", "Song title"),
            ("stream_url", "Streaming link"),
            ("spotify_artist_url", "Spotify artist URL"),
            ("youtube_url", "YouTube URL"),
            ("genre_description", "Genre description"),
            ("theme_description", "Theme description"),
            ("artist_blurb_1", "Bio line 1"),
            ("artist_blurb_2", "Bio line 2"),
            ("artist_blurb_3", "Bio line 3"),
        ]
        for row, (key, label) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            entry = ttk.Entry(parent, textvariable=self.template_vars[key])
            entry.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)

    def apply_provider_preset(self) -> None:
        provider = self.smtp_provider.get()
        if provider == "Gmail":
            self.smtp_host.set("smtp.gmail.com")
            self.smtp_port.set("587")
            self.use_ssl.set(False)
            self.provider_note_text.set("Gmail usually works with an app password and STARTTLS on port 587.")
        elif provider == "Outlook":
            self.smtp_host.set("smtp.office365.com")
            self.smtp_port.set("587")
            self.use_ssl.set(False)
            self.provider_note_text.set("Microsoft 365 / Outlook usually uses port 587 with STARTTLS.")
        elif provider == "Yahoo":
            self.smtp_host.set("smtp.mail.yahoo.com")
            self.smtp_port.set("587")
            self.use_ssl.set(False)
            self.provider_note_text.set("Yahoo Mail typically uses an app password and STARTTLS.")
        else:
            self.provider_note_text.set("Custom SMTP selected. Enter the host, port, and auth details from your mail provider.")

    def _settings_dict(self) -> dict[str, object]:
        return {
            "source_mode": self.source_mode.get(),
            "source_url": self.source_url.get(),
            "csv_path": self.csv_path.get(),
            "smtp_provider": self.smtp_provider.get(),
            "smtp_host": self.smtp_host.get(),
            "smtp_port": self.smtp_port.get(),
            "smtp_user": self.smtp_user.get(),
            "from_name": self.from_name.get(),
            "from_email": self.from_email.get(),
            "use_ssl": self.use_ssl.get(),
            "delay_seconds": self.delay_seconds.get(),
            "max_per_run": self.max_per_run.get(),
            "sent_log_path": self.sent_log_path.get(),
            "subject_template": self.subject_template.get(),
            "body_template": self.body_text.get("1.0", "end").rstrip("\n"),
            "search_query": self.search_query.get(),
            "attachment_paths": list(self.attachment_paths),
            "drive_client_secrets_path": self.drive_client_secrets_path.get(),
            "drive_token_path": self.drive_token_path.get(),
            "drive_folder_id": self.drive_folder_id.get(),
            "drive_threshold_mb": self.drive_threshold_mb.get(),
            "drive_make_public": self.drive_make_public.get(),
            "template_vars": {key: var.get() for key, var in self.template_vars.items()},
        }

    def _password_account(self) -> str | None:
        host = self.smtp_host.get().strip().lower()
        user = self.smtp_user.get().strip().lower()
        if not host or not user:
            return None
        return f"{user}@{host}"

    def _load_secure_password(self) -> str:
        if keyring is None:
            return ""
        account = self._password_account()
        if not account:
            return ""
        try:
            secret = keyring.get_password(KEYRING_SERVICE, account)
        except Exception:
            return ""
        return secret or ""

    def _store_secure_password(self) -> bool:
        if keyring is None:
            return False
        account = self._password_account()
        password = self.smtp_password.get()
        if not account or not password:
            return False
        try:
            keyring.set_password(KEYRING_SERVICE, account, password)
        except Exception:
            return False
        return True

    def _clear_secure_password(self) -> bool:
        if keyring is None:
            return False
        account = self._password_account()
        if not account:
            return False
        try:
            keyring.delete_password(KEYRING_SERVICE, account)
        except Exception:
            return False
        return True

    def _apply_settings_dict(self, payload: dict[str, object]) -> None:
        self.source_mode.set(str(payload.get("source_mode", self.source_mode.get())))
        self.source_url.set(str(payload.get("source_url", self.source_url.get())))
        self.csv_path.set(str(payload.get("csv_path", self.csv_path.get())))
        self.smtp_provider.set(str(payload.get("smtp_provider", self.smtp_provider.get())))
        self.smtp_host.set(str(payload.get("smtp_host", self.smtp_host.get())))
        self.smtp_port.set(str(payload.get("smtp_port", self.smtp_port.get())))
        self.smtp_user.set(str(payload.get("smtp_user", self.smtp_user.get())))
        self.smtp_password.set(str(payload.get("smtp_password", self.smtp_password.get())))
        self.from_name.set(str(payload.get("from_name", self.from_name.get())))
        self.from_email.set(str(payload.get("from_email", self.from_email.get())))
        self.use_ssl.set(bool(payload.get("use_ssl", self.use_ssl.get())))
        self.delay_seconds.set(str(payload.get("delay_seconds", self.delay_seconds.get())))
        self.max_per_run.set(str(payload.get("max_per_run", self.max_per_run.get() or "")))
        self.sent_log_path.set(str(payload.get("sent_log_path", self.sent_log_path.get())))
        self.subject_template.set(str(payload.get("subject_template", self.subject_template.get())))
        self.search_query.set(str(payload.get("search_query", self.search_query.get())))
        attachments = payload.get("attachment_paths", [])
        self.attachment_paths = [str(item) for item in attachments] if isinstance(attachments, list) else []
        self.drive_client_secrets_path.set(str(payload.get("drive_client_secrets_path", self.drive_client_secrets_path.get())))
        self.drive_token_path.set(str(payload.get("drive_token_path", self.drive_token_path.get())))
        self.drive_folder_id.set(str(payload.get("drive_folder_id", self.drive_folder_id.get())))
        self.drive_threshold_mb.set(str(payload.get("drive_threshold_mb", self.drive_threshold_mb.get())))
        self.drive_make_public.set(bool(payload.get("drive_make_public", self.drive_make_public.get())))
        self.refresh_attachments_view()
        template_vars = payload.get("template_vars", {})
        if isinstance(template_vars, dict):
            for key, value in template_vars.items():
                if key in self.template_vars:
                    self.template_vars[key].set(str(value))
        body = str(payload.get("body_template", self.body_text.get("1.0", "end").rstrip("\n")))
        self.body_text.delete("1.0", "end")
        self.body_text.insert("1.0", body)

        legacy_password = str(payload.get("smtp_password", "") or "")
        secure_password = self._load_secure_password()
        if secure_password:
            self.smtp_password.set(secure_password)
            self.password_status_text.set("Password storage: loaded from system keychain")
        elif legacy_password:
            self.smtp_password.set(legacy_password)
            try:
                self._store_secure_password()
                self.password_status_text.set("Password storage: migrated to system keychain")
            except Exception as exc:  # noqa: BLE001
                self.password_status_text.set("Password storage: migration failed")
                self.log(f"Password migration failed: {exc}")
        else:
            self.smtp_password.set("")
            self.password_status_text.set("Password storage: system keychain")

    def save_settings_to_disk(self) -> None:
        try:
            stored = self._store_secure_password()
            SETTINGS_PATH.write_text(json.dumps(self._settings_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            self.log(f"Settings saved to {SETTINGS_PATH.name}.")
            self.status_text.set("Settings saved.")
            if stored:
                self.password_status_text.set("Password storage: system keychain")
            else:
                self.password_status_text.set("Password storage: not saved to keychain")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save settings failed", str(exc))

    def clear_saved_password(self) -> None:
        cleared = self._clear_secure_password()
        self.smtp_password.set("")
        self.password_status_text.set("Password storage: cleared" if cleared else "Password storage: not available")
        self.log("Saved password cleared." if cleared else "Password clear requested.")

    def load_settings_from_disk(self) -> None:
        if not SETTINGS_PATH.exists():
            self.log("No saved settings found.")
            return
        try:
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._apply_settings_dict(payload)
                self.log(f"Settings loaded from {SETTINGS_PATH.name}.")
                self.status_text.set("Settings loaded.")
                self.refresh_station_view()
                self.preview_selected_or_first()
            else:
                raise ValueError("Settings file has invalid format")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Load settings failed", str(exc))

    def on_close(self) -> None:
        try:
            self.save_settings_to_disk()
        finally:
            self.destroy()

    def reset_templates(self) -> None:
        self.subject_template.set(DEFAULT_SUBJECT)
        self.body_text.delete("1.0", "end")
        self.body_text.insert("1.0", DEFAULT_BODY)
        self.log("Templates reset to defaults.")

    def _browse_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose station CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.csv_path.set(path)
            self.source_mode.set("csv")

    def _browse_drive_client_secrets(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose Google OAuth client secrets JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.drive_client_secrets_path.set(path)
            if not self.drive_token_path.get().strip():
                self.drive_token_path.set(str(Path(path).with_name("drive_token.json")))

    def _browse_drive_token_path(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose token cache file",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.drive_token_path.set(path)

    def _build_config(self, dry_run: bool) -> Config:
        source_url = self.source_url.get().strip() if self.source_mode.get() == "url" else None
        input_csv = Path(self.csv_path.get().strip() or "stations.csv")
        max_per_run_text = self.max_per_run.get().strip()
        max_per_run = int(max_per_run_text) if max_per_run_text else None
        body_template = self.body_text.get("1.0", "end").rstrip("\n")
        from_email = self.from_email.get().strip() or self.smtp_user.get().strip()
        threshold_mb = float(self.drive_threshold_mb.get().strip() or "25")
        return Config(
            smtp_host=self.smtp_host.get().strip(),
            smtp_port=int(self.smtp_port.get().strip()),
            smtp_user=self.smtp_user.get().strip(),
            smtp_password=self.smtp_password.get(),
            from_name=self.from_name.get().strip() or "Roman Tishkov",
            from_email=from_email,
            use_ssl=self.use_ssl.get(),
            dry_run=dry_run,
            delay_seconds=float(self.delay_seconds.get().strip() or "0"),
            max_per_run=max_per_run,
            input_csv=input_csv,
            source_url=source_url,
            sent_log_csv=Path(self.sent_log_path.get().strip() or "sent_log.csv"),
            body_template_path=None,
            subject_template=self.subject_template.get(),
            attachment_paths=tuple(self.attachment_paths),
            drive_client_secrets_path=Path(self.drive_client_secrets_path.get().strip()) if self.drive_client_secrets_path.get().strip() else None,
            drive_token_path=Path(self.drive_token_path.get().strip()) if self.drive_token_path.get().strip() else None,
            drive_folder_id=self.drive_folder_id.get().strip() or None,
            drive_upload_threshold_bytes=max(int(threshold_mb * 1024 * 1024), 1),
            drive_make_public=self.drive_make_public.get(),
            body_template_text=body_template,
            subject_template_text=self.subject_template.get(),
        )

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "!disabled"
        for child in self.winfo_children():
            self._set_state_recursive(child, state)
        try:
            self.stop_button.state(["!disabled"])
        except Exception:
            pass
        self.log_text.configure(state="normal")
        self.log_text.configure(state="disabled")

    def _set_state_recursive(self, widget: tk.Widget, state: str) -> None:
        if widget is getattr(self, "stop_button", None):
            return
        if isinstance(widget, (ttk.Entry, ttk.Button, ttk.Checkbutton, ttk.Combobox, ttk.Radiobutton, ttk.Treeview)):
            try:
                widget.state([state])
            except Exception:
                pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, state)

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def load_contacts_async(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        self._stop_event.clear()
        self.status_text.set("Loading stations...")
        self._set_busy(True)
        self._worker = threading.Thread(target=self._load_contacts_worker, daemon=True)
        self._worker.start()

    def _load_contacts_worker(self) -> None:
        try:
            cfg = self._build_config(dry_run=True)
            contacts = load_contacts(cfg)
            self._ui_queue.put(("contacts_loaded", contacts))
        except Exception as exc:  # noqa: BLE001
            self._ui_queue.put(("error", f"Failed to load stations: {exc}"))
        finally:
            self._ui_queue.put(("ui", "idle"))

    def refresh_station_view(self) -> None:
        query = self.search_query.get().strip().lower()
        self.station_tree.delete(*self.station_tree.get_children())
        filtered = [row for row in self.contacts if self._row_matches(row, query)]
        for idx, row in enumerate(filtered):
            genres = row.get("genres") or row.get("genre_description", "")
            genre_display = ", ".join(genres[:3]) if isinstance(genres, list) else str(genres)
            self.station_tree.insert(
                "",
                "end",
                iid=f"row-{idx}",
                values=(
                    row.get("station_name", ""),
                    row.get("email", ""),
                    row.get("music_director", ""),
                    row.get("city", ""),
                    row.get("state", ""),
                    genre_display,
                ),
            )
        self.contact_count_text.set(f"{len(filtered)} of {len(self.contacts)} contacts shown")

    def _row_matches(self, row: dict[str, str], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(
            [
                row.get("station_name", ""),
                row.get("email", ""),
                row.get("music_director", ""),
                row.get("city", ""),
                row.get("state", ""),
                " ".join(row.get("genres", [])) if isinstance(row.get("genres"), list) else str(row.get("genre_description", "")),
            ]
        ).lower()
        return query in haystack

    def preview_selected_or_first(self) -> None:
        row = self._get_selected_row() or (self.contacts[0] if self.contacts else None)
        self.preview_text.delete("1.0", "end")
        if not row:
            self.preview_text.insert("1.0", "Load stations first, then select one to preview.")
            return
        context = merge_context(row)
        context.update({key: var.get() for key, var in self.template_vars.items() if var.get()})
        subject = render_text(self.subject_template.get(), context)
        body = render_text(self.body_text.get("1.0", "end").rstrip("\n"), context)
        preview = (
            f"To: {row.get('email', '')}\n"
            f"Subject: {subject}\n\n"
            f"{body}\n"
        )
        if self.attachment_paths:
            preview += "\nAttachments:\n" + "\n".join(f"- {Path(path).name}" for path in self.attachment_paths) + "\n"
        self.preview_text.insert("1.0", preview)

    def _get_selected_row(self) -> dict[str, str] | None:
        selection = self.station_tree.selection()
        if not selection:
            return None
        selected_index = self.station_tree.index(selection[0])
        visible = self._visible_contacts()
        if 0 <= selected_index < len(visible):
            return visible[selected_index]
        return None

    def _visible_contacts(self) -> list[dict[str, str]]:
        query = self.search_query.get().strip().lower()
        return [row for row in self.contacts if self._row_matches(row, query)]

    def _template_overrides(self) -> dict[str, str]:
        return {key: var.get() for key, var in self.template_vars.items() if var.get().strip()}

    def refresh_attachments_view(self) -> None:
        if not hasattr(self, "attach_listbox"):
            return
        self.attach_listbox.delete(0, "end")
        total_bytes = 0
        for path in self.attachment_paths:
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = 0
            total_bytes += size
            self.attach_listbox.insert("end", f"{Path(path).name}  ({format_bytes(size)})")
        if self.attachment_paths:
            self.attachment_summary_text.set(
                f"{len(self.attachment_paths)} file(s), total {format_bytes(total_bytes)}. Safe send limit: {format_bytes(MAX_SAFE_ATTACHMENT_BYTES)}."
            )
        else:
            self.attachment_summary_text.set("No attachments selected.")

    def add_attachments(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose files to attach",
            filetypes=[("All files", "*.*")],
        )
        if not paths:
            return
        for path in paths:
            if path not in self.attachment_paths:
                self.attachment_paths.append(path)
        self.refresh_attachments_view()
        self.log(f"Added {len(paths)} attachment(s).")

    def remove_selected_attachments(self) -> None:
        if not hasattr(self, "attach_listbox"):
            return
        selected = list(self.attach_listbox.curselection())
        if not selected:
            return
        for idx in reversed(selected):
            if 0 <= idx < len(self.attachment_paths):
                self.attachment_paths.pop(idx)
        self.refresh_attachments_view()
        self.log("Removed selected attachments.")

    def clear_attachments(self) -> None:
        self.attachment_paths.clear()
        self.refresh_attachments_view()
        self.log("Cleared all attachments.")

    def start_send(self, dry_run: bool) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        try:
            cfg = self._build_config(dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Invalid settings", str(exc))
            return
        if not cfg.source_url and not cfg.input_csv.exists():
            messagebox.showerror("Missing source", "Choose a valid CSV file or switch to the public source URL.")
            return
        if not cfg.from_email:
            messagebox.showerror("Missing sender", "Set From email or SMTP user.")
            return
        if not cfg.smtp_password and not dry_run:
            messagebox.showerror("Missing password", "Set the SMTP password or load it from the system keychain.")
            return
        if not dry_run:
            try:
                local_attachments, drive_attachments = split_attachments_by_size(
                    tuple(self.attachment_paths),
                    threshold_bytes=cfg.drive_upload_threshold_bytes,
                )
                if local_attachments:
                    validate_attachment_size_limit(tuple(str(path) for path in local_attachments))
                if drive_attachments and not cfg.drive_client_secrets_path:
                    messagebox.showerror(
                        "Google Drive required",
                        "Some attachments are larger than the Drive threshold, but Google Drive is not configured yet.",
                    )
                    return
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Attachments too large", str(exc))
                return

        self._stop_event.clear()
        self._set_busy(True)
        self.status_text.set("Preparing batch...")
        self._worker = threading.Thread(target=self._send_worker, args=(cfg,), daemon=True)
        self._worker.start()

    def _send_worker(self, cfg: Config) -> None:
        try:
            contacts = self.contacts or load_contacts(cfg)
            if cfg.max_per_run is not None:
                contacts = contacts[: cfg.max_per_run]

            def log_cb(msg: str) -> None:
                self._ui_queue.put(("log", msg))

            def preview_cb(_recipient: str, _context: dict[str, str], _subject: str, _body: str, _message: Any) -> None:
                return

            sent_count, skipped_count = run_campaign(
                cfg,
                contacts,
                on_log=log_cb,
                on_preview=preview_cb,
                stop_event=self._stop_event,
                template_overrides=self._template_overrides(),
            )
            self._ui_queue.put(("done", {"sent": sent_count, "skipped": skipped_count}))
        except Exception as exc:  # noqa: BLE001
            self._ui_queue.put(("error", f"Send failed: {exc}"))
        finally:
            self._ui_queue.put(("ui", "idle"))

    def stop_run(self) -> None:
        self._stop_event.set()
        self.log("Stop requested.")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "log":
                    self.log(str(payload))
                elif kind == "error":
                    messagebox.showerror("SendMail", str(payload))
                    self.status_text.set("Error.")
                elif kind == "contacts_loaded":
                    self.contacts = list(payload)
                    self.refresh_station_view()
                    self.status_text.set(f"Loaded {len(self.contacts)} contacts.")
                    self.log(f"Loaded {len(self.contacts)} contacts.")
                    self.preview_selected_or_first()
                elif kind == "done":
                    self.status_text.set(f"Done. Sent {payload['sent']}, skipped {payload['skipped']}.")
                    self.log(f"Done. Sent {payload['sent']}, skipped {payload['skipped']}.")
                elif kind == "ui":
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)


def main() -> int:
    app = SendMailApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
