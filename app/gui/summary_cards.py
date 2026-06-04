"""Summary metric cards."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles


class SummaryCards(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=styles.APP_BG, corner_radius=0)
        self.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="metrics")
        self.cards = {}
        for index, (key, label, hint) in enumerate(
            [
                ("pdf_count", "PDF 文件数", "已导入"),
                ("member_count", "识别成员数", "已识别"),
                ("complete_count", "完整成员数", "可生成"),
                ("warning_count", "警告数量", "待检查"),
            ]
        ):
            self.cards[key] = _MetricCard(self, label, hint)
            self.cards[key].grid(row=0, column=index, padx=(0 if index == 0 else 10, 0), sticky="ew")

    def update_values(self, *, pdf_count: int, member_count: int, complete_count: int, warning_count: int) -> None:
        self.cards["pdf_count"].set_value(pdf_count)
        self.cards["member_count"].set_value(member_count)
        self.cards["complete_count"].set_value(complete_count)
        self.cards["warning_count"].set_value(warning_count, color=styles.WARNING if warning_count else styles.TEXT_MAIN)


class _MetricCard(ctk.CTkFrame):
    def __init__(self, parent, label: str, hint: str):
        super().__init__(
            parent,
            fg_color=styles.CARD_BG,
            corner_radius=styles.CARD_RADIUS,
            border_width=1,
            border_color=styles.BORDER,
            height=styles.SUMMARY_CARD_HEIGHT,
        )
        self.pack_propagate(False)
        self.value = ctk.CTkLabel(self, text="0", font=styles.FONT_NUMBER, text_color=styles.TEXT_MAIN)
        ctk.CTkLabel(self, text=label, font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED).pack(
            anchor="w", padx=16, pady=(7, 0)
        )
        self.value.pack(anchor="w", padx=16, pady=(0, 0))
        ctk.CTkLabel(self, text=hint, font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED).pack(
            anchor="w", padx=16, pady=(0, 6)
        )

    def set_value(self, value: int, color: str | None = None) -> None:
        self.value.configure(text=str(value), text_color=color or styles.TEXT_MAIN)
