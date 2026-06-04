"""Top header and status badge."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles


class HeaderBar(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=styles.APP_BG, corner_radius=0, height=styles.HEADER_HEIGHT)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self.app = app

        title_group = ctk.CTkFrame(self, fg_color="transparent")
        title_group.grid(row=0, column=0, sticky="w", padx=(0, 24))
        ctk.CTkLabel(
            title_group,
            text="空谷 Konggu",
            font=(styles.FONT_FAMILY, 18, "bold"),
            text_color=styles.TEXT_MAIN,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_group,
            text="多人课表解析与空课表生成",
            font=styles.FONT_TITLE,
            text_color=styles.TEXT_MAIN,
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkLabel(
            title_group,
            text="导入中方 / 英方课表 PDF，自动识别成员占用时间并生成空课表。",
            font=styles.FONT_SMALL,
            text_color=styles.TEXT_MUTED,
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        self.badge = ctk.CTkLabel(
            actions,
            text="状态：空闲",
            height=34,
            corner_radius=14,
            fg_color="#EEF2F7",
            text_color=styles.TEXT_NAV,
            font=styles.FONT_BADGE,
            padx=18,
        )
        self.badge.grid(row=0, column=0, sticky="e", padx=(0, 10))
        self.settings_button = ctk.CTkButton(
            actions,
            text="设置",
            width=72,
            height=32,
            fg_color="#FFFFFF",
            hover_color="#F3F4F6",
            text_color=styles.TEXT_NAV,
            border_width=1,
            border_color=styles.BORDER,
            command=self._show_settings_hint,
        )
        self.settings_button.grid(row=0, column=1, sticky="e")

    def set_badge(self, text: str, color: str) -> None:
        bg, fg = {
            styles.TEXT_MUTED: ("#EEF2F7", styles.TEXT_NAV),
            styles.INFO: ("#DBEAFE", styles.INFO),
            styles.PRIMARY: (styles.PRIMARY_SOFT, styles.PRIMARY),
            styles.SUCCESS: ("#DCFCE7", styles.SUCCESS),
            styles.WARNING: (styles.WARNING_SOFT, styles.WARNING),
            styles.ERROR: (styles.DANGER_SOFT, styles.ERROR),
        }.get(color, ("#EEF2F7", styles.TEXT_NAV))
        self.badge.configure(text=f"状态：{text}", fg_color=bg, text_color=fg)

    def _show_settings_hint(self) -> None:
        self.app.show_settings_hint()
