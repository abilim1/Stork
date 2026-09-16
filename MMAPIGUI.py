"""Moomoo history pull GUI."""

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

POLL_MS = 2000
SAVES = ROOT / "saves"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Moomoo History Pull")
        self.geometry("780x540")
        self.minsize(640, 460)

        SAVES.mkdir(exist_ok=True)

        self.log_q: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.pull_locked = False
        self.opend_ready = False

        self._build()
        self.after(120, self._drain_log)
        self.after(200, self._poll_opend)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        status_row = ttk.Frame(self)
        status_row.pack(fill=tk.X, **pad)
        ttk.Label(status_row, text="OpenD").pack(side=tk.LEFT)
        self.opend_dot = tk.Canvas(status_row, width=12, height=12, highlightthickness=0)
        self.opend_dot.pack(side=tk.LEFT, padx=(8, 6))
        self.opend_text = tk.StringVar(value="Checking OpenD.exe...")
        ttk.Label(status_row, textvariable=self.opend_text).pack(side=tk.LEFT)

        top = ttk.Frame(self)
        top.pack(fill=tk.X, **pad)

        ttk.Label(top, text="Symbol").pack(side=tk.LEFT)
        self.symbol = tk.StringVar(value="US.MU")
        ttk.Entry(top, textvariable=self.symbol, width=16).pack(side=tk.LEFT, padx=(6, 16))

        ttk.Label(top, text="Interval").pack(side=tk.LEFT)
        self.interval = tk.StringVar(value="15m")
        interval_box = ttk.Combobox(
            top, textvariable=self.interval, values=INTERVALS, width=10, state="readonly"
        )
        interval_box.pack(side=tk.LEFT, padx=6)
        interval_box.bind("<<ComboboxSelected>>", self._on_interval)

        self.extended = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="US pre / after hours", variable=self.extended).pack(
            side=tk.LEFT, padx=16
        )

        period = ttk.LabelFrame(self, text="Period  (from → to)")
        period.pack(fill=tk.X, padx=10, pady=4)

        today = date.today()
        self.from_row = DateRow(period, "From", date(today.year, 1, 1))
        self.from_row.pack(anchor=tk.W, padx=8, pady=4)
        self.to_row = DateRow(period, "To", today)
        self.to_row.pack(anchor=tk.W, padx=8, pady=4)

        self.period_hint = ttk.Label(
            period, text="Day + month + year. API dates are YYYY-MM-DD."
        )
        self.period_hint.pack(anchor=tk.W, padx=8, pady=(0, 6))

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, **pad)
        self.pull_btn = ttk.Button(actions, text="Pull", command=self._start_pull)
        self.pull_btn.pack(side=tk.LEFT)
        ttk.Label(actions, text=f"API {HOST}:{PORT}   saves → {SAVES.name}/").pack(
            side=tk.LEFT, padx=12
        )

        log_frame = ttk.LabelFrame(self, text="Log status")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self.log = tk.Text(log_frame, height=14, wrap=tk.WORD, state=tk.DISABLED)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.status = tk.StringVar(value="Idle. Waiting for Pull.")
        ttk.Label(self, textvariable=self.status, anchor=tk.W).pack(fill=tk.X, padx=10, pady=(0, 8))

        self._log("Ready. CSVs go to saves/. Pull stays grey until OpenD is open.")

    def _set_dot(self, ready: bool) -> None:
        self.opend_dot.delete("all")
        color = "#2e7d32" if ready else "#c62828"
        self.opend_dot.create_oval(2, 2, 10, 10, fill=color, outline=color)

    def _refresh_pull_button(self) -> None:
        allow = self.opend_ready and not self.busy and not self.pull_locked
        self.pull_btn.config(state=tk.NORMAL if allow else tk.DISABLED)

    def _poll_opend(self) -> None:
        ready, text = opend_state(HOST, PORT)
        self.opend_ready = ready
        self._set_dot(ready)
        if self.pull_locked:
            self.opend_text.set("Pull locked after failure  •  restart app to try again")
        else:
            self.opend_text.set(text)
        self._refresh_pull_button()
        self.after(POLL_MS, self._poll_opend)

    def _on_interval(self, _event=None) -> None:
        monthly = self.interval.get() == "monthly"
        self.from_row.set_day_visible(not monthly)
        self.to_row.set_day_visible(not monthly)
        if monthly:
            self.period_hint.config(
                text="Monthly: pick month and year only. From = 1st of that month, To = last day."
            )
        else:
            self.period_hint.config(text="Day + month + year. API dates are YYYY-MM-DD.")

    def _log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_q.put(f"[{stamp}] {msg}")

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
            self.status.set("OpenD is not open. Pull is disabled.")
            self._log("Blocked: OpenD.exe is not open")
            return

        code = self.symbol.get().strip().upper()
        interval = self.interval.get()
        monthly = interval == "monthly"
        if not code or "." not in code:
            self.status.set("Error: symbol must look like US.MU")
            self._log("Rejected: symbol must be MARKET.CODE, e.g. US.MU")
            return
        try:
            start = self.from_row.as_date(monthly, end_of_month=False)
            end = self.to_row.as_date(monthly, end_of_month=True)
        except ValueError as exc:
            self.status.set(f"Error: {exc}")
            self._log(f"Rejected date: {exc}")
            return
        if start > end:
            self.status.set("Error: From is after To")
            self._log("Rejected: From date is after To date")
            return

        self.busy = True
        self._refresh_pull_button()
        self.status.set("Connecting to OpenD...")
        self._log(f"Pull start  {code}  {interval}  {start} -> {end}")
        threading.Thread(
            target=self._pull_worker,
            args=(code, interval, start.isoformat(), end.isoformat(), self.extended.get()),
            daemon=True,
        ).start()

    def _lock_after_failure(self, err: str) -> None:
        self.pull_locked = True
        self.busy = False
        self._refresh_pull_button()
        self.status.set("Failed. Pull locked. Restart the app after fixing OpenD / login.")
        self._log(f"Failed (no retry): {err}")
        self._log(LOGIN_PROMPT.replace("\n", " | "))
        self.after(0, lambda: messagebox.showerror("OpenD / login", LOGIN_PROMPT + f"\n\n{err}"))

    def _pull_worker(self, code: str, interval: str, start: str, end: str, extended: bool) -> None:
        try:
            ktype = kltype_for(interval)
            self._log(f"KLType mapped to {ktype}")
            out_path = SAVES / csv_name(code, interval)
            n = pull_history(code, start, end, ktype, extended, out_path, self._log)
            self.status.set(f"Done: {n} bars -> saves/{out_path.name}" if n else "Done: 0 bars")
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
            self.status.set(f"Failed: {exc}")
            self.busy = False


if __name__ == "__main__":
    App().mainloop()
