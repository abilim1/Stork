"""Stork — stock price history GUI."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
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
    pull_history,
)
from core.kline_map import INTERVALS, csv_name, kltype_for  # noqa: E402
from core.opend_status import opend_state  # noqa: E402
from gui.period_widgets import DateRow  # noqa: E402
from gui.theme import (  # noqa: E402
    BAD,
    BG,
    BTN,
    BTN_FG,
    BTN_OFF,
    BTN_OFF_FG,
    LINE,
    LOG_BG,
    MONO,
    MUTED,
    OK,
    TEXT,
    SURFACE,
    UI_FONT,
    apply,
)

POLL_MS = 2000
SAVES = ROOT / "saves"
WIN_W, WIN_H = 720, 560


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stork")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        apply(self)

        SAVES.mkdir(exist_ok=True)

        self.log_q: queue.Queue[str] = queue.Queue()
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
        ttk.Label(head, text="Stock price history", style="Muted.TLabel").pack(anchor=tk.W)

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
        line1 = ttk.Frame(fields, style="Card.TFrame")
        line1.pack(fill=tk.X)
        ttk.Label(line1, text="Symbol", style="CardBold.TLabel").pack(side=tk.LEFT)
        self.symbol = tk.StringVar(value="US.MU")
        ttk.Entry(line1, textvariable=self.symbol, width=12).pack(side=tk.LEFT, padx=(8, 18))
        ttk.Label(line1, text="Interval", style="CardBold.TLabel").pack(side=tk.LEFT)
        self.interval = tk.StringVar(value="15m")
        interval_box = ttk.Combobox(
            line1, textvariable=self.interval, values=INTERVALS, width=8, state="readonly"
        )
        interval_box.pack(side=tk.LEFT, padx=(8, 16))
        interval_box.bind("<<ComboboxSelected>>", self._on_interval)
        self.extended = tk.BooleanVar(value=False)
        ttk.Checkbutton(line1, text="Pre / after hours", variable=self.extended).pack(side=tk.LEFT)

        period = self._card(self)
        period_head = ttk.Frame(period, style="Card.TFrame")
        period_head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(period_head, text="Period", style="CardBold.TLabel").pack(side=tk.LEFT)
        self.period_hint = ttk.Label(
            period_head, text="Year month day", style="CardMuted.TLabel"
        )
        self.period_hint.pack(side=tk.LEFT, padx=10)

        today = date.today()
        dates = ttk.Frame(period, style="Card.TFrame")
        dates.pack()
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

        log_wrap = tk.Frame(self, bg=LINE, highlightthickness=0)
        log_wrap.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))
        self.log = tk.Text(
            log_wrap,
            height=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=LOG_BG,
            fg=MUTED,
            insertbackground=TEXT,
            bd=0,
            highlightthickness=0,
            font=MONO,
            padx=12,
            pady=10,
        )
        self.log.pack(fill=tk.BOTH, expand=True)

        tk.Frame(self, bg=BG, height=12).pack(fill=tk.X)
        self._log("Ready. Pull stays off until OpenD is open.")

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

    def _on_interval(self, _event=None) -> None:
        monthly = self.interval.get() == "monthly"
        self.from_row.set_day_visible(not monthly)
        self.to_row.set_day_visible(not monthly)
        if monthly:
            self.period_hint.config(text="Year month")
        else:
            self.period_hint.config(text="Year month day")

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
        self.after(120, self._drain_log)

    def _start_pull(self) -> None:
        if self.busy or self.pull_locked:
            return
        if not self.opend_ready:
            self.status.set("OpenD is not open")
            self._log("Blocked: OpenD.exe is not open")
            return

        code = self.symbol.get().strip().upper()
        interval = self.interval.get()
        monthly = interval == "monthly"
        if not code or "." not in code:
            self.status.set("Symbol must look like US.MU")
            self._log("Rejected: symbol must be MARKET.CODE")
            return
        try:
            start = self.from_row.as_date(monthly, end_of_month=False)
            end = self.to_row.as_date(monthly, end_of_month=True)
        except ValueError as exc:
            self.status.set(str(exc))
            self._log(f"Rejected date: {exc}")
            return
        if start > end:
            self.status.set("From is after To")
            self._log("Rejected: From date is after To date")
            return

        self.busy = True
        self._refresh_pull_button()
        self.status.set("Connecting…")
        self._log(f"Pull  {code}  {interval}  {start} → {end}")
        threading.Thread(
            target=self._pull_worker,
            args=(code, interval, start.isoformat(), end.isoformat(), self.extended.get()),
            daemon=True,
        ).start()

    def _lock_after_failure(self, err: str) -> None:
        self.pull_locked = True
        self.busy = False
        self._refresh_pull_button()
        self.status.set("Failed  ·  restart after OpenD / login")
        self._log(f"Failed (no retry): {err}")
        self._log(LOGIN_PROMPT.replace("\n", "  ·  "))
        self.after(0, lambda: messagebox.showerror("OpenD / login", LOGIN_PROMPT + f"\n\n{err}"))

    def _pull_worker(self, code: str, interval: str, start: str, end: str, extended: bool) -> None:
        try:
            ktype = kltype_for(interval)
            self._log(f"KLType {ktype}")
            out_path = SAVES / csv_name(code, interval)
            n = pull_history(code, start, end, ktype, extended, out_path, self._log)
            self.status.set(f"{n} bars  ·  saves/{out_path.name}" if n else "0 bars")
            self.busy = False
        except OpenDSessionError as exc:
            self._lock_after_failure(str(exc))
            return
        except Exception as exc:
            hint = str(exc)
            if looks_like_login_issue(hint):
                self._lock_after_failure(hint)
                return
            self._log(f"Failed: {type(exc).__name__}: {exc}")
            self.status.set(str(exc))
            self.busy = False


if __name__ == "__main__":
    App().mainloop()
