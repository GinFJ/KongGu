"""Left navigation panel layout for the Konggu desktop app."""

from __future__ import annotations

from tkinter import BOTH, LEFT, RIGHT, X, Y, ttk
import tkinter as tk

from app.gui.theme import FONT_FAMILY, PALETTE
from app.gui.widgets import sidebar_card


def build_left_panel(app, parent) -> None:
    """Build the PDF import and action sidebar."""

    brand = tk.Frame(parent, bg=PALETTE["nav"])
    brand.pack(fill=X, padx=20, pady=(22, 20))

    tk.Label(
        brand,
        text="空谷",
        bg=PALETTE["nav"],
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 22, "bold"),
    ).pack(anchor="w")
    tk.Label(
        brand,
        text="青禾计划系列",
        bg=PALETTE["nav"],
        fg=PALETTE["nav_muted"],
        font=(FONT_FAMILY, 10),
    ).pack(anchor="w", pady=(4, 0))

    import_frame = sidebar_card(
        parent,
        "课表 PDF 导入",
        "推荐直接批量添加，系统会按文件名识别中方/英方。",
    )

    ttk.Button(
        import_frame,
        text="批量添加所有课表 PDF",
        style="Primary.TButton",
        command=app.add_mixed_pdfs,
    ).pack(fill=X, pady=(0, 8))

    row = tk.Frame(import_frame, bg=PALETTE["nav_card"])
    row.pack(fill=X)
    ttk.Button(
        row,
        text="添加中方 PDF",
        style="Secondary.TButton",
        command=lambda: app.add_pdfs("中方"),
    ).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
    ttk.Button(
        row,
        text="添加英方 PDF",
        style="Secondary.TButton",
        command=lambda: app.add_pdfs("英方"),
    ).pack(side=RIGHT, fill=X, expand=True, padx=(4, 0))

    action_frame = sidebar_card(parent, "生成与导出")
    app.parse_button = ttk.Button(
        action_frame,
        text="生成空课表",
        style="Primary.TButton",
        command=app.parse_async,
    )
    app.parse_button.pack(fill=X, pady=(0, 8))
    ttk.Button(
        action_frame,
        text="导出 Excel 空课表",
        style="Export.TButton",
        command=app.export_excel,
    ).pack(fill=X, pady=(0, 10))
    app.progress = ttk.Progressbar(action_frame, mode="indeterminate", style="Horizontal.TProgressbar")
    app.progress.pack(fill=X, pady=(0, 8))
    tk.Label(
        action_frame,
        text="导出范围：全量 1-11 节，包含中午和晚上。",
        bg=PALETTE["nav_card"],
        fg=PALETTE["muted"],
        wraplength=240,
        justify="left",
        font=(FONT_FAMILY, 9),
    ).pack(anchor="w")

    pdf_frame = sidebar_card(parent, "已选择 PDF")
    tk.Label(
        pdf_frame,
        text="文件列表",
        bg=PALETTE["nav_card"],
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 10, "bold"),
    ).pack(anchor="w", pady=(2, 5))
    list_wrap = tk.Frame(pdf_frame, bg=PALETTE["nav_card"])
    list_wrap.pack(fill=BOTH, expand=False, pady=(4, 8))
    app.pdf_list = tk.Listbox(
        list_wrap,
        height=5,
        activestyle="none",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=PALETTE["border"],
        selectbackground=PALETTE["accent"],
        selectforeground="#FFFFFF",
        bg="#F8FAFC",
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 9),
    )
    scrollbar = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=app.pdf_list.yview)
    app.pdf_list.configure(yscrollcommand=scrollbar.set)
    app.pdf_list.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.pack(side=RIGHT, fill=Y)

    row2 = tk.Frame(pdf_frame, bg=PALETTE["nav_card"])
    row2.pack(fill=X)
    ttk.Button(row2, text="删除选中", style="Secondary.TButton", command=app.remove_selected_pdf).pack(
        side=LEFT, fill=X, expand=True, padx=(0, 4)
    )
    ttk.Button(row2, text="清空列表", style="Secondary.TButton", command=app.clear_pdf_list).pack(
        side=RIGHT, fill=X, expand=True, padx=(4, 0)
    )

    dir_frame = sidebar_card(
        parent,
        "本地课表根目录",
        "如果课表已经按部门存在一个文件夹里，可以直接选择根目录递归读取。",
    )
    tk.Label(
        dir_frame,
        text="目录路径",
        bg=PALETTE["nav_card"],
        fg=PALETTE["text"],
        font=(FONT_FAMILY, 10, "bold"),
    ).pack(anchor="w", pady=(0, 5))
    ttk.Entry(dir_frame, textvariable=app.root_dir).pack(fill=X, pady=(0, 8))
    ttk.Button(dir_frame, text="载入参考库", style="Primary.TButton", command=app.load_reference_library).pack(
        fill=X, pady=(0, 8)
    )
    ttk.Button(dir_frame, text="选择目录", style="Secondary.TButton", command=app.choose_root_dir).pack(fill=X)

    status_frame = sidebar_card(parent, "状态")
    tk.Label(
        status_frame,
        textvariable=app.status_text,
        wraplength=240,
        justify="left",
        bg=PALETTE["nav_card"],
        fg=PALETTE["muted"],
        font=(FONT_FAMILY, 9),
    ).pack(anchor="w")
    tk.Label(
        status_frame,
        textvariable=app.summary_text,
        wraplength=240,
        justify="left",
        bg=PALETTE["nav_card"],
        fg=PALETTE["accent_dark"],
        font=(FONT_FAMILY, 9, "bold"),
    ).pack(anchor="w", pady=(10, 0))
