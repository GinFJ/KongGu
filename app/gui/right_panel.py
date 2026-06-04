"""Right content panel layout for the Konggu desktop app."""

from __future__ import annotations

from tkinter import BOTH, LEFT, X, ttk
import tkinter as tk

from app.gui.theme import FONT_FAMILY, PALETTE
from app.gui.widgets import create_tree, draw_header, metric_card, surface_card


def build_right_panel(app, parent) -> None:
    """Build the metrics, result tabs, and log viewer."""

    content = tk.Frame(parent, bg=PALETTE["bg"])
    content.pack(fill=BOTH, expand=True, padx=22, pady=20)

    app.header_canvas = tk.Canvas(content, height=124, highlightthickness=0, bd=0, bg=PALETTE["bg"])
    app.header_canvas.pack(fill=X, pady=(0, 14))
    app.header_canvas.bind("<Configure>", draw_header)

    metrics = tk.Frame(content, bg=PALETTE["bg"])
    metrics.pack(fill=X, pady=(0, 14))
    metric_card(metrics, "成员数", app.metric_students, "参与合并的人数").pack(
        side=LEFT, fill=X, expand=True, padx=(0, 8)
    )
    metric_card(metrics, "时间块", app.metric_blocks, "识别出的课程片段").pack(
        side=LEFT, fill=X, expand=True, padx=8
    )
    metric_card(metrics, "教学周", app.metric_weeks, "覆盖的周次数量").pack(
        side=LEFT, fill=X, expand=True, padx=8
    )
    metric_card(metrics, "空课表格", app.metric_free_slots, "按周次与节次全量生成").pack(
        side=LEFT, fill=X, expand=True, padx=(8, 0)
    )

    notebook_card = surface_card(content)
    notebook = ttk.Notebook(notebook_card)
    notebook.pack(fill=BOTH, expand=True, padx=12, pady=12)

    app.tab_free = ttk.Frame(notebook, padding=8)
    app.tab_members = ttk.Frame(notebook, padding=8)
    app.tab_blocks = ttk.Frame(notebook, padding=8)
    app.tab_errors = ttk.Frame(notebook, padding=8)

    notebook.add(app.tab_free, text="空课表导出")
    notebook.add(app.tab_members, text="成员检查")
    notebook.add(app.tab_blocks, text="识别明细")
    notebook.add(app.tab_errors, text="处理日志")

    app.free_tree = create_tree(app.tab_free)
    app.members_tree = create_tree(app.tab_members)
    app.blocks_tree = create_tree(app.tab_blocks)

    app.error_box = tk.Text(
        app.tab_errors,
        wrap="word",
        height=20,
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=PALETTE["border"],
        bg=PALETTE["surface_soft"],
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 10),
        padx=14,
        pady=12,
    )
    app.error_box.pack(fill=BOTH, expand=True)
    app.error_box.insert("1.0", "暂无提示。")
    app.error_box.configure(state="disabled")
