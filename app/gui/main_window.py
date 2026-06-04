"""CustomTkinter main window for Konggu."""

from __future__ import annotations

from pathlib import Path
import queue
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk
import pandas as pd

import sitecustomize  # noqa: F401  # Reorders Anaconda/user site-packages before pandas import.
from app.bootstrap import load_reference_library_config, load_schedule_core
from app.gui import styles
from app.gui.header_bar import HeaderBar
from app.gui.import_panel import ImportPanel
from app.gui.result_tabs import ResultTabs
from app.gui.status_bar import StatusBar
from app.gui.summary_cards import SummaryCards
from app.gui.table_widgets import fill_table
from app.gui.task_runner import BackgroundTaskError, run_background_task
from app.gui.view_models import UiEvent, UiState
from app.services.availability_preview_service import build_availability_preview
from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes
from app.services.generate_availability_service import AvailabilityGenerationResult, generate_availability
from app.services.pdf_source_service import add_pdf_sources, load_reference_library_summary
from app.services.result_view_service import (
    build_detail_table,
    build_file_table,
    build_gui_process_result,
    build_member_table,
    build_processing_log_text,
    build_summary_text,
)
from core.logging_config import setup_logging
from core.models import CourseBlock, FileProcessRecord, MemberSchedule, PdfSource, ProcessResult


schedule_app = load_schedule_core()
WEEKDAYS = getattr(schedule_app, "WEEKDAYS", ["周一", "周二", "周三", "周四", "周五", "周六", "周日"])


