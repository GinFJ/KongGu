"""Left sidebar for Konggu."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles


class Sidebar(ctk.CTkFrame):
    """Persistent navigation rail."""

    def __init__(self, parent, app):
        super().__init__(
            parent,
            width=styles.SIDEBAR_WIDTH,
            fg_color=styles.SIDEBAR_BG,
            corner_radius=0,
            border_width=0,
            border_color=styles.BORDER,
        )
        self.app = app
        self.nav_buttons = {}
        self.selected_nav = "工作台"
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(self, text="空谷 Konggu", font=(styles.FONT_FAMILY, 20, "bold"), text_color=styles.TEXT_MAIN).grid(
            row=0, column=0, padx=20, pady=(24, 2), sticky="w"
        )
        ctk.CTkLabel(
            self,
            text="多人空课表生成工具",
            font=styles.FONT_SMALL,
            text_color=styles.TEXT_MUTED,
        ).grid(row=1, column=0, padx=20, pady=(0, 24), sticky="w")

        for row, label in enumerate(["工作台", "文件导入", "成员检查", "空课表结果", "识别明细", "处理日志"], start=2):
            button = ctk.CTkButton(
                self,
                text=label,
                height=38,
                anchor="w",
                fg_color="transparent",
                hover_color="#F3F4F6",
                text_color=styles.TEXT_NAV,
                font=styles.FONT_BODY,
                corner_radius=10,
                command=lambda name=label: self._select(name),
            )
            button.grid(row=row, column=0, padx=14, pady=3, sticky="ew")
            self.nav_buttons[label] = button

        footer = ctk.CTkFrame(self, fg_color=styles.SIDEBAR_BG, corner_radius=0)
        footer.grid(row=9, column=0, sticky="sew", padx=14, pady=(8, 18))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkFrame(footer, height=1, fg_color=styles.BORDER).grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.settings_button = ctk.CTkButton(
            footer,
            text="设置",
            height=34,
            anchor="w",
            fg_color="transparent",
            hover_color="#F3F4F6",
            text_color=styles.TEXT_NAV,
            font=styles.FONT_SMALL,
            corner_radius=10,
            command=lambda: self._select("设置"),
        )
        self.settings_button.grid(row=1, column=0, sticky="ew")
        ctk.CTkLabel(footer, text="v0.1.0", font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED).grid(
            row=2, column=0, padx=6, pady=(12, 0), sticky="w"
        )
        ctk.CTkFrame(self, width=1, fg_color=styles.BORDER).place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self.set_active("工作台")

    def _select(self, name: str) -> None:
        self.set_active(name)
        self.app.select_tab_by_nav(name)

    def set_active(self, name: str) -> None:
        self.selected_nav = name
        for label, button in self.nav_buttons.items():
            active = label == name
            button.configure(
                fg_color=styles.PRIMARY_SOFT if active else "transparent",
                text_color=styles.PRIMARY if active else styles.TEXT_NAV,
                font=(styles.FONT_FAMILY, 13, "bold") if active else styles.FONT_BODY,
            )
        self.settings_button.configure(
            fg_color=styles.PRIMARY_SOFT if name == "设置" else "transparent",
            text_color=styles.PRIMARY if name == "设置" else styles.TEXT_NAV,
        )
