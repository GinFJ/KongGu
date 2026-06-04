"""Bottom status bar."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles


class StatusBar(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=styles.CARD_BG, height=36, corner_radius=0)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkLabel(self, text="请先导入课表 PDF", font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED)
        self.task = ctk.CTkLabel(self, text="空闲", font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED)
        self.label.grid(row=0, column=0, padx=16, sticky="w")
        self.task.grid(row=0, column=1, padx=16, sticky="e")

    def set_status(self, text: str, task: str | None = None) -> None:
        self.label.configure(text=text)
        if task is not None:
            self.task.configure(text=task)
