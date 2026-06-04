"""Shared CustomTkinter styling constants for the Konggu desktop UI."""

from __future__ import annotations

from tkinter import ttk
import tkinter as tk

APP_BG = "#F5F7F8"
SIDEBAR_BG = "#FFFFFF"
CARD_BG = "#FFFFFF"
PRIMARY = "#2F7D6D"
PRIMARY_HOVER = "#256B5E"
PRIMARY_SOFT = "#E8F3EF"
TEXT_MAIN = "#1F2933"
TEXT_NAV = "#374151"
TEXT_MUTED = "#6B7280"
BORDER = "#E5E7EB"
SUCCESS = "#16A34A"
WARNING = "#F59E0B"
ERROR = "#DC2626"
INFO = "#2563EB"
CACHE = "#7C3AED"
EXPORT = "#0F766E"
DANGER = "#DC2626"
DANGER_HOVER = "#B91C1C"
DANGER_SOFT = "#FEE2E2"
WARNING_SOFT = "#FEF3C7"
SURFACE_SOFT = "#F8FAFC"
ROW_ALT = "#F3F6F8"
TABLE_HEADER = "#F9FAFB"
SUCCESS_BG = "#ECFDF3"
WARNING_BG = "#FFFBEB"
ERROR_BG = "#FEF2F2"
INFO_BG = "#EFF6FF"

FONT_FAMILY = "Microsoft YaHei UI"
FONT_TITLE = (FONT_FAMILY, 24, "bold")
FONT_PAGE_TITLE = (FONT_FAMILY, 20, "bold")
FONT_SECTION = (FONT_FAMILY, 16, "bold")
FONT_BODY = (FONT_FAMILY, 13)
FONT_SMALL = (FONT_FAMILY, 12)
FONT_NUMBER = (FONT_FAMILY, 24, "bold")
FONT_BADGE = (FONT_FAMILY, 12, "bold")

SIDEBAR_WIDTH = 220
CARD_RADIUS = 14
HEADER_HEIGHT = 90
ACTION_CARD_HEIGHT = 150
FILE_CARD_HEIGHT = 136
SUMMARY_CARD_HEIGHT = 72
TASK_CARD_HEIGHT = 84
BUTTON_HEIGHT = 36
BUTTON_WIDTH = 132


def apply_ttk_style(root: tk.Misc) -> None:
    """Style ttk widgets embedded in CustomTkinter frames."""

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=FONT_BODY, foreground=TEXT_MAIN)
    style.configure("Konggu.Treeview", rowheight=34, borderwidth=0, relief="flat")
    style.configure(
        "Konggu.Treeview",
        background=CARD_BG,
        fieldbackground=CARD_BG,
        foreground=TEXT_MAIN,
    )
    style.configure(
        "Konggu.Treeview.Heading",
        font=(FONT_FAMILY, 12, "bold"),
        padding=(8, 8),
        background=TABLE_HEADER,
        foreground=TEXT_MAIN,
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "Konggu.Treeview",
        background=[("selected", "#DDEDE9")],
        foreground=[("selected", TEXT_MAIN)],
    )
    style.configure("Konggu.Vertical.TScrollbar", background=SURFACE_SOFT, troughcolor=CARD_BG)
    style.configure("Konggu.Horizontal.TScrollbar", background=SURFACE_SOFT, troughcolor=CARD_BG)
