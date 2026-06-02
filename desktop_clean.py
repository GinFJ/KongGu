import contextlib
import importlib.util
import io
import json
import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, X, Y, filedialog, messagebox, ttk
import tkinter as tk

import sitecustomize  # noqa: F401  # Reorders Anaconda/user site-packages before pandas import.
import pandas as pd

from core.legacy_adapter import (
    availability_slot_from_row,
    build_file_records,
    build_member_schedules,
    build_process_result,
    course_blocks_from_legacy,
    legacy_source_to_model,
    model_sources_to_legacy,
    pdf_source_from_path,
)
from core.logging_config import setup_logging
from core.models import CourseBlock, FileProcessRecord, MemberSchedule, PdfSource, ProcessResult


FONT_FAMILY = "Microsoft YaHei UI"

PALETTE = {
    "bg": "#F4F8F1",
    "surface": "#FFFFFF",
    "surface_soft": "#F8FBF5",
    "nav": "#FDFEF9",
    "nav_card": "#FFFFFF",
    "nav_muted": "#6F7F72",
    "text": "#13241C",
    "muted": "#6F7B74",
    "border": "#DCE8DC",
    "accent": "#1F9D6B",
    "accent_dark": "#087A58",
    "accent_soft": "#E7F7EE",
    "success": "#139968",
    "danger": "#C2410C",
    "warning": "#B7791F",
    "row_alt": "#F7FBF4",
}


# 桌面版额外依赖说明：
# pip install pandas openpyxl PyMuPDF pyinstaller
#
# 如果要识别扫描版 PDF，还需要：
# pip install paddlepaddle paddleocr opencv-python-headless
#
# 本程序是独立桌面入口，不再启动 Streamlit，也不再显示网页。


