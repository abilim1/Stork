"""From / To date row. Hides day when interval is monthly."""

from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date
from tkinter import ttk


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
