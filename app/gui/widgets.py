"""Reusable Tkinter widget builders for the Konggu desktop UI."""

from __future__ import annotations

from tkinter import BOTH, END, X, Y, ttk
import tkinter as tk

import pandas as pd

from app.gui.theme import FONT_FAMILY, PALETTE


def sidebar_card(parent, title: str, subtitle: str = "") -> tk.Frame:
    """Create one card in the left navigation column."""

    card = tk.Frame(parent, bg=PALETTE["nav_card"], highlightthickness=1, highlightbackground=PALETTE["border"])
    card.pack(fill=X, padx=16, pady=(0, 12))

    inner = tk.Frame(card, bg=PALETTE["nav_card"])
    inner.pack(fill=BOTH, expand=True, padx=14, pady=14)

    tk.Label(
        inner,
        text=title,
        bg=PALETTE["nav_card"],
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 12, "bold"),
    ).pack(anchor="w")
    if subtitle:
        tk.Label(
            inner,
            text=subtitle,
            bg=PALETTE["nav_card"],
            fg=PALETTE["muted"],
            font=(FONT_FAMILY, 9),
            wraplength=240,
            justify="left",
        ).pack(anchor="w", pady=(4, 10))
    return inner


def surface_card(parent) -> tk.Frame:
    """Create a framed content surface in the main area."""

    card = tk.Frame(parent, bg=PALETTE["surface"], highlightthickness=1, highlightbackground=PALETTE["border"])
    card.pack(fill=BOTH, expand=True)
    return card


def metric_card(parent, title: str, value_var: tk.StringVar, desc: str) -> tk.Frame:
    """Create a compact metric tile."""

    card = tk.Frame(parent, bg=PALETTE["surface"], highlightthickness=1, highlightbackground=PALETTE["border"])
    tk.Label(
        card,
        text=title,
        bg=PALETTE["surface"],
        fg=PALETTE["muted"],
        font=(FONT_FAMILY, 10, "bold"),
    ).pack(anchor="w", padx=16, pady=(14, 0))
    tk.Label(
        card,
        textvariable=value_var,
        bg=PALETTE["surface"],
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 22, "bold"),
    ).pack(anchor="w", padx=16, pady=(4, 0))
    tk.Label(
        card,
        text=desc,
        bg=PALETTE["surface"],
        fg=PALETTE["muted"],
        font=(FONT_FAMILY, 9),
    ).pack(anchor="w", padx=16, pady=(0, 14))
    return card


def create_tree(parent) -> ttk.Treeview:
    """Create a scrollable treeview table."""

    frame = ttk.Frame(parent, style="Surface.TFrame")
    frame.pack(fill=BOTH, expand=True)
    tree = ttk.Treeview(frame, show="headings")
    tree.tag_configure("oddrow", background=PALETTE["row_alt"])
    tree.tag_configure("evenrow", background=PALETTE["surface"])
    yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return tree


def fill_tree(tree: ttk.Treeview, df: pd.DataFrame) -> None:
    """Fill a treeview with a pandas DataFrame."""

    tree.delete(*tree.get_children())
    if df is None or df.empty:
        tree["columns"] = []
        return

    view_df = df.copy()
    view_df = view_df.fillna("")
    columns = [str(col) for col in view_df.columns]
    tree["columns"] = columns

    for col in columns:
        tree.heading(col, text=col)
        sample_width = max([len(str(col))] + [len(str(value)) for value in view_df[col].head(20)])
        tree.column(col, width=max(92, min(260, sample_width * 14)), anchor="w")

    for index, (_, row) in enumerate(view_df.iterrows()):
        values = [str(row[col]) for col in view_df.columns]
        tag = "oddrow" if index % 2 else "evenrow"
        tree.insert("", END, values=values, tags=(tag,))


def draw_header(event) -> None:
    """Draw the decorative top header on a canvas configure event."""

    canvas = event.widget
    width = max(event.width, 1)
    height = max(event.height, 1)
    canvas.delete("all")

    start = _hex_to_rgb("#FFFFFF")
    end = _hex_to_rgb("#E7F8DE")
    steps = 120
    for i in range(steps):
        ratio = i / max(steps - 1, 1)
        color = _rgb_to_hex(tuple(int(start[j] * (1 - ratio) + end[j] * ratio) for j in range(3)))
        x0 = int(width * i / steps)
        x1 = int(width * (i + 1) / steps) + 1
        canvas.create_rectangle(x0, 0, x1, height, fill=color, outline=color)

    for x in range(0, width, 32):
        canvas.create_line(x, 0, x, height, fill="#E7EFE6", width=1)
    for y in range(0, height, 32):
        canvas.create_line(0, y, width, y, fill="#EAF1E8", width=1)

    canvas.create_text(
        32,
        28,
        anchor="nw",
        text="空谷 · 空课表生成",
        fill=PALETTE["text"],
        font=(FONT_FAMILY, 24, "bold"),
    )
    canvas.create_text(
        34,
        78,
        anchor="nw",
        text="导入 PDF，检查成员课表完整性，生成 Excel 空课表",
        fill=PALETTE["muted"],
        font=(FONT_FAMILY, 11),
    )

    canvas.create_rectangle(0, height - 3, width, height, fill="#BEE7C9", outline="#BEE7C9")
    canvas.create_arc(width - 190, 22, width - 70, 102, start=20, extent=105, outline="#50B982", width=4)
    canvas.create_line(width - 132, 82, width - 108, 42, fill="#139968", width=4)
    canvas.create_oval(width - 120, 54, width - 70, 78, fill="#9FDB85", outline="")
    canvas.create_line(width - 222, 86, width - 62, 56, fill="#A9DDE8", width=2, smooth=True)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(value: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*value)
