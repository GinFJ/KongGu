"""Reusable empty state for CustomTkinter panels."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles


class EmptyState(ctk.CTkFrame):
    def __init__(self, parent, title: str, subtitle: str):
        super().__init__(parent, fg_color=styles.SURFACE_SOFT, corner_radius=styles.CARD_RADIUS)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=0)
        ctk.CTkLabel(content, text="[ ]", font=(styles.FONT_FAMILY, 22), text_color=styles.TEXT_MUTED).pack()
        ctk.CTkLabel(content, text=title, font=styles.FONT_SECTION, text_color=styles.TEXT_MAIN).pack(pady=(6, 2))
        ctk.CTkLabel(content, text=subtitle, font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED).pack()
