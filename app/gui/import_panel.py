"""Import/action controls and imported-file card."""

from __future__ import annotations

import customtkinter as ctk

from app.gui import styles
from app.gui.table_widgets import create_table


class ImportPanel(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=styles.APP_BG, corner_radius=0)
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        self.actions = ctk.CTkFrame(
            self,
            fg_color=styles.CARD_BG,
            corner_radius=styles.CARD_RADIUS,
            border_width=1,
            border_color=styles.BORDER,
            height=styles.ACTION_CARD_HEIGHT,
        )
        self.actions.grid(row=0, column=0, sticky="ew")
        self.actions.grid_propagate(False)
        self.actions.grid_columnconfigure((0, 1), weight=1, uniform="action_groups")
        self.action_buttons = {}

        self._build_group(
            self.actions,
            column=0,
            title="导入课表",
            buttons=[
                ("添加中方 PDF", lambda: self.app.add_pdfs("中方"), "secondary"),
                ("添加英方 PDF", lambda: self.app.add_pdfs("英方"), "secondary"),
                ("批量导入 PDF", self.app.add_mixed_pdfs, "secondary"),
                ("选择课表目录", self.app.choose_root_dir, "secondary"),
            ],
        )
        self._build_group(
            self.actions,
            column=1,
            title="处理操作",
            buttons=[
                ("载入参考库", self.app.load_reference_library, "secondary"),
                ("生成空课表", self.app.parse_async, "primary"),
                ("导出 Excel", self.app.export_excel, "export"),
                ("清空列表", self.app.clear_pdf_list, "danger"),
            ],
        )

        self.file_card = ctk.CTkFrame(
            self,
            fg_color=styles.CARD_BG,
            corner_radius=styles.CARD_RADIUS,
            border_width=1,
            border_color=styles.BORDER,
            height=styles.FILE_CARD_HEIGHT,
        )
        self.file_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.file_card.grid_propagate(False)
        self.file_card.grid_columnconfigure(0, weight=1)
        self.file_card.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.file_card, fg_color=styles.CARD_BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(10, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="已导入文件", font=styles.FONT_SECTION, text_color=styles.TEXT_MAIN).grid(
            row=0, column=0, sticky="w"
        )
        self.file_count_label = ctk.CTkLabel(header, text="共 0 个 PDF", font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED)
        self.file_count_label.grid(row=0, column=1, sticky="e", padx=(12, 8))
        self.delete_selected_button = ctk.CTkButton(
            header,
            text="删除选中",
            width=92,
            height=30,
            fg_color=styles.DANGER_SOFT,
            hover_color="#FECACA",
            text_color=styles.ERROR,
            font=styles.FONT_SMALL,
            command=self.app.remove_selected_pdf,
        )
        self.delete_selected_button.grid(row=0, column=2, sticky="e")

        self.file_table = create_table(self.file_card)
        self.empty_state = _EmptyState(
            self.file_card,
            "暂无课表文件",
            "请点击上方按钮导入中方或英方课表 PDF。",
        )
        self.empty_state.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))

    def _build_group(self, parent, *, column: int, title: str, buttons: list[tuple[str, object, str]]) -> None:
        group = ctk.CTkFrame(parent, fg_color=styles.CARD_BG, corner_radius=0)
        group.grid(row=0, column=column, sticky="nsew", padx=(18 if column == 0 else 8, 18), pady=14)
        group.grid_columnconfigure((0, 1), weight=0)
        ctk.CTkLabel(group, text=title, font=styles.FONT_SECTION, text_color=styles.TEXT_MAIN).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        for index, (label, command, kind) in enumerate(buttons):
            button = _make_button(group, label, command, kind)
            button.grid(row=1 + index // 2, column=index % 2, padx=(0, 8), pady=(0, 8), sticky="w")
            self.action_buttons[label] = button

    def set_file_count(self, count: int) -> None:
        self.file_count_label.configure(text=f"共 {count} 个 PDF")

    def show_empty_state(self, show: bool) -> None:
        if show:
            self.file_table.master.grid_remove()
            self.empty_state.grid()
        else:
            self.empty_state.grid_remove()
            self.file_table.master.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))


class _EmptyState(ctk.CTkFrame):
    def __init__(self, parent, title: str, subtitle: str):
        super().__init__(parent, fg_color=styles.SURFACE_SOFT, corner_radius=styles.CARD_RADIUS)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=0)
        ctk.CTkLabel(content, text="[ ]", font=(styles.FONT_FAMILY, 18), text_color=styles.TEXT_MUTED).pack()
        ctk.CTkLabel(content, text=title, font=styles.FONT_BODY, text_color=styles.TEXT_MAIN).pack(pady=(2, 1))
        ctk.CTkLabel(content, text=subtitle, font=styles.FONT_SMALL, text_color=styles.TEXT_MUTED).pack()


def _make_button(parent, label: str, command, kind: str) -> ctk.CTkButton:
    if kind == "primary":
        return ctk.CTkButton(
            parent,
            text=label,
            width=styles.BUTTON_WIDTH,
            height=styles.BUTTON_HEIGHT,
            command=command,
            font=styles.FONT_BODY,
            fg_color=styles.PRIMARY,
            hover_color=styles.PRIMARY_HOVER,
            text_color="#FFFFFF",
        )
    if kind == "export":
        return ctk.CTkButton(
            parent,
            text=label,
            width=styles.BUTTON_WIDTH,
            height=styles.BUTTON_HEIGHT,
            command=command,
            font=styles.FONT_BODY,
            fg_color=styles.PRIMARY_SOFT,
            hover_color="#D7ECE5",
            text_color=styles.PRIMARY,
            border_width=1,
            border_color=styles.PRIMARY,
        )
    if kind == "danger":
        return ctk.CTkButton(
            parent,
            text=label,
            width=styles.BUTTON_WIDTH,
            height=styles.BUTTON_HEIGHT,
            command=command,
            font=styles.FONT_BODY,
            fg_color=styles.DANGER_SOFT,
            hover_color="#FECACA",
            text_color=styles.ERROR,
        )
    return ctk.CTkButton(
        parent,
        text=label,
        width=styles.BUTTON_WIDTH,
        height=styles.BUTTON_HEIGHT,
        command=command,
        font=styles.FONT_BODY,
        fg_color="#FFFFFF",
        hover_color="#F3F4F6",
        text_color=styles.TEXT_NAV,
        border_width=1,
        border_color=styles.BORDER,
    )
