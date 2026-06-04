"""Table helpers used by the CustomTkinter UI."""

from __future__ import annotations

from tkinter import END, ttk
import tkinter as tk

import pandas as pd

from app.gui import styles


def create_table(parent) -> ttk.Treeview:
    """Create a scrollable ttk table that sits inside a CTk frame."""

    frame = tk.Frame(parent, bg=styles.CARD_BG)
    tree = ttk.Treeview(frame, show="headings", style="Konggu.Treeview")
    tree.tag_configure("oddrow", background=styles.ROW_ALT)
    tree.tag_configure("evenrow", background=styles.CARD_BG)
    tree.tag_configure("success", background=styles.SUCCESS_BG)
    tree.tag_configure("warning", background=styles.WARNING_BG)
    tree.tag_configure("error", background=styles.ERROR_BG)
    tree.tag_configure("info", background=styles.INFO_BG)
    yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview, style="Konggu.Vertical.TScrollbar")
    xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview, style="Konggu.Horizontal.TScrollbar")
    tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return tree


def fill_table(tree: ttk.Treeview, rows: pd.DataFrame | list[dict]) -> None:
    """Fill a table from a DataFrame or list of dictionaries."""

    tree.delete(*tree.get_children())
    if isinstance(rows, list):
        df = pd.DataFrame(rows)
    else:
        df = rows
    if df is None or df.empty:
        tree["columns"] = []
        return

    view_df = df.fillna("").copy()
    columns = [str(col) for col in view_df.columns]
    tree["columns"] = columns
    for col in columns:
        tree.heading(col, text=col)
        sample_width = max([len(str(col))] + [len(str(value)) for value in view_df[col].head(30)])
        tree.column(col, width=max(96, min(320, sample_width * 13)), anchor="w", stretch=True)

    for index, (_, row) in enumerate(view_df.iterrows()):
        tag = _row_tag(row, index)
        tree.insert("", END, values=[str(row[col]) for col in view_df.columns], tags=(tag,))


def _row_tag(row: pd.Series, index: int) -> str:
    status_text = " ".join(
        str(row.get(column, ""))
        for column in ("状态", "备注", "风险提示", "警告信息")
        if column in row.index
    )
    if any(keyword in status_text for keyword in ("失败", "错误", "ERROR", "缺少", "疑似", "需检查")):
        return "error"
    if any(keyword in status_text for keyword in ("警告", "WARNING", "低置信度")):
        return "warning"
    if any(keyword in status_text for keyword in ("成功", "已识别", "正常", "完整", "SUCCESS")):
        return "success"
    if any(keyword in status_text for keyword in ("等待", "解析中", "INFO")):
        return "info"
    return "oddrow" if index % 2 else "evenrow"
