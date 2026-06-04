"""Result tab view."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles
from app.gui.availability_table import AvailabilityTable
from app.gui.detail_panel import DetailPanel
from app.gui.log_panel import LogPanel
from app.gui.member_check_table import MemberCheckTable


class ResultTabs(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(
            parent,
            fg_color=styles.CARD_BG,
            corner_radius=styles.CARD_RADIUS,
            border_width=1,
            border_color=styles.BORDER,
        )
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color=styles.CARD_BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="结果预览", font=styles.FONT_SECTION, text_color=styles.TEXT_MAIN).grid(
            row=0, column=0, sticky="w"
        )
        self.status_label = ctk.CTkLabel(header, text="尚未生成", font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED)
        self.status_label.grid(row=0, column=1, sticky="e")
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=styles.CARD_BG,
            segmented_button_selected_color=styles.PRIMARY,
            segmented_button_selected_hover_color=styles.PRIMARY_HOVER,
            segmented_button_unselected_color=styles.SURFACE_SOFT,
            segmented_button_unselected_hover_color="#EEF2F7",
            text_color=styles.TEXT_MAIN,
            anchor="nw",
        )
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        self.availability_tab = AvailabilityTable(self.tabview.add("空课表结果"))
        self.member_tab = MemberCheckTable(self.tabview.add("成员检查"))
        self.detail_tab = DetailPanel(self.tabview.add("识别明细"))
        self.log_tab = LogPanel(self.tabview.add("处理日志"))
        self.availability_tab.pack(fill="both", expand=True, padx=4, pady=4)
        self.member_tab.pack(fill="both", expand=True, padx=4, pady=4)
        self.detail_tab.pack(fill="both", expand=True, padx=4, pady=4)
        self.log_tab.pack(fill="both", expand=True, padx=4, pady=4)

    def select(self, name: str) -> None:
        if name in {"空课表结果", "成员检查", "识别明细", "处理日志"}:
            self.tabview.set(name)

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)
