"""Processing log tab."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles
from app.gui.empty_state import EmptyState


class LogPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=styles.CARD_BG, corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.textbox = ctk.CTkTextbox(
            self,
            fg_color=styles.SURFACE_SOFT,
            text_color=styles.TEXT_MAIN,
            font=styles.FONT_BODY,
            border_width=1,
            border_color=styles.BORDER,
            corner_radius=styles.CARD_RADIUS,
        )
        self.empty_state = EmptyState(self, "暂无处理日志", "程序运行信息将在这里显示。")
        self.empty_state.grid(row=0, column=0, sticky="nsew")
        self.has_content = False
        self._configure_tags()
        self.textbox.configure(state="disabled")

    def append(self, level: str, message: str) -> None:
        self._show_textbox()
        self.textbox.configure(state="normal")
        self._insert_line(level, message)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def set_text(self, text: str) -> None:
        self._show_textbox()
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        for line in text.splitlines() or [""]:
            level, message = _parse_log_line(line)
            self._insert_line(level, message)
        self.textbox.configure(state="disabled")

    def show_empty_state(self) -> None:
        self.has_content = False
        self.textbox.grid_remove()
        self.empty_state.grid()

    def _show_textbox(self) -> None:
        if self.has_content:
            return
        self.has_content = True
        self.empty_state.grid_remove()
        self.textbox.grid(row=0, column=0, sticky="nsew")

    def _configure_tags(self) -> None:
        self.textbox.tag_config("INFO", foreground=styles.INFO)
        self.textbox.tag_config("SUCCESS", foreground=styles.SUCCESS)
        self.textbox.tag_config("WARNING", foreground=styles.WARNING)
        self.textbox.tag_config("ERROR", foreground=styles.ERROR)
        self.textbox.tag_config("CACHE", foreground=styles.CACHE)
        self.textbox.tag_config("EXPORT", foreground=styles.EXPORT)

    def _insert_line(self, level: str, message: str) -> None:
        normalized = level.upper() if level else "INFO"
        if normalized not in {"INFO", "SUCCESS", "WARNING", "ERROR", "CACHE", "EXPORT"}:
            normalized = "INFO"
        self.textbox.insert("end", f"[{normalized}] ", normalized)
        self.textbox.insert("end", f"{message}\n")


def _parse_log_line(line: str) -> tuple[str, str]:
    for level in ("INFO", "SUCCESS", "WARNING", "ERROR", "CACHE", "EXPORT"):
        prefix = f"[{level}] "
        if line.startswith(prefix):
            return level, line[len(prefix) :]
    return "INFO", line
