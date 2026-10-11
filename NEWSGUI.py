"""Stork — news search GUI. Keyword search, then a local date window."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.history_client import (  # noqa: E402
    HOST,
    LOGIN_PROMPT,
    PORT,
    OpenDSessionError,
    looks_like_login_issue,
)
from core.news_client import (  # noqa: E402
    DEFAULT_KEYWORDS,
    MAX_COUNT_CAP,
    SUBTYPES,
    pull_news,
    split_keywords,
)
from core.opend_status import opend_state  # noqa: E402
from gui.period_widgets import DateRow  # noqa: E402
from gui.theme import (  # noqa: E402
    BAD,
    BG,
    BTN,
    BTN_FG,
    BTN_OFF,
    BTN_OFF_FG,
    CARD,
    LINE,
    LOG_BG,
    MONO,
    MUTED,
    OK,
    SURFACE,
    TEXT,
    UI_FONT,
    apply,
)

POLL_MS = 2000
SAVES = ROOT / "saves"
WIN_W, WIN_H = 860, 760

COLUMNS = (
    ("date", "Date", 96),
    ("keyword", "Keyword", 120),
    ("source", "Source", 120),
    ("title", "Title", 340),
    ("views", "Views", 60),
    ("url", "URL", 180),
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stork news")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.minsize(760, 640)
        apply(self)

        SAVES.mkdir(exist_ok=True)

        self.log_q: queue.Queue[str] = queue.Queue()
        self.result_q: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.pull_locked = False
        self.opend_ready = False

        self._build()
        self.after(120, self._drain_log)
        self.after(200, self._poll_opend)

    def _card(self, parent) -> tk.Frame:
        wrap = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        wrap.pack(fill=tk.X, padx=20, pady=(0, 10))
        inner = ttk.Frame(wrap, style="Card.TFrame")
        inner.pack(fill=tk.X, padx=14, pady=12)
        return inner

    def _build(self) -> None:
        tk.Frame(self, bg=BG, height=18).pack(fill=tk.X)

        head = ttk.Frame(self)
        head.pack(fill=tk.X, padx=20, pady=(0, 12))
        ttk.Label(head, text="Stork", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(head, text="News by keyword, kept inside a date window", style="Muted.TLabel").pack(
            anchor=tk.W
        )

        status = self._card(self)
        row = ttk.Frame(status, style="Card.TFrame")
        row.pack(fill=tk.X)
        self.opend_dot = tk.Canvas(
            row, width=10, height=10, bg=SURFACE, highlightthickness=0, bd=0
        )
        self.opend_dot.pack(side=tk.LEFT, pady=2)
        self.opend_text = tk.StringVar(value="Checking OpenD…")
        ttk.Label(row, textvariable=self.opend_text, style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=8
        )

        fields = self._card(self)
        ttk.Label(fields, text="Keywords", style="CardBold.TLabel").pack(anchor=tk.W)
        ttk.Label(
            fields,
            text="One per line. OpenD returns the latest matches, then the dates below keep a window. Raise Max if a long range comes back short.",
            style="CardMuted.TLabel",
            wraplength=700,
        ).pack(anchor=tk.W, pady=(2, 8))
        self.keywords = tk.Text(
            fields,
            height=5,
            wrap=tk.WORD,
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            bd=0,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=LINE,
            font=UI_FONT,
            padx=8,
            pady=6,
        )
        self.keywords.pack(fill=tk.X)
        self.keywords.insert("1.0", "\n".join(DEFAULT_KEYWORDS))

        opts = ttk.Frame(fields, style="Card.TFrame")
        opts.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(opts, text="Type", style="CardBold.TLabel").pack(side=tk.LEFT)
        self.subtype = tk.StringVar(value="ALL")
        ttk.Combobox(
            opts, textvariable=self.subtype, values=SUBTYPES, width=10, state="readonly"
        ).pack(side=tk.LEFT, padx=(8, 18))
        ttk.Label(opts, text="Max per keyword", style="CardBold.TLabel").pack(side=tk.LEFT)
        self.max_count = tk.StringVar(value="50")
        ttk.Entry(opts, textvariable=self.max_count, width=6).pack(side=tk.LEFT, padx=(8, 0))

        period = self._card(self)
        period_head = ttk.Frame(period, style="Card.TFrame")
        period_head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(period_head, text="Period", style="CardBold.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            period_head,
            text="5/13 means the latest 5/13 on or before today.",
            style="CardMuted.TLabel",
        ).pack(side=tk.LEFT, padx=10)

        today = date.today()
        dates = ttk.Frame(period, style="Card.TFrame")
        dates.pack(anchor=tk.W)
        self.from_row = DateRow(dates, date(today.year, 1, 1))
        self.from_row.pack(side=tk.LEFT)
        ttk.Label(dates, text="to", style="CardMuted.TLabel").pack(side=tk.LEFT, padx=14)
        self.to_row = DateRow(dates, today)
        self.to_row.pack(side=tk.LEFT)

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=20, pady=(2, 10))
        self.pull_btn = tk.Button(
            actions,
            text="Pull",
            command=self._start_pull,
            font=UI_FONT,
            bd=0,
            padx=22,
            pady=7,
            cursor="hand2",
            activebackground=BTN,
            activeforeground=BTN_FG,
        )
        self.pull_btn.pack(side=tk.LEFT)
        self.status = tk.StringVar(value="Waiting")
        ttk.Label(actions, textvariable=self.status, style="Muted.TLabel").pack(
            side=tk.LEFT, padx=14
        )

        table_wrap = tk.Frame(self, bg=LINE, highlightthickness=0)
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))
        self.tree = ttk.Treeview(
            table_wrap,
            columns=[key for key, _label, _width in COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            stretch = key == "title"
            self.tree.column(key, width=width, minwidth=60, stretch=stretch, anchor=tk.W)
        scroll = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._open_url)

        log_wrap = tk.Frame(self, bg=LINE, highlightthickness=0)
        log_wrap.pack(fill=tk.X, padx=20, pady=(0, 8))
        self.log = tk.Text(
            log_wrap,
            height=6,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=LOG_BG,
            fg=MUTED,
            insertbackground=TEXT,
            bd=0,
            highlightthickness=0,
            font=MONO,
            padx=12,
            pady=8,
        )
        self.log.pack(fill=tk.X)

        tk.Frame(self, bg=BG, height=12).pack(fill=tk.X)
        self._refresh_pull_button()
        self._log("Ready. Pull stays off until OpenD is open. Double-click a row to open its link.")

    def _set_dot(self, ready: bool) -> None:
        self.opend_dot.delete("all")
        color = OK if ready else BAD
        self.opend_dot.create_oval(1, 1, 9, 9, fill=color, outline=color)

    def _refresh_pull_button(self) -> None:
        allow = self.opend_ready and not self.busy and not self.pull_locked
        if allow:
            self.pull_btn.config(
                state=tk.NORMAL, bg=BTN, fg=BTN_FG, disabledforeground=BTN_OFF_FG
            )
        else:
            self.pull_btn.config(
                state=tk.DISABLED, bg=BTN_OFF, fg=BTN_OFF_FG, disabledforeground=BTN_OFF_FG
            )

    def _poll_opend(self) -> None:
        ready, text = opend_state(HOST, PORT)
        self.opend_ready = ready
        self._set_dot(ready)
        if self.pull_locked:
            self.opend_text.set("Locked after failure  ·  restart after fixing OpenD")
        else:
            self.opend_text.set(text)
        self._refresh_pull_button()
        self.after(POLL_MS, self._poll_opend)

    def _log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_q.put(f"{stamp}   {msg}")

    def _drain_log(self) -> None:
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log.configure(state=tk.NORMAL)
                self.log.insert(tk.END, line + "\n")
                self.log.see(tk.END)
                self.log.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self.result_q.get_nowait()
                if kind == "ok":
                    result = payload
                    if result.path is None:
                        self.status.set(f"0 in range  ·  API returned {result.fetched}")
                    else:
                        self.status.set(f"{result.saved} items  ·  saves/{result.path.name}")
                    self._show_rows(result.rows)
                elif kind == "fail":
                    self._lock_after_failure(payload)
                elif kind == "error":
                    self._log(f"Failed: {payload}")
                    self.status.set(payload)
                    self.busy = False
                    self._refresh_pull_button()
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _show_rows(self, rows: list[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    row["date"],
                    row["keyword"],
                    row["source"],
                    row["title"],
                    row["view_count"],
                    row["url"],
                ),
            )
        self.busy = False
        self._refresh_pull_button()

    def _open_url(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        url = str(self.tree.item(selected[0], "values")[-1])
        if url.startswith("http"):
            webbrowser.open(url)

    def _start_pull(self) -> None:
        if self.busy or self.pull_locked:
            return
        if not self.opend_ready:
            self.status.set("OpenD is not open")
            self._log("Blocked: OpenD.exe is not open")
            return
        try:
            keywords = split_keywords(self.keywords.get("1.0", tk.END))
            if not keywords:
                raise ValueError("Enter at least one keyword")
            max_count = int(self.max_count.get().strip())
            if not 1 <= max_count <= MAX_COUNT_CAP:
                raise ValueError(f"Max per keyword is 1–{MAX_COUNT_CAP}")
            start = self.from_row.as_date(False)
            end = self.to_row.as_date(False)
        except ValueError as exc:
            self.status.set(str(exc))
            self._log(f"Rejected: {exc}")
            return
        if start > end:
            self.status.set("From is after To")
            self._log("Rejected: From date is after To date")
            return

        subtype = self.subtype.get()
        out_path = SAVES / f"news_{start.isoformat()}_{end.isoformat()}.csv"
        self.busy = True
        self._refresh_pull_button()
        self.status.set("Connecting…")
        self._log(f"Pull  {len(keywords)} keywords  {subtype}  {start} → {end}")
        threading.Thread(
            target=self._pull_worker,
            args=(keywords, start, end, max_count, subtype, out_path),
            daemon=True,
        ).start()

    def _lock_after_failure(self, err: str) -> None:
        self.pull_locked = True
        self.busy = False
        self._refresh_pull_button()
        self.status.set("Failed  ·  restart after OpenD / login")
        self._log(f"Failed (no retry): {err}")
        self._log(LOGIN_PROMPT.replace("\n", "  ·  "))
        messagebox.showerror("OpenD / login", LOGIN_PROMPT + f"\n\n{err}")

    def _pull_worker(self, keywords, start, end, max_count, subtype, out_path: Path) -> None:
        try:
            result = pull_news(keywords, start, end, max_count, subtype, out_path, self._log)
            self.result_q.put(("ok", result))
        except OpenDSessionError as exc:
            self.result_q.put(("fail", str(exc)))
        except Exception as exc:
            hint = str(exc)
            if looks_like_login_issue(hint):
                self.result_q.put(("fail", hint))
                return
            self.result_q.put(("error", f"{type(exc).__name__}: {exc}"))


if __name__ == "__main__":
    App().mainloop()