def _resource_path(name: str) -> Path:
    """返回打包后的资源路径；开发模式下返回当前脚本同目录资源。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent / name


def _load_reference_library_config() -> dict:
    """加载真实课表参考库配置；配置缺失时返回空字典。"""
    config_path = _resource_path("config") / "reference_library.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _configure_runtime_environment():
    """Keep third-party caches inside the project/bundle instead of user home."""
    if getattr(sys, "frozen", False):
        cache_root = Path(sys.executable).resolve().parent / "cache"
    else:
        cache_root = _resource_path("cache")
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_paths = {
        "MPLCONFIGDIR": cache_root / "matplotlib",
        "PADDLE_PDX_CACHE_HOME": cache_root / "paddlex",
        "PADDLE_HOME": cache_root / "paddle",
        "PADDLEOCR_HOME": cache_root / "paddleocr",
        "KONGGU_OCR_TEXT_CACHE": cache_root / "pdf_text",
        "KONGGU_OCR_LAYOUT_CACHE": cache_root / "pdf_layout",
        "KONGGU_PARSE_CACHE": cache_root / "parsed_blocks",
    }
    for env_name, path in cache_paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(env_name, str(path))
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def load_schedule_core():
    """加载从原程序中恢复出的课表解析核心模块。"""
    _configure_runtime_environment()
    os.environ.setdefault("STREAMLIT_GLOBAL_SUPPRESS_DEPRECATION_WARNINGS", "true")
    for logger_name in (
        "streamlit",
        "streamlit.runtime",
        "streamlit.runtime.caching",
        "streamlit.runtime.scriptrunner_utils",
    ):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    # 开发调试时如果存在 app.py，优先直接导入。
    try:
        from core import schedule_core

        return schedule_core
    except Exception as exc:
        raise RuntimeError("源码版课表核心加载失败，请检查 core/schedule_core.py。") from exc

    candidates = [
        _resource_path("_recovered_core") / "app.pyc",
        Path(__file__).resolve().parent / "_recovered_core" / "app.pyc",
        Path.cwd() / "_recovered_core" / "app.pyc",
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue

        spec = importlib.util.spec_from_file_location("schedule_app_core", candidate)
        if spec is None or spec.loader is None:
            continue

        module = importlib.util.module_from_spec(spec)
        sys.modules["schedule_app_core"] = module
        with contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
        return module

    raise RuntimeError("未找到课表解析核心模块 app.pyc。请重新生成桌面程序。")


schedule_app = load_schedule_core()
WEEKDAYS = getattr(schedule_app, "WEEKDAYS", ["周一", "周二", "周三", "周四", "周五", "周六", "周日"])


class CourseScheduleDesktop(tk.Tk):
    """课表合并与空课表生成桌面程序。"""

    def __init__(self):
        super().__init__()
        self.title("青禾计划 · 空谷")
        self.geometry("1280x820")
        self.minsize(1120, 720)
        self.configure(bg=PALETTE["bg"])
        self._setup_theme()
        self.logger = setup_logging()
        self.reference_library = _load_reference_library_config()
        self.reference_library_path = str(self.reference_library.get("root_path", "")).strip()

        self.pdf_sources: list[PdfSource] = []
        # 参考库只作为快捷入口，不默认参与解析，避免用户手动添加几个 PDF 后又把整库扫一遍。
        self.root_dir = tk.StringVar(value="")
        self.is_processing = False
        # 空课表需要全量导出所有节次，不再按“最少空闲人数”过滤。
        self.threshold = tk.IntVar(value=0)
        initial_status = "请选择课表 PDF，或点击“载入参考库”后按目录批量解析。"
        self.status_text = tk.StringVar(value=initial_status)
        self.summary_text = tk.StringVar(value="")
        self.metric_students = tk.StringVar(value="0")
        self.metric_blocks = tk.StringVar(value="0")
        self.metric_weeks = tk.StringVar(value="0")
        self.metric_free_slots = tk.StringVar(value="0")

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

    def _setup_theme(self):
        """统一 ttk 控件的主题，让桌面程序看起来更像完整产品。"""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=(FONT_FAMILY, 10), foreground=PALETTE["text"])
        style.configure("TFrame", background=PALETTE["bg"])
        style.configure("Surface.TFrame", background=PALETTE["surface"])
        style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
        style.configure("Muted.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
        style.configure("Surface.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"])
        style.configure("SurfaceMuted.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])

        style.configure(
            "Primary.TButton",
            font=(FONT_FAMILY, 10, "bold"),
            padding=(14, 10),
            borderwidth=0,
            foreground="#FFFFFF",
            background=PALETTE["accent"],
        )
        style.map(
            "Primary.TButton",
            background=[("active", PALETTE["accent_dark"]), ("disabled", "#D5DCE7")],
            foreground=[("disabled", "#7B8493")],
        )
        style.configure(
            "Secondary.TButton",
            font=(FONT_FAMILY, 10),
            padding=(12, 9),
            borderwidth=1,
            relief="flat",
            foreground=PALETTE["text"],
            background="#FFFFFF",
        )
        style.map("Secondary.TButton", background=[("active", "#ECF7EF")])
        style.configure(
            "Export.TButton",
            font=(FONT_FAMILY, 10, "bold"),
            padding=(12, 9),
            borderwidth=0,
            foreground="#FFFFFF",
            background=PALETTE["success"],
        )
        style.map("Export.TButton", background=[("active", "#087A58")])

        style.configure(
            "TNotebook",
            background=PALETTE["surface"],
            borderwidth=0,
            tabmargins=(4, 4, 4, 0),
        )
        style.configure(
            "TNotebook.Tab",
            font=(FONT_FAMILY, 10, "bold"),
            padding=(18, 10),
            background=PALETTE["surface_soft"],
            foreground=PALETTE["muted"],
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PALETTE["accent_soft"])],
            foreground=[("selected", PALETTE["accent_dark"])],
        )

        style.configure(
            "Treeview",
            rowheight=30,
            borderwidth=0,
            relief="flat",
            background=PALETTE["surface"],
            fieldbackground=PALETTE["surface"],
            foreground=PALETTE["text"],
        )
        style.configure(
            "Treeview.Heading",
            font=(FONT_FAMILY, 10, "bold"),
            padding=(8, 8),
            background="#ECF5EA",
            foreground=PALETTE["text"],
            relief="flat",
        )
        style.map("Treeview", background=[("selected", "#DDF4E7")], foreground=[("selected", PALETTE["text"])])
        style.configure("Horizontal.TProgressbar", troughcolor="#D6E8D8", background=PALETTE["accent"])

    def _load_default_timetable(self) -> pd.DataFrame:
        """加载默认节次时间表；失败时给出最小可用表。"""
        try:
            timetable = schedule_app.default_timetable()
            timetable, _ = schedule_app.validate_timetable(timetable)
            return timetable
        except Exception:
            return pd.DataFrame(
                [
                    {"period": i, "start": "", "end": ""}
                    for i in range(1, 12)
                ]
            )

    def _build_ui(self):
        """构建桌面界面。"""
        outer = tk.Frame(self, bg=PALETTE["bg"])
        outer.pack(fill=BOTH, expand=True)

        left = tk.Frame(outer, width=330, bg=PALETTE["nav"])
        left.pack(side=LEFT, fill=Y)
        left.pack_propagate(False)

        right = tk.Frame(outer, bg=PALETTE["bg"])
        right.pack(side=RIGHT, fill=BOTH, expand=True)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _sidebar_card(self, parent, title: str, subtitle: str = "") -> tk.Frame:
        """创建左侧深色卡片。"""
        card = tk.Frame(parent, bg=PALETTE["nav_card"], highlightthickness=1, highlightbackground=PALETTE["border"])
        card.pack(fill=X, padx=16, pady=(0, 12))

        inner = tk.Frame(card, bg=PALETTE["nav_card"])
        inner.pack(fill=BOTH, expand=True, padx=14, pady=14)

        tk.Label(
            inner,
            text=title,
            bg=PALETTE["nav_card"],
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 12, "bold"),
        ).pack(anchor="w")
        if subtitle:
            tk.Label(
                inner,
                text=subtitle,
                bg=PALETTE["nav_card"],
                fg=PALETTE["muted"],
                font=(FONT_FAMILY, 9),
                wraplength=240,
                justify="left",
            ).pack(anchor="w", pady=(4, 10))
        return inner

    def _surface_card(self, parent) -> tk.Frame:
        """创建右侧浅色内容卡片。"""
        card = tk.Frame(parent, bg=PALETTE["surface"], highlightthickness=1, highlightbackground=PALETTE["border"])
        card.pack(fill=BOTH, expand=True)
        return card

    def _draw_header(self, event):
        """绘制顶部渐变横幅，避免引入外部图片资源。"""
        canvas = event.widget
        width = max(event.width, 1)
        height = max(event.height, 1)
        canvas.delete("all")

        start = self._hex_to_rgb("#FFFFFF")
        end = self._hex_to_rgb("#E7F8DE")
        steps = 120
        for i in range(steps):
            ratio = i / max(steps - 1, 1)
            color = self._rgb_to_hex(
                tuple(int(start[j] * (1 - ratio) + end[j] * ratio) for j in range(3))
            )
            x0 = int(width * i / steps)
            x1 = int(width * (i + 1) / steps) + 1
            canvas.create_rectangle(x0, 0, x1, height, fill=color, outline=color)

        for x in range(0, width, 32):
            canvas.create_line(x, 0, x, height, fill="#E7EFE6", width=1)
        for y in range(0, height, 32):
            canvas.create_line(0, y, width, y, fill="#EAF1E8", width=1)

        canvas.create_text(
            32,
            28,
            anchor="nw",
            text="空谷 · 空课表生成",
            fill=PALETTE["text"],
            font=(FONT_FAMILY, 24, "bold"),
        )
        canvas.create_text(
            34,
            78,
            anchor="nw",
            text="导入 PDF，检查成员课表完整性，生成 Excel 空课表",
            fill=PALETTE["muted"],
            font=(FONT_FAMILY, 11),
        )

        canvas.create_rectangle(0, height - 3, width, height, fill="#BEE7C9", outline="#BEE7C9")
        canvas.create_arc(width - 190, 22, width - 70, 102, start=20, extent=105, outline="#50B982", width=4)
        canvas.create_line(width - 132, 82, width - 108, 42, fill="#139968", width=4)
        canvas.create_oval(width - 120, 54, width - 70, 78, fill="#9FDB85", outline="")
        canvas.create_line(width - 222, 86, width - 62, 56, fill="#A9DDE8", width=2, smooth=True)

    @staticmethod
    def _hex_to_rgb(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(value: tuple[int, int, int]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*value)

    def _build_left_panel(self, parent):
        """左侧：只保留 PDF 导入与解析操作。"""
        brand = tk.Frame(parent, bg=PALETTE["nav"])
        brand.pack(fill=X, padx=20, pady=(22, 20))

        tk.Label(
            brand,
            text="空谷",
            bg=PALETTE["nav"],
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="青禾计划系列",
            bg=PALETTE["nav"],
            fg=PALETTE["nav_muted"],
            font=(FONT_FAMILY, 10),
        ).pack(anchor="w", pady=(4, 0))

        import_frame = self._sidebar_card(
            parent,
            "课表 PDF 导入",
            "推荐直接批量添加，系统会按文件名识别中方/英方。",
        )

        ttk.Button(
            import_frame,
            text="批量添加所有课表 PDF",
            style="Primary.TButton",
            command=self.add_mixed_pdfs,
        ).pack(fill=X, pady=(0, 8))

        row = tk.Frame(import_frame, bg=PALETTE["nav_card"])
        row.pack(fill=X)
        ttk.Button(
            row,
            text="添加中方 PDF",
            style="Secondary.TButton",
            command=lambda: self.add_pdfs("中方"),
        ).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(
            row,
            text="添加英方 PDF",
            style="Secondary.TButton",
            command=lambda: self.add_pdfs("英方"),
        ).pack(side=RIGHT, fill=X, expand=True, padx=(4, 0))

        action_frame = self._sidebar_card(parent, "生成与导出")
        self.parse_button = ttk.Button(
            action_frame,
            text="生成空课表",
            style="Primary.TButton",
            command=self.parse_async,
        )
        self.parse_button.pack(fill=X, pady=(0, 8))
        ttk.Button(
            action_frame,
            text="导出 Excel 空课表",
            style="Export.TButton",
            command=self.export_excel,
        ).pack(fill=X, pady=(0, 10))
        self.progress = ttk.Progressbar(action_frame, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(fill=X, pady=(0, 8))
        tk.Label(
            action_frame,
            text="导出范围：全量 1-11 节，包含中午和晚上。",
            bg=PALETTE["nav_card"],
            fg=PALETTE["muted"],
            wraplength=240,
            justify="left",
            font=(FONT_FAMILY, 9),
        ).pack(anchor="w")

        pdf_frame = self._sidebar_card(parent, "已选择 PDF")
        tk.Label(
            pdf_frame,
            text="文件列表",
            bg=PALETTE["nav_card"],
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(2, 5))
        list_wrap = tk.Frame(pdf_frame, bg=PALETTE["nav_card"])
        list_wrap.pack(fill=BOTH, expand=False, pady=(4, 8))
        self.pdf_list = tk.Listbox(
            list_wrap,
            height=5,
            activestyle="none",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            selectbackground=PALETTE["accent"],
            selectforeground="#FFFFFF",
            bg="#F8FAFC",
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 9),
        )
        scrollbar = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.pdf_list.yview)
        self.pdf_list.configure(yscrollcommand=scrollbar.set)
        self.pdf_list.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        row2 = tk.Frame(pdf_frame, bg=PALETTE["nav_card"])
        row2.pack(fill=X)
        ttk.Button(row2, text="删除选中", style="Secondary.TButton", command=self.remove_selected_pdf).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(row2, text="清空列表", style="Secondary.TButton", command=self.clear_pdf_list).pack(side=RIGHT, fill=X, expand=True, padx=(4, 0))

        dir_frame = self._sidebar_card(
            parent,
            "本地课表根目录",
            "如果课表已经按部门存在一个文件夹里，可以直接选择根目录递归读取。",
        )
        tk.Label(
            dir_frame,
            text="目录路径",
            bg=PALETTE["nav_card"],
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        ttk.Entry(dir_frame, textvariable=self.root_dir).pack(fill=X, pady=(0, 8))
        ttk.Button(dir_frame, text="载入参考库", style="Primary.TButton", command=self.load_reference_library).pack(fill=X, pady=(0, 8))
        ttk.Button(dir_frame, text="选择目录", style="Secondary.TButton", command=self.choose_root_dir).pack(fill=X)

        status_frame = self._sidebar_card(parent, "状态")
        tk.Label(
            status_frame,
            textvariable=self.status_text,
            wraplength=240,
            justify="left",
            bg=PALETTE["nav_card"],
            fg=PALETTE["muted"],
            font=(FONT_FAMILY, 9),
        ).pack(anchor="w")
        tk.Label(
            status_frame,
            textvariable=self.summary_text,
            wraplength=240,
            justify="left",
            bg=PALETTE["nav_card"],
            fg=PALETTE["accent_dark"],
            font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(10, 0))

    def _build_right_panel(self, parent):
        """右侧：显示解析概览、空课表预览和占用明细。"""
        content = tk.Frame(parent, bg=PALETTE["bg"])
        content.pack(fill=BOTH, expand=True, padx=22, pady=20)

        self.header_canvas = tk.Canvas(content, height=124, highlightthickness=0, bd=0, bg=PALETTE["bg"])
        self.header_canvas.pack(fill=X, pady=(0, 14))
        self.header_canvas.bind("<Configure>", self._draw_header)

        metrics = tk.Frame(content, bg=PALETTE["bg"])
        metrics.pack(fill=X, pady=(0, 14))
        self._metric_card(metrics, "成员数", self.metric_students, "参与合并的人数").pack(side=LEFT, fill=X, expand=True, padx=(0, 8))
        self._metric_card(metrics, "时间块", self.metric_blocks, "识别出的课程片段").pack(side=LEFT, fill=X, expand=True, padx=8)
        self._metric_card(metrics, "教学周", self.metric_weeks, "覆盖的周次数量").pack(side=LEFT, fill=X, expand=True, padx=8)
        self._metric_card(metrics, "空课表格", self.metric_free_slots, "按周次与节次全量生成").pack(side=LEFT, fill=X, expand=True, padx=(8, 0))

        notebook_card = self._surface_card(content)
        notebook = ttk.Notebook(notebook_card)
        notebook.pack(fill=BOTH, expand=True, padx=12, pady=12)

        self.tab_free = ttk.Frame(notebook, padding=8)
        self.tab_members = ttk.Frame(notebook, padding=8)
        self.tab_blocks = ttk.Frame(notebook, padding=8)
        self.tab_errors = ttk.Frame(notebook, padding=8)

        notebook.add(self.tab_free, text="空课表导出")
        notebook.add(self.tab_members, text="成员检查")
        notebook.add(self.tab_blocks, text="识别明细")
        notebook.add(self.tab_errors, text="处理日志")

        self.free_tree = self._create_tree(self.tab_free)
        self.members_tree = self._create_tree(self.tab_members)
        self.blocks_tree = self._create_tree(self.tab_blocks)

        self.error_box = tk.Text(
            self.tab_errors,
            wrap="word",
            height=20,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            bg=PALETTE["surface_soft"],
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 10),
            padx=14,
            pady=12,
        )
        self.error_box.pack(fill=BOTH, expand=True)
        self.error_box.insert("1.0", "暂无提示。")
        self.error_box.configure(state="disabled")

    def _metric_card(self, parent, title: str, value_var: tk.StringVar, desc: str) -> tk.Frame:
        """右侧统计卡片。"""
        card = tk.Frame(parent, bg=PALETTE["surface"], highlightthickness=1, highlightbackground=PALETTE["border"])
        tk.Label(
            card,
            text=title,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(
            card,
            textvariable=value_var,
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=(FONT_FAMILY, 22, "bold"),
        ).pack(anchor="w", padx=16, pady=(4, 0))
        tk.Label(
            card,
            text=desc,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=(FONT_FAMILY, 9),
        ).pack(anchor="w", padx=16, pady=(0, 14))
        return card

    def _create_tree(self, parent) -> ttk.Treeview:
        """创建可横向/纵向滚动的数据表。"""
        frame = ttk.Frame(parent, style="Surface.TFrame")
        frame.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(frame, show="headings")
        tree.tag_configure("oddrow", background=PALETTE["row_alt"])
        tree.tag_configure("evenrow", background=PALETTE["surface"])
        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def add_mixed_pdfs(self):
        """批量添加 PDF，并根据文件名/路径自动判断中方或英方。"""
        paths = filedialog.askopenfilenames(
            title="选择中方/英方课表 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        for path in paths:
            inferred = schedule_app.infer_pdf_kind(Path(path).name, str(path))
            if inferred in {"中方", "英方"}:
                self._add_pdf_source(path, inferred)
            else:
                self._add_pdf_source(path, "中方")
        self._refresh_pdf_list()

    def add_pdfs(self, kind: str):
        """按指定类型添加 PDF。"""
        paths = filedialog.askopenfilenames(
            title=f"选择{kind}课表 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        for path in paths:
            self._add_pdf_source(path, kind)
        self._refresh_pdf_list()

    def _add_pdf_source(self, path: str, kind: str):
        """读取 PDF 文件为统一 PdfSource，解析时再适配旧核心。"""
        pdf_path = Path(path)
        if not pdf_path.exists():
            return
        if kind not in {"中方", "英方"}:
            kind = "中方"
        key = (str(pdf_path.resolve()), kind)
        existing = {(item.source_path, item.kind) for item in self.pdf_sources}
        if key in existing:
            return
        try:
            source = pdf_source_from_path(pdf_path, kind)
        except Exception as exc:
            self.logger.exception("PDF 文件读取失败: %s", pdf_path)
            messagebox.showwarning("文件读取失败", f"{pdf_path.name}\n{exc}")
            return
        self.pdf_sources.append(source)
        self.logger.info("添加 PDF: %s kind=%s hash=%s", source.file_name, source.kind, source.content_hash)

    def _refresh_pdf_list(self):
        """刷新 PDF 列表显示。"""
        self.pdf_list.delete(0, END)
        for item in self.pdf_sources:
            self.pdf_list.insert(END, f"[{item.kind}] {item.file_name}")
        if self.pdf_sources:
            self.status_text.set(f"已选择 {len(self.pdf_sources)} 个 PDF。可以继续添加，或直接解析。")
        else:
            self.status_text.set("请选择课表 PDF，或选择包含多人课表 PDF 的根目录。")

    def remove_selected_pdf(self):
        """删除列表中选中的 PDF。"""
        indices = list(self.pdf_list.curselection())
        for index in reversed(indices):
            del self.pdf_sources[index]
        self._refresh_pdf_list()

    def clear_pdf_list(self):
        """清空已选择 PDF。"""
        self.pdf_sources.clear()
        self._refresh_pdf_list()

    def choose_root_dir(self):
        """选择本地课表根目录。"""
        directory = filedialog.askdirectory(title="选择包含课表 PDF 的文件夹")
        if directory:
            self.root_dir.set(directory)
            self.status_text.set(f"已选择目录：{directory}")

    def load_reference_library(self):
        """一键载入真实课表参考库目录。"""
        if not self.reference_library_path:
            messagebox.showwarning("未配置参考库", "没有找到 config/reference_library.json 中的参考库路径。")
            return
        path = Path(self.reference_library_path)
        if not path.exists():
            messagebox.showwarning("参考库不存在", f"当前电脑上找不到参考库目录：\n{path}")
            return
        self.root_dir.set(str(path))
        pdf_count = len(list(path.rglob("*.pdf")))
        self.status_text.set(f"已载入参考库：{path}")
        self.summary_text.set(f"参考库共 {pdf_count} 个 PDF，可直接点击“解析并生成空课表”。")

    def parse_async(self):
        """后台解析，避免界面卡死。"""
        if self.is_processing:
            messagebox.showinfo("正在处理", "当前已有解析任务在运行，请等待完成。")
            return
        self.is_processing = True
        self.parse_button.configure(state="disabled")
        selected_count = len(self.pdf_sources)
        root = self.root_dir.get().strip()
        if selected_count:
            self.status_text.set(f"正在解析已选择的 {selected_count} 个 PDF。扫描件首次 OCR 会较慢，后续会走缓存。")
        elif root:
            self.status_text.set("正在从本地目录读取并解析 PDF。")
        else:
            self.is_processing = False
            self.parse_button.configure(state="normal")
            messagebox.showwarning("缺少 PDF", "请先添加课表 PDF，或点击“载入参考库/选择目录”。")
            return
        if hasattr(self, "progress"):
            self.progress.start(10)
        worker = threading.Thread(target=self._parse_worker, daemon=True)
        worker.start()

    def _parse_worker(self):
        """实际解析流程：PDF -> 时间块 -> 占用表 -> 空课表。"""
        started_at = pd.Timestamp.now()
        try:
            model_sources = list(self.pdf_sources)
            root = self.root_dir.get().strip()
            if not model_sources and root:
                legacy_dir_sources = schedule_app.local_pdf_sources_from_dir(root)
                model_sources.extend(legacy_source_to_model(source) for source in legacy_dir_sources)

            if not model_sources:
                raise ValueError("请先添加课表 PDF，或选择一个包含课表 PDF 的本地目录。")

            self.logger.info(
                "开始解析 PDF: count=%s mode=%s",
                len(model_sources),
                "selected_files" if self.pdf_sources else "root_directory",
            )
            legacy_sources = model_sources_to_legacy(model_sources)
            blocks, calendar_df, errors, preview_df = schedule_app.parse_actual_pdf_sources(
                legacy_sources,
                uploaded_calendar_df=None,
            )

            if calendar_df is None or calendar_df.empty:
                calendar_df = schedule_app.synthesize_calendar_from_blocks(blocks)

            if not blocks:
                raise ValueError("没有识别到可用课表时间块。请确认 PDF 清晰，且文件类型选择为中方或英方。")

            course_blocks = course_blocks_from_legacy(blocks)
            occupancy = schedule_app.build_occupancy(blocks)
            students = sorted({block["name"] for block in blocks})
            weeks = sorted({int(block["week"]) for block in blocks if block.get("week") is not None})
            periods = list(range(1, 12))
            all_slot_df = schedule_app.build_slot_table(occupancy, students, weeks, WEEKDAYS, periods)
            blocks_df = schedule_app.blocks_to_dataframe(blocks)
            member_schedules = build_member_schedules(course_blocks, students)
            file_records = build_file_records(model_sources, course_blocks, errors or [])
            elapsed = (pd.Timestamp.now() - started_at).total_seconds()
            self.logger.info(
                "解析完成: files=%s members=%s blocks=%s weeks=%s elapsed=%.2fs",
                len(model_sources),
                len(students),
                len(blocks),
                len(weeks),
                elapsed,
            )

            self.after(
                0,
                lambda: self._apply_parse_result(
                    blocks=blocks,
                    course_blocks=course_blocks,
                    calendar_df=calendar_df,
                    occupancy=occupancy,
                    students=students,
                    weeks=weeks,
                    blocks_df=blocks_df,
                    all_slot_df=all_slot_df,
                    errors=errors,
                    preview_df=preview_df,
                    member_schedules=member_schedules,
                    file_records=file_records,
                ),
            )
        except Exception as exc:
            detail = traceback.format_exc()
            self.logger.exception("解析失败")
            self.after(0, lambda exc=exc, detail=detail: self._show_parse_error(exc, detail))

    def _apply_parse_result(
        self,
        blocks,
        course_blocks,
        calendar_df,
        occupancy,
        students,
        weeks,
        blocks_df,
        all_slot_df,
        errors,
        preview_df,
        member_schedules,
        file_records,
    ):
        """把后台解析结果应用到界面。"""
        self.blocks = blocks
        self.course_blocks = course_blocks
        self.calendar_df = calendar_df
        self.occupancy = occupancy
        self.students = students
        self.weeks = weeks
        self.blocks_df = blocks_df
        self.all_slot_df = all_slot_df
        self.errors = errors or []
        self.preview_df = preview_df
        self.member_schedules = member_schedules
        self.file_records = file_records

        if hasattr(self, "progress"):
            self.progress.stop()
        self.is_processing = False
        self.parse_button.configure(state="normal")
        self.status_text.set("解析完成。")
        complete_members = sum(1 for member in self.member_schedules if member.status == "完整")
        pending_members = max(0, len(self.member_schedules) - complete_members)
        self.summary_text.set(
            f"共 {len(students)} 人，完整 {complete_members} 人，待补 {pending_members} 人。"
        )
        self.metric_students.set(str(len(students)))
        self.metric_blocks.set(str(len(blocks)))
        self.metric_weeks.set(str(len(weeks)))

        self.refresh_empty_schedule_table()
        self.process_result = build_process_result(
            blocks=self.course_blocks,
            members=self.member_schedules,
            file_records=self.file_records,
            slots=getattr(self, "availability_slots", []),
            logs=[f"共 {len(students)} 人，{len(blocks)} 个时间块，{len(weeks)} 个教学周。"],
            calendar_rows=calendar_df.to_dict("records") if calendar_df is not None and not calendar_df.empty else [],
        )
        self._fill_tree(self.members_tree, self._build_member_table())
        self._fill_tree(self.blocks_tree, self.blocks_df.head(500))
        self._fill_errors()

    def _build_member_table(self) -> pd.DataFrame:
        """生成成员课表完整性检查表。"""
        rows = []
        for member in self.member_schedules:
            rows.append(
                {
                    "成员姓名": member.name,
                    "中方课表": member.chinese_status,
                    "英方课表": member.english_status,
                    "当前状态": member.status,
                    "备注": member.display_remark,
                }
            )
        return pd.DataFrame(rows)

    def _show_parse_error(self, exc: Exception, detail: str):
        """显示解析错误。"""
        if hasattr(self, "progress"):
            self.progress.stop()
        self.is_processing = False
        self.parse_button.configure(state="normal")
        self.status_text.set(f"解析失败：{exc}")
        self._set_error_text(detail)
        messagebox.showerror("解析失败", str(exc))

    def refresh_empty_schedule_table(self):
        """生成完整空课表预览。"""
        if not self.students or not self.weeks:
            return

        total_students = len(self.students)
        rows = []
        slots = []
        date_map = self._build_date_map()
        time_map = self._build_time_map()

        for week in self.weeks:
            for weekday in WEEKDAYS:
                for period in range(1, 12):
                    busy_students = sorted(self.occupancy.get((week, weekday, period), set()))
                    free_students = [student for student in self.students if student not in busy_students]
                    free_count = total_students - len(busy_students)
                    date_value = date_map.get((week, weekday), "")
                    row = {
                        "周次": week,
                        "日期": date_value,
                        "星期": weekday,
                        "节次": period,
                        "时间": time_map.get(period, ""),
                        "空闲人数": free_count,
                        "空闲人员": "、".join(free_students),
                        "占用人数": len(busy_students),
                        "有课人员": "、".join(busy_students),
                    }
                    rows.append(row)
                    slots.append(availability_slot_from_row(row))

        self.free_df = pd.DataFrame(rows)
        self.availability_slots = slots
        self.metric_free_slots.set(str(len(self.free_df)))
        self._fill_tree(self.free_tree, self.free_df)

    def _build_date_map(self) -> dict[tuple[int, str], str]:
        """从校历或识别结果中建立 (周次, 星期) -> 日期 的映射。"""
        if self.calendar_df is None or self.calendar_df.empty:
            return {}

        df = self.calendar_df.copy()
        if "date" not in df.columns or "week" not in df.columns or "weekday" not in df.columns:
            return {}

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        mapping = {}
        for _, row in df.dropna(subset=["date"]).iterrows():
            try:
                week = int(row["week"])
            except Exception:
                continue
            weekday = str(row["weekday"])
            mapping[(week, weekday)] = row["date"].strftime("%Y-%m-%d")
        return mapping

    def _build_time_map(self) -> dict[int, str]:
        """建立节次 -> 时间范围 的映射。"""
        if self.timetable_df is None or self.timetable_df.empty:
            return {}

        mapping = {}
        for _, row in self.timetable_df.iterrows():
            try:
                period = int(row["period"])
            except Exception:
                continue
            start = str(row.get("start", "")).strip()
            end = str(row.get("end", "")).strip()
            mapping[period] = f"{start}-{end}" if start or end else ""
        return mapping

    def _fill_tree(self, tree: ttk.Treeview, df: pd.DataFrame):
        """把 DataFrame 填入表格控件。"""
        tree.delete(*tree.get_children())
        if df is None or df.empty:
            tree["columns"] = []
            return

        view_df = df.copy()
        view_df = view_df.fillna("")
        columns = [str(col) for col in view_df.columns]
        tree["columns"] = columns

        for col in columns:
            tree.heading(col, text=col)
            sample_width = max([len(str(col))] + [len(str(value)) for value in view_df[col].head(20)])
            tree.column(col, width=max(92, min(260, sample_width * 14)), anchor="w")

        for index, (_, row) in enumerate(view_df.iterrows()):
            values = [str(row[col]) for col in view_df.columns]
            tag = "oddrow" if index % 2 else "evenrow"
            tree.insert("", END, values=values, tags=(tag,))

    def _fill_errors(self):
        """显示 OCR 与解析提示。"""
        record_lines = []
        for record in self.file_records:
            member = record.member_name or "未识别成员"
            record_lines.append(
                f"[{record.status}] {record.source.file_name}｜{record.display_kind}｜{member}｜{record.display_result}"
            )
        if self.errors:
            text = "\n".join(self.errors)
            if record_lines:
                text += "\n\n文件处理记录：\n" + "\n".join(record_lines)
        elif self.preview_df is not None and not self.preview_df.empty:
            text = "解析成功，无明显错误。\n\n文件概览：\n" + self.preview_df.to_string(index=False)
            if record_lines:
                text += "\n\n文件处理记录：\n" + "\n".join(record_lines)
        else:
            text = "解析成功，无明显错误。"
            if record_lines:
                text += "\n\n文件处理记录：\n" + "\n".join(record_lines)
        self._set_error_text(text)

    def _set_error_text(self, text: str):
        self.error_box.configure(state="normal")
        self.error_box.delete("1.0", END)
        self.error_box.insert("1.0", text)
        self.error_box.configure(state="disabled")

    def export_excel(self):
        """导出最终 Excel 空课表。"""
        if not self.occupancy or not self.students:
            messagebox.showwarning("无法导出", "请先解析课表。")
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
            data = schedule_app.build_empty_schedule_excel_bytes(
                occupancy=self.occupancy,
                students=self.students,
                weeks=self.weeks,
                calendar_df=self.calendar_df,
                timetable_df=self.timetable_df,
                threshold=0,
                blocks_df=self.blocks_df,
                all_slot_df=self.all_slot_df,
            )
            Path(path).write_bytes(data)
            self.logger.info("Excel 导出成功: %s", path)
            messagebox.showinfo("导出成功", f"已保存：{path}")
        except Exception as exc:
            self.logger.exception("Excel 导出失败: %s", path)
            messagebox.showerror("导出失败", str(exc))

def main():
    app = CourseScheduleDesktop()
    app.mainloop()


if __name__ == "__main__":
    main()
