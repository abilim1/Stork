"""
Moomoo history pull GUI.

Requires OpenD running at 127.0.0.1:11111 and:
  pip install moomoo-api pandas
"""

from __future__ import annotations

import calendar
import queue
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import ttk

HOST = "127.0.0.1"
PORT = 11111
MAX_COUNT = 1000

INTERVALS = [
    "1m",
    "3m",
    "5m",
    "10m",
    "15m",
    "30m",
    "1 hour",
    "2 hour",
    "4 hour",
    "daily",
    "monthly",
]

MONTHS = [
    ("01", "Jan"),
    ("02", "Feb"),
    ("03", "Mar"),
    ("04", "Apr"),
    ("05", "May"),
    ("06", "Jun"),
    ("07", "Jul"),
    ("08", "Aug"),
    ("09", "Sep"),
    ("10", "Oct"),
    ("11", "Nov"),
    ("12", "Dec"),
]


def kltype_for(label: str):
    from moomoo import KLType

    mapping = {
        "1m": "K_1M",
        "3m": "K_3M",
        "5m": "K_5M",
        "10m": "K_10M",
        "15m": "K_15M",
        "30m": "K_30M",
        "1 hour": "K_60M",
        "2 hour": "K_120M",
        "4 hour": "K_240M",
        "daily": "K_DAY",
        "monthly": "K_MON",
    }
    name = mapping[label]
    if not hasattr(KLType, name):
        raise RuntimeError(f"This moomoo-api build has no KLType.{name}")
    return getattr(KLType, name)


def safe_filename(code: str, interval: str) -> str:
    safe_code = code.replace(".", "_")
    safe_int = interval.replace(" ", "")
    return f"{safe_code}_{safe_int}.csv"


class DateRow(ttk.Frame):
    def __init__(self, master, label: str, initial: date):
        super().__init__(master)
        ttk.Label(self, text=label, width=6).pack(side=tk.LEFT)
        self.year = tk.StringVar(value=str(initial.year))
        self.month = tk.StringVar(value=f"{initial.month:02d}")
        self.day = tk.StringVar(value=f"{initial.day:02d}")

        years = [str(y) for y in range(date.today().year, 1999, -1)]
        months = [f"{m:02d}" for m in range(1, 13)]
        days = [f"{d:02d}" for d in range(1, 32)]

        ttk.Combobox(self, textvariable=self.year, values=years, width=6, state="readonly").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Combobox(self, textvariable=self.month, values=months, width=4, state="readonly").pack(
            side=tk.LEFT, padx=2
        )
        self.day_box = ttk.Combobox(
            self, textvariable=self.day, values=days, width=4, state="readonly"
        )
        self.day_box.pack(side=tk.LEFT, padx=2)

    def set_day_visible(self, visible: bool) -> None:
        if visible:
            self.day_box.pack(side=tk.LEFT, padx=2)
        else:
            self.day_box.pack_forget()

    def as_date(self, monthly: bool, end_of_month: bool = False) -> date:
        y = int(self.year.get())
        m = int(self.month.get())
        if monthly:
            last = calendar.monthrange(y, m)[1]
            d = last if end_of_month else 1
        else:
            d = int(self.day.get())
            last = calendar.monthrange(y, m)[1]
            d = min(d, last)
        return date(y, m, d)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Moomoo History Pull")
        self.geometry("760x520")
        self.minsize(640, 440)

        self.log_q: queue.Queue[str] = queue.Queue()
        self.busy = False

        self._build()
        self.after(120, self._drain_log)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

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
        start_default = date(today.year, 1, 1)
        self.from_row = DateRow(period, "From", start_default)
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
        ttk.Label(actions, text="OpenD must be running on 127.0.0.1:11111").pack(
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

        self._log("Ready. Set symbol, period, interval, then Pull.")

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

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.pull_btn.config(state=tk.DISABLED if busy else tk.NORMAL)

    def _start_pull(self) -> None:
        if self.busy:
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

        self._set_busy(True)
        self.status.set("Connecting to OpenD...")
        self._log(f"Pull start  {code}  {interval}  {start} -> {end}")
        threading.Thread(
            target=self._pull_worker,
            args=(code, interval, start.isoformat(), end.isoformat(), self.extended.get()),
            daemon=True,
        ).start()

    def _pull_worker(self, code: str, interval: str, start: str, end: str, extended: bool) -> None:
        try:
            from moomoo import RET_OK, AuType, OpenQuoteContext
        except Exception as exc:
            self._log(f"Import failed: {exc}")
            self._log("Install with: pip install moomoo-api pandas")
            self.status.set("Failed: moomoo-api not installed")
            self._set_busy(False)
            return

        quote_ctx = None
        try:
            ktype = kltype_for(interval)
            self._log(f"KLType mapped to {ktype}")
            self.status.set("Opening quote context...")

            quote_ctx = OpenQuoteContext(host=HOST, port=PORT)
            self._log(f"Connected OpenQuoteContext {HOST}:{PORT}")

            ret, used_remain = quote_ctx.get_history_kl_quota(get_detail=False)
            if ret == RET_OK:
                used, remain = used_remain[0], used_remain[1]
                self._log(f"API quota  used={used}  remain={remain}")
            else:
                self._log(f"API get_history_kl_quota: {used_remain}")

            pages = []
            page_req_key = None
            page = 0
            while True:
                page += 1
                self.status.set(f"Requesting page {page}...")
                self._log(f"API request_history_kline page={page} start={start} end={end}")
                ret, data, page_req_key = quote_ctx.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=ktype,
                    autype=AuType.QFQ,
                    max_count=MAX_COUNT,
                    page_req_key=page_req_key,
                    extended_time=extended,
                )
                if ret != RET_OK:
                    self._log(f"API error page {page}: {data}")
                    self.status.set(f"API error: {data}")
                    return

                n = 0 if data is None else len(data)
                self._log(f"API ok page {page}: {n} bars")
                if n == 0:
                    break
                pages.append(data)
                if page_req_key is None:
                    self._log("API page_req_key empty — last page")
                    break

            if not pages:
                self._log("No bars returned")
                self.status.set("Done: 0 bars")
                return

            import pandas as pd

            out = pd.concat(pages, ignore_index=True)
            if "time_key" in out.columns:
                out = out.drop_duplicates(subset=["time_key"], keep="last")
                out = out.sort_values("time_key").reset_index(drop=True)
            out_path = Path(__file__).resolve().parent / safe_filename(code, interval)
            out.to_csv(out_path, index=False)
            first = out["time_key"].iloc[0] if "time_key" in out.columns else "?"
            last = out["time_key"].iloc[-1] if "time_key" in out.columns else "?"
            self._log(f"Saved {len(out)} bars -> {out_path.name}")
            self._log(f"Range {first} -> {last}")

            ret, used_remain = quote_ctx.get_history_kl_quota(get_detail=False)
            if ret == RET_OK:
                self._log(f"API quota after  used={used_remain[0]}  remain={used_remain[1]}")
            self.status.set(f"Done: {len(out)} bars -> {out_path.name}")
        except Exception as exc:
            self._log(f"Failed: {type(exc).__name__}: {exc}")
            self.status.set(f"Failed: {exc}")
        finally:
            if quote_ctx is not None:
                try:
                    quote_ctx.close()
                    self._log("Quote context closed")
                except Exception as exc:
                    self._log(f"Close warning: {exc}")
            self._set_busy(False)


if __name__ == "__main__":
    App().mainloop()
