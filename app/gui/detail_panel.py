"""Recognition detail tab."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles
from app.gui.empty_state import EmptyState
from app.gui.table_widgets import create_table


class DetailPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=styles.CARD_BG, corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.table = create_table(self)
        self.empty_state = EmptyState(self, "暂无识别明细", "解析完成后将在这里显示每个 PDF 的识别过程。")
        self.empty_state.grid(row=0, column=0, sticky="nsew")

    def show_table(self, show: bool) -> None:
        if show:
            self.empty_state.grid_remove()
            self.table.master.grid(row=0, column=0, sticky="nsew")
        else:
            self.table.master.grid_remove()
            self.empty_state.grid()
