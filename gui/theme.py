"""Flat dark theme for ttk / tk."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#101216"
SURFACE = "#171A20"
CARD = "#1C2028"
LINE = "#2A303A"
TEXT = "#ECEDEF"
MUTED = "#8E959E"
ACCENT = "#E8EAED"
OK = "#34D399"
BAD = "#F87171"
BTN = "#ECEDEF"
BTN_FG = "#111318"
BTN_OFF = "#2A303A"
BTN_OFF_FG = "#6B7280"
LOG_BG = "#0C0E12"

UI_FONT = ("Helvetica", 10)
UI_FONT_SM = ("Helvetica", 9)
UI_FONT_BOLD = ("Helvetica", 10, "bold")
UI_FONT_TITLE = ("Helvetica", 16, "bold")
MONO = ("Helvetica", 9)


def apply(root: tk.Tk) -> ttk.Style:
    root.configure(bg=BG)
    root.option_add("*Font", UI_FONT)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=CARD, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT, font=UI_FONT)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=UI_FONT_TITLE)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=UI_FONT_SM)
    style.configure("Bold.TLabel", background=BG, foreground=TEXT, font=UI_FONT_BOLD)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT, font=UI_FONT)
    style.configure("CardBold.TLabel", background=SURFACE, foreground=TEXT, font=UI_FONT_BOLD)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=UI_FONT_SM)
    style.configure(
        "TEntry",
        fieldbackground=CARD,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor=LINE,
        lightcolor=LINE,
        darkcolor=LINE,
        padding=6,
    )
    style.configure(
        "TCombobox",
        fieldbackground=CARD,
        background=CARD,
        foreground=TEXT,
        arrowcolor=MUTED,
        bordercolor=LINE,
        lightcolor=LINE,
        darkcolor=LINE,
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", CARD)],
        foreground=[("readonly", TEXT)],
        background=[("readonly", CARD)],
    )
    style.configure(
        "TCheckbutton",
        background=SURFACE,
        foreground=MUTED,
        font=UI_FONT_SM,
        indicatorcolor=CARD,
    )
    style.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", TEXT)])
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", LINE)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    return style