class KongguApp(ctk.CTk):
    """Modern workflow-style desktop app for schedule parsing."""

    def __init__(self):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("green")
        super().__init__(fg_color=styles.APP_BG)
        self.title("空谷 Konggu")
        self.geometry("1200x780")
        self.minsize(1000, 650)
        styles.apply_ttk_style(self)

        self.logger = setup_logging()
        self.reference_library = load_reference_library_config()
        self.reference_library_path = str(self.reference_library.get("root_path", "")).strip()
        self.ui_events: queue.Queue[UiEvent] = queue.Queue()
        self.ui_state = UiState.IDLE

        self.pdf_sources: list[PdfSource] = []
        self.root_dir = tk.StringVar(value="")
        self.is_processing = False
        self.blocks: list[dict] = []
        self.course_blocks: list[CourseBlock] = []
        self.member_schedules: list[MemberSchedule] = []
        self.file_records: list[FileProcessRecord] = []
        self.process_result = ProcessResult()
        self.calendar_df = pd.DataFrame()
        self.timetable_df = self._load_default_timetable()
        self.occupancy: dict = {}
        self.students: list[str] = []
        self.weeks: list[int] = []
        self.blocks_df = pd.DataFrame()
        self.all_slot_df = pd.DataFrame()
        self.free_df = pd.DataFrame()
        self.preview_df = pd.DataFrame()
        self.availability_slots = []
        self.errors: list[str] = []

        self._build_ui()
        self._set_state(UiState.IDLE, "请先导入课表 PDF")
        self.after(100, self.process_ui_events)

    def _load_default_timetable(self) -> pd.DataFrame:
        """加载默认节次时间表；失败时给出最小可用表。"""
        try:
            timetable = schedule_app.default_timetable()
            timetable, _ = schedule_app.validate_timetable(timetable)
            return timetable
        except Exception:
            return pd.DataFrame([{"period": i, "start": "", "end": ""} for i in range(1, 12)])

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.main_area = ctk.CTkFrame(self, fg_color=styles.APP_BG, corner_radius=0)
        self.main_area.grid(row=0, column=0, sticky="nsew", padx=20, pady=(14, 10))
        self.main_area.grid_columnconfigure(0, weight=1)
        self.main_area.grid_rowconfigure(4, weight=1)

        self.header_bar = HeaderBar(self.main_area, self)
        self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.import_panel = ImportPanel(self.main_area, self)
        self.import_panel.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.summary_cards = SummaryCards(self.main_area, self)
        self.summary_cards.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.progress_panel = self._build_progress_panel(self.main_area)
        self.progress_panel.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        self.result_tabs = ResultTabs(self.main_area, self)
        self.result_tabs.grid(row=4, column=0, sticky="nsew")

        self.status_bar = StatusBar(self)
        self.status_bar.grid(row=1, column=0, sticky="ew")
        self._refresh_file_list()
        self._refresh_summary_cards()

    def _build_progress_panel(self, parent) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(
            parent,
            fg_color=styles.CARD_BG,
            corner_radius=styles.CARD_RADIUS,
            border_width=1,
            border_color=styles.BORDER,
            height=styles.TASK_CARD_HEIGHT,
        )
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(panel, text="当前任务", font=styles.FONT_SECTION, text_color=styles.TEXT_MAIN).grid(
            row=0, column=0, padx=18, pady=(9, 1), sticky="w"
        )
        self.current_task_label = ctk.CTkLabel(
            panel,
            text="等待导入课表 PDF",
            font=styles.FONT_BODY,
            text_color=styles.TEXT_MUTED,
        )
        self.current_task_label.grid(row=1, column=0, padx=18, pady=(0, 4), sticky="w")
        self.current_file_label = ctk.CTkLabel(
            panel,
            text="当前文件：-",
            font=styles.FONT_SMALL,
            text_color=styles.TEXT_MUTED,
        )
        self.current_file_label.grid(row=2, column=0, padx=18, pady=(0, 6), sticky="w")
        self.progress_bar = ctk.CTkProgressBar(panel, height=8, progress_color=styles.PRIMARY, mode="determinate")
        self.progress_bar.grid(row=3, column=0, padx=18, pady=(0, 10), sticky="ew")
        self.progress_bar.set(0)
        return panel

    def show_settings_hint(self) -> None:
        messagebox.showinfo(
            "设置",
            "设置功能暂未开放，当前可在 config 文件中调整课表时间、校历和参考库配置。",
        )
        self._enqueue_event("log", "设置功能暂未开放，当前可在 config 文件中调整。", level="INFO")

    def add_mixed_pdfs(self):
        paths = filedialog.askopenfilenames(
            title="选择中方/英方课表 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        self._add_pdf_paths(paths, None)

    def add_pdfs(self, kind: str):
        paths = filedialog.askopenfilenames(
            title=f"选择{kind}课表 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        self._add_pdf_paths(paths, kind if kind in {"中方", "英方"} else "中方")

    def _add_pdf_paths(self, paths, kind: str | None):
        result = add_pdf_sources(
            paths=paths,
            explicit_kind=kind,
            existing_sources=self.pdf_sources,
            schedule_core=schedule_app,
        )
        self.pdf_sources.extend(result.added)
        for source in result.added:
            self.logger.info("添加 PDF: %s kind=%s hash=%s", source.file_name, source.kind, source.content_hash)
            self._enqueue_event("log", f"已添加 {source.file_name}", level="INFO")
        for pdf_path, exc in result.errors:
            self.logger.warning("PDF 文件读取失败: %s error=%s", pdf_path, exc)
            messagebox.showwarning("文件读取失败", f"{pdf_path.name}\n{exc}")
            self._enqueue_event("log", f"{pdf_path.name} 读取失败：{exc}", level="ERROR")
        self._refresh_file_list()
        self._refresh_summary_cards()
        self._set_state(UiState.FILES_SELECTED if self.pdf_sources else UiState.IDLE)

    def _refresh_file_list(self) -> None:
        has_files = bool(self.pdf_sources or self.file_records)
        self.import_panel.show_empty_state(not has_files)
        self.import_panel.set_file_count(len(self.pdf_sources) or len(self.file_records))
        if has_files:
            fill_table(self.import_panel.file_table, build_file_table(self.pdf_sources, self.file_records))
        self.import_panel.file_table.bind("<<TreeviewSelect>>", lambda _event: self._update_button_states())

    def remove_selected_pdf(self):
        selection = list(self.import_panel.file_table.selection())
        if not selection:
            messagebox.showinfo("未选择文件", "请先在文件列表中选择要删除的 PDF。")
            return
        indices = [self.import_panel.file_table.index(item) for item in selection]
        for index in sorted(indices, reverse=True):
            if 0 <= index < len(self.pdf_sources):
                del self.pdf_sources[index]
        self._refresh_file_list()
        self._refresh_summary_cards()
        self._set_state(UiState.FILES_SELECTED if self.pdf_sources else UiState.IDLE)

    def clear_pdf_list(self):
        self.pdf_sources.clear()
        self.file_records = []
        self._refresh_file_list()
        self._refresh_summary_cards()
        self._set_state(UiState.IDLE, "已清空文件列表")
        self._enqueue_event("log", "已清空 PDF 文件列表", level="INFO")

    def choose_root_dir(self):
        directory = filedialog.askdirectory(title="选择包含课表 PDF 的文件夹")
        if directory:
            self.root_dir.set(directory)
            self._set_state(UiState.FILES_SELECTED, f"已选择目录：{directory}")
            self._enqueue_event("log", f"已选择课表目录：{directory}", level="INFO")

    def load_reference_library(self):
        if not self.reference_library_path:
            messagebox.showwarning("未配置参考库", "没有找到 config/reference_library.json 中的参考库路径。")
            return
        try:
            summary = load_reference_library_summary(self.reference_library_path)
        except FileNotFoundError:
            messagebox.showwarning("参考库不存在", f"当前电脑上找不到参考库目录：\n{self.reference_library_path}")
            return
        self.root_dir.set(str(summary.path))
        self._set_state(UiState.FILES_SELECTED, f"已载入参考库：{summary.path}")
        self._enqueue_event("log", f"参考库共 {summary.pdf_count} 个 PDF，可直接生成空课表。", level="INFO")

    def parse_async(self):
        if self.is_processing:
            messagebox.showinfo("正在处理", "当前已有解析任务在运行，请等待完成。")
            return
        selected_count = len(self.pdf_sources)
        root = self.root_dir.get().strip()
        if not selected_count and not root:
            messagebox.showwarning("缺少 PDF", "请先导入课表 PDF。")
            self._set_state(UiState.IDLE, "请先导入课表 PDF")
            return

        self.is_processing = True
        current = self._current_parse_target_text()
        self._set_state(UiState.RUNNING, "正在解析课表 PDF")
        self.current_file_label.configure(text=f"当前文件：{current}")
        self._enqueue_event("log", f"正在读取 {selected_count or '目录内'} 个 PDF", level="INFO")
        self.progress_bar.set(0.15)
        run_background_task(
            owner=self,
            task=self._generate_availability,
            on_success=self._apply_parse_result,
            on_error=self._show_parse_error,
            logger=self.logger,
            error_message="解析失败",
        )

    def _generate_availability(self) -> AvailabilityGenerationResult:
        target = self._current_parse_target_text()
        self._enqueue_event("status", f"正在解析：{target}")
        self._enqueue_event("progress", f"正在处理 {target}", progress=0.35)
        self.logger.info(
            "开始解析 PDF: count=%s mode=%s",
            len(self.pdf_sources),
            "selected_files" if self.pdf_sources else "root_directory",
        )
        result = generate_availability(
            schedule_core=schedule_app,
            selected_sources=list(self.pdf_sources),
            root_dir=self.root_dir.get().strip(),
            weekdays=WEEKDAYS,
        )
        self.logger.info(
            "解析完成: files=%s members=%s blocks=%s weeks=%s elapsed=%.2fs",
            len(result.model_sources),
            len(result.students),
            len(result.blocks),
            len(result.weeks),
            result.elapsed_seconds,
        )
        self._enqueue_event("progress", "解析结果已生成", progress=0.9)
        return result

    def _apply_parse_result(self, result: AvailabilityGenerationResult):
        self.blocks = result.blocks
        self.course_blocks = result.course_blocks
        self.calendar_df = result.calendar_df
        self.occupancy = result.occupancy
        self.students = result.students
        self.weeks = result.weeks
        self.blocks_df = result.blocks_df
        self.all_slot_df = result.all_slot_df
        self.errors = result.errors
        self.preview_df = result.preview_df
        self.member_schedules = result.member_schedules
        self.file_records = result.file_records

        self.progress_bar.set(1)
        self.is_processing = False
        self._set_state(UiState.COMPLETED, "解析完成")
        self.current_file_label.configure(text="当前文件：已完成")
        self.refresh_empty_schedule_table()
        self.process_result = build_gui_process_result(
            course_blocks=self.course_blocks,
            members=self.member_schedules,
            file_records=self.file_records,
            slots=getattr(self, "availability_slots", []),
            student_count=len(result.students),
            block_count=len(result.blocks),
            week_count=len(result.weeks),
            calendar_df=result.calendar_df,
        )
        self._refresh_file_list()
        self._refresh_summary_cards()
        fill_table(self.result_tabs.member_tab.table, build_member_table(self.member_schedules))
        self.result_tabs.member_tab.show_table(bool(self.member_schedules))
        fill_table(self.result_tabs.detail_tab.table, build_detail_table(self.file_records))
        self.result_tabs.detail_tab.show_table(bool(self.file_records))
        self._set_log_text(
            build_processing_log_text(errors=self.errors, preview_df=self.preview_df, file_records=self.file_records)
        )
        self.result_tabs.set_status("最近生成：已完成")
        self.result_tabs.select("空课表结果")
        self._enqueue_event("log", "空课表生成完成", level="SUCCESS")

    def _show_parse_error(self, error: BackgroundTaskError):
        self.is_processing = False
        self._set_state(UiState.FAILED, f"解析失败：{error.exception}")
        self.current_file_label.configure(text="当前文件：解析失败")
        self._set_log_text(f"[ERROR] 解析失败：{error.exception}\n\n{error.detail}")
        messagebox.showerror("解析失败", str(error.exception))

    def refresh_empty_schedule_table(self):
        preview = build_availability_preview(
            occupancy=self.occupancy,
            students=self.students,
            weeks=self.weeks,
            weekdays=WEEKDAYS,
            calendar_df=self.calendar_df,
            timetable_df=self.timetable_df,
        )
        self.free_df = preview.free_df
        self.availability_slots = preview.slots
        fill_table(self.result_tabs.availability_tab.table, self.free_df)
        self.result_tabs.availability_tab.show_table(not self.free_df.empty)
        self._refresh_summary_cards()

    def export_excel(self):
        if not self.occupancy or not self.students:
            messagebox.showwarning("无法导出", "请先生成空课表。")
            return

        path = filedialog.asksaveasfilename(
            title="保存 Excel 空课表",
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            initialfile="空课表.xlsx",
        )
        if not path:
            return

        try:
            self.logger.info(
                "开始导出 Excel: members=%s slots=%s path=%s",
                self.process_result.summary.member_count or len(self.students),
                self.process_result.summary.slot_count or len(self.free_df),
                path,
            )
            data = build_export_excel_bytes(
                schedule_core=schedule_app,
                request=ExcelExportRequest(
                    occupancy=self.occupancy,
                    students=self.students,
                    weeks=self.weeks,
                    calendar_df=self.calendar_df,
                    timetable_df=self.timetable_df,
                    threshold=0,
                    blocks_df=self.blocks_df,
                    all_slot_df=self.all_slot_df,
                ),
            )
            Path(path).write_bytes(data)
            self._set_state(UiState.EXPORTED, f"已导出：{path}")
            self._enqueue_event("log", f"已导出到 {path}", level="EXPORT")
            messagebox.showinfo("导出成功", f"已保存：{path}")
        except PermissionError as exc:
            self.logger.exception("Excel 导出失败: %s", path)
            self._enqueue_event("log", "Excel 文件可能正在被 WPS/Excel 打开，请关闭后重试。", level="ERROR")
            messagebox.showerror("导出失败", "Excel 文件可能正在被 WPS/Excel 打开，请关闭后重试。")
        except Exception as exc:
            self.logger.exception("Excel 导出失败: %s", path)
            self._enqueue_event("log", f"导出失败：{exc}", level="ERROR")
            messagebox.showerror("导出失败", str(exc))

    def _refresh_summary_cards(self) -> None:
        complete_count = sum(1 for member in self.member_schedules if member.status == "完整")
        warning_count = len(self.errors) + sum(1 for record in self.file_records if record.status == "解析失败")
        self.summary_cards.update_values(
            pdf_count=len(self.pdf_sources) or len(self.file_records),
            member_count=len(self.students),
            complete_count=complete_count,
            warning_count=warning_count,
        )

    def _set_state(self, state: UiState, message: str | None = None) -> None:
        self.ui_state = state
        label, color = {
            UiState.IDLE: ("空闲", styles.TEXT_MUTED),
            UiState.FILES_SELECTED: ("已选择文件", styles.INFO),
            UiState.READY: ("已就绪", styles.PRIMARY),
            UiState.RUNNING: ("正在解析", styles.PRIMARY),
            UiState.COMPLETED: ("已完成", styles.SUCCESS),
            UiState.FAILED: ("失败", styles.ERROR),
            UiState.EXPORTED: ("已导出", styles.SUCCESS),
        }[state]
        if hasattr(self, "header_bar"):
            self.header_bar.set_badge(label, color)
        if message and hasattr(self, "current_task_label"):
            self.current_task_label.configure(text=message)
        if hasattr(self, "status_bar"):
            self.status_bar.set_status(message or label, label)
        if hasattr(self, "current_file_label") and state in {UiState.IDLE, UiState.FILES_SELECTED, UiState.READY, UiState.EXPORTED}:
            self.current_file_label.configure(text="当前文件：-")
        if hasattr(self, "progress_bar") and state in {UiState.IDLE, UiState.FILES_SELECTED, UiState.READY}:
            self.progress_bar.set(0)
        self._update_button_states()

    def _update_button_states(self) -> None:
        if not hasattr(self, "import_panel"):
            return
        buttons = self.import_panel.action_buttons
        add_state = "disabled" if self.ui_state == UiState.RUNNING else "normal"
        generate_state = (
            "normal"
            if self.ui_state in {UiState.FILES_SELECTED, UiState.READY, UiState.COMPLETED, UiState.FAILED, UiState.EXPORTED}
            and (self.pdf_sources or self.root_dir.get().strip())
            else "disabled"
        )
        export_state = "normal" if self.ui_state in {UiState.COMPLETED, UiState.EXPORTED} and self.students else "disabled"
        has_selection = bool(self.import_panel.file_table.selection())
        delete_state = "normal" if self.ui_state != UiState.RUNNING and has_selection else "disabled"
        clear_state = "disabled" if self.ui_state == UiState.RUNNING or not self.pdf_sources else "normal"
        for label in ["添加中方 PDF", "添加英方 PDF", "批量导入 PDF", "选择课表目录", "载入参考库"]:
            buttons[label].configure(state=add_state)
        buttons["清空列表"].configure(state=clear_state)
        buttons["生成空课表"].configure(state=generate_state)
        buttons["导出 Excel"].configure(state=export_state)
        self.import_panel.delete_selected_button.configure(state=delete_state)

    def _set_log_text(self, text: str) -> None:
        self.result_tabs.log_tab.set_text(text)

    def _enqueue_event(self, event_type: str, message: str, *, level: str = "INFO", progress: float | None = None) -> None:
        self.ui_events.put(UiEvent(event_type=event_type, message=message, level=level, progress=progress))

    def process_ui_events(self) -> None:
        while True:
            try:
                event = self.ui_events.get_nowait()
            except queue.Empty:
                break
            if event.event_type == "log":
                self.result_tabs.log_tab.append(event.level, event.message)
            elif event.event_type == "progress" and event.progress is not None:
                self.progress_bar.set(event.progress)
                if event.message and hasattr(self, "current_file_label"):
                    self.current_file_label.configure(text=f"当前文件：{event.message}")
            elif event.event_type == "status":
                self.status_bar.set_status(event.message)
        self.after(100, self.process_ui_events)

    def _current_parse_target_text(self) -> str:
        if self.pdf_sources:
            if len(self.pdf_sources) == 1:
                return self.pdf_sources[0].file_name
            return self.pdf_sources[0].file_name + f" 等 {len(self.pdf_sources)} 个文件"
        root = self.root_dir.get().strip()
        return root or "-"


CourseScheduleDesktop = KongguApp
