"""Shared visual theme for the Konggu desktop UI."""

from __future__ import annotations

from tkinter import ttk
import tkinter as tk


FONT_FAMILY = "Microsoft YaHei UI"

PALETTE = {
    "bg": "#F4F8F1",
    "surface": "#FFFFFF",
    "surface_soft": "#F8FBF5",
    "nav": "#FDFEF9",
    "nav_card": "#FFFFFF",
    "nav_muted": "#6F7F72",
    "text": "#13241C",
    "muted": "#6F7B74",
    "border": "#DCE8DC",
    "accent": "#1F9D6B",
    "accent_dark": "#087A58",
    "accent_soft": "#E7F7EE",
    "success": "#139968",
    "danger": "#C2410C",
    "warning": "#B7791F",
    "row_alt": "#F7FBF4",
}


def apply_theme(root: tk.Misc) -> None:
    """Apply the shared ttk theme to a Tk root or child widget."""

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=(FONT_FAMILY, 10), foreground=PALETTE["text"])
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("Surface.TFrame", background=PALETTE["surface"])
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Muted.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.configure("Surface.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"])
    style.configure("SurfaceMuted.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])

    style.configure(
        "Primary.TButton",
        font=(FONT_FAMILY, 10, "bold"),
        padding=(14, 10),
        borderwidth=0,
        foreground="#FFFFFF",
        background=PALETTE["accent"],
    )
    style.map(
        "Primary.TButton",
        background=[("active", PALETTE["accent_dark"]), ("disabled", "#D5DCE7")],
        foreground=[("disabled", "#7B8493")],
    )
    style.configure(
        "Secondary.TButton",
        font=(FONT_FAMILY, 10),
        padding=(12, 9),
        borderwidth=1,
        relief="flat",
        foreground=PALETTE["text"],
        background="#FFFFFF",
    )
    style.map("Secondary.TButton", background=[("active", "#ECF7EF")])
    style.configure(
        "Export.TButton",
        font=(FONT_FAMILY, 10, "bold"),
        padding=(12, 9),
        borderwidth=0,
        foreground="#FFFFFF",
        background=PALETTE["success"],
    )
    style.map("Export.TButton", background=[("active", "#087A58")])

    style.configure(
        "TNotebook",
        background=PALETTE["surface"],
        borderwidth=0,
        tabmargins=(4, 4, 4, 0),
    )
    style.configure(
        "TNotebook.Tab",
        font=(FONT_FAMILY, 10, "bold"),
        padding=(18, 10),
        background=PALETTE["surface_soft"],
        foreground=PALETTE["muted"],
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PALETTE["accent_soft"])],
        foreground=[("selected", PALETTE["accent_dark"])],
    )

    style.configure(
        "Treeview",
        rowheight=30,
        borderwidth=0,
        relief="flat",
        background=PALETTE["surface"],
        fieldbackground=PALETTE["surface"],
        foreground=PALETTE["text"],
    )
    style.configure(
        "Treeview.Heading",
        font=(FONT_FAMILY, 10, "bold"),
        padding=(8, 8),
        background="#ECF5EA",
        foreground=PALETTE["text"],
        relief="flat",
    )
    style.map("Treeview", background=[("selected", "#DDF4E7")], foreground=[("selected", PALETTE["text"])])
    style.configure("Horizontal.TProgressbar", troughcolor="#D6E8D8", background=PALETTE["accent"])
