from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(
    r"C:\Users\14577\Documents\BaiduNetdiskTmp\baiduyunguanjia\onlinedit\cache\aecd7faadb489290070166b519db2d20"
)
EXPORT_DIR = WORKSPACE / "export"

SOURCE_GLOB = "001_*.txt"

TREE_MARKERS = ("├──", "└──")
INDENT_TOKENS = ("│   ", "    ")

TARGET_TREE = {
    "001_待整理": [
        "001_来源不明与重复项",
        "002_系统软件缓存待确认",
        "003_临时下载与碎片文件",
    ],
    "100_生活": [
        "101_个人技能成长",
        "102_日常生活记录",
        "103_健康与医疗管理",
        "104_生活办事台账",
    ],
    "200_学业": [
        "201_本科课程资料",
        "202_升学深造规划",
        "203_竞赛项目",
        "204_科研论文与课题",
        "205_学生组织与社会实践",
    ],
    "300_职业与成长规划": [
        "301_实习实践经历",
        "302_证书与荣誉资质",
        "303_求职就业准备",
    ],
    "400_重要凭证与存档": [
        "401_身份与法律凭证",
        "402_财务与资产相关",
        "403_合同与协议归档",
    ],
    "500_数字资产与素材库": [
        "501_原创作品库",
        "502_账号与密钥管理",
        "503_通用素材库",
        "504_代码与开发项目",
        "505_软件游戏资源待确认",
    ],
}

TARGET_DESCRIPTIONS = {
    "001_待整理/001_来源不明与重复项": "来源、用途或归属不清的目录，先保留原结构等待人工确认。",
    "001_待整理/002_系统软件缓存待确认": "系统、软件、依赖、游戏库和缓存类目录，只标记，不纳入正式归档。",
    "001_待整理/003_临时下载与碎片文件": "临时下载、缓存、桌面残留和待二次筛选的碎片内容。",
    "100_生活/101_个人技能成长": "语言、软技能、兴趣成长类课程和资料。",
    "100_生活/102_日常生活记录": "聊天备份、生活照片、日常事件记录等。",
    "100_生活/103_健康与医疗管理": "体检、就诊、疫苗、健康饮食等记录。",
    "100_生活/104_生活办事台账": "住宿、水电物业、生活服务办理凭证。",
    "200_学业/201_本科课程资料": "本科课程、课堂笔记、作业、考试、课程视频和教材资料。",
    "200_学业/202_升学深造规划": "考研、目标院校、申请材料和学习计划。",
    "200_学业/203_竞赛项目": "蓝桥杯、计算机设计大赛等竞赛项目资料。",
    "200_学业/204_科研论文与课题": "SCI、论文、科研、专利、文献和课题材料。",
    "200_学业/205_学生组织与社会实践": "志愿服务、社会实践、三下乡、学生组织和青志协资料。",
    "300_职业与成长规划/301_实习实践经历": "实习公司、岗位经历和实践证明材料。",
    "300_职业与成长规划/302_证书与荣誉资质": "证书、荣誉、奖项、任职证明和综合测评材料。",
    "300_职业与成长规划/303_求职就业准备": "简历、求职、面试、就业准备资料。",
    "400_重要凭证与存档/401_身份与法律凭证": "身份证、户口本、护照、公证、授权委托等重要凭证。",
    "400_重要凭证与存档/402_财务与资产相关": "银行卡、社保公积金、保险、收支、账单和缴费记录。",
    "400_重要凭证与存档/403_合同与协议归档": "合同、协议、租赁和履约相关凭证。",
    "500_数字资产与素材库/501_原创作品库": "写作、设计、摄影、剪辑、达芬奇工程和可复用原创作品。",
    "500_数字资产与素材库/502_账号与密钥管理": "账号清单、软件密钥、授权信息和数字证书备份。",
    "500_数字资产与素材库/503_通用素材库": "壁纸、PPT模板、图片、视频、音频和通用素材。",
    "500_数字资产与素材库/504_代码与开发项目": "源码、数据集、模型、前后端项目和开发配置。",
    "500_数字资产与素材库/505_软件游戏资源待确认": "软件安装包、游戏资源、资源站和可疑大型资源目录。",
}


@dataclass(frozen=True)
class Entry:
    line_no: int
    level: int
    name: str
    path: tuple[str, ...]


def find_source_file() -> Path:
    matches = sorted(SOURCE_DIR.glob(SOURCE_GLOB))
    if not matches:
        raise FileNotFoundError(f"No source file matched {SOURCE_DIR / SOURCE_GLOB}")
    return matches[0]


def parse_tree_line(line_no: int, line: str, stack: list[str]) -> Entry | None:
    marker_index = -1
    for marker in TREE_MARKERS:
        marker_index = line.find(marker)
        if marker_index >= 0:
            break
    if marker_index < 0:
        return None

    prefix = line[:marker_index]
    level = sum(prefix.count(token) for token in INDENT_TOKENS)
    name = line[marker_index + 3 :].strip()

    if len(stack) <= level:
        stack.extend([""] * (level + 1 - len(stack)))
    stack[level] = name
    del stack[level + 1 :]

    return Entry(line_no=line_no, level=level, name=name, path=tuple(stack))


def parse_source(source_file: Path) -> tuple[list[str], list[Entry]]:
    lines = source_file.read_text(encoding="utf-8").splitlines()
    stack: list[str] = []
    entries: list[Entry] = []
    for line_no, line in enumerate(lines, start=1):
        entry = parse_tree_line(line_no, line, stack)
        if entry is not None:
            entries.append(entry)
    return lines, entries


def looks_like_file(name: str) -> bool:
    if re.fullmatch(r"\d+(?:\.\d+)+", name):
        return False
    suffix = Path(name).suffix
    return bool(suffix) and 1 < len(suffix) <= 9


def build_directory_stats(entries: list[Entry]) -> tuple[set[tuple[str, ...]], dict[tuple[str, ...], dict[str, int]], dict[tuple[str, ...], int]]:
    dir_paths: set[tuple[str, ...]] = set()
    first_seen: dict[tuple[str, ...], int] = {}

    for entry in entries:
        first_seen.setdefault(entry.path, entry.line_no)
        for depth in range(1, len(entry.path)):
            dir_paths.add(entry.path[:depth])

    stats: dict[tuple[str, ...], dict[str, int]] = defaultdict(
        lambda: {"entries": 0, "likely_files": 0, "likely_dirs": 0}
    )
    for entry in entries:
        is_dir = entry.path in dir_paths
        for depth in range(1, len(entry.path)):
            parent = entry.path[:depth]
            stats[parent]["entries"] += 1
            if is_dir:
                stats[parent]["likely_dirs"] += 1
            elif looks_like_file(entry.name):
                stats[parent]["likely_files"] += 1
            else:
                stats[parent]["likely_dirs"] += 1

    return dir_paths, stats, first_seen


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def is_protected_path(path: tuple[str, ...]) -> bool:
    lower_source = "/".join(path).lower()
    lower_segments = [segment.lower() for segment in path]
    protected_exact = {
        ".venv",
        "node_modules",
        ".git",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".ipynb_checkpoints",
    }
    protected_keywords = [
        "steamlibrary",
        "program files (x86)",
        "windows kits",
        "programdata",
    ]
    return any(segment in protected_exact for segment in lower_segments) or contains_any(lower_source, protected_keywords)


def is_protected_anchor(path: tuple[str, ...]) -> bool:
    last = path[-1].lower()
    protected_exact = {
        ".venv",
        "node_modules",
        ".git",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".ipynb_checkpoints",
    }
    protected_names = {
        "steamlibrary",
        "program files (x86)",
        "windows kits",
        "programdata",
    }
    return last in protected_exact or last in protected_names


def classify(path: tuple[str, ...]) -> tuple[str, str, str, str]:
    source = "/".join(path)

    if is_protected_path(path):
        return (
            "001_待整理/002_系统软件缓存待确认",
            "标记待确认",
            "high",
            "系统、依赖、软件库或游戏库目录，按保护规则不纳入正式归档。",
        )

    if any(segment in {"Temp", "Download"} for segment in path) or contains_any(source, ["BaiduNetdiskTmp", "chrome_drag", "临时", "缓存"]):
        return (
            "001_待整理/003_临时下载与碎片文件",
            "建议暂存",
            "high",
            "临时下载、缓存或碎片目录，需要二次筛选。",
        )

    if any(segment.startswith("来自：") for segment in path) or any(
        segment in {"Desktop", "Users", "111", "fgyhm"} for segment in path
    ):
        return (
            "001_待整理/001_来源不明与重复项",
            "建议暂存",
            "medium",
            "来源型、桌面型或用户目录导入内容，先保留原结构等待确认。",
        )

    if contains_any(source, ["社会实践", "三下乡", "志愿", "返家乡", "青志协", "牛布青志协", "校旗传递", "学生组织"]):
        return (
            "200_学业/205_学生组织与社会实践",
            "建议迁移",
            "high",
            "学生组织、志愿服务或社会实践资料。",
        )

    if contains_any(source, ["合同", "协议", "租赁", "履约"]):
        return (
            "400_重要凭证与存档/403_合同与协议归档",
            "建议迁移",
            "high",
            "合同或协议类凭证。",
        )

    if contains_any(source, ["银行", "保险", "社保", "公积金", "资产", "财务", "收支", "账单", "发票", "缴费"]):
        return (
            "400_重要凭证与存档/402_财务与资产相关",
            "建议迁移",
            "high",
            "财务、保险、社保或资产相关记录。",
        )

    if contains_any(source, ["身份证", "户口", "护照", "公证", "授权", "法律"]):
        return (
            "400_重要凭证与存档/401_身份与法律凭证",
            "建议迁移",
            "high",
            "身份、法律或授权凭证。",
        )

    if contains_any(source, ["简历", "求职", "面试", "就业准备"]):
        return (
            "300_职业与成长规划/303_求职就业准备",
            "建议迁移",
            "high",
            "求职就业准备资料。",
        )

    if contains_any(source, ["实习", "岗位", "实践经历"]):
        return (
            "300_职业与成长规划/301_实习实践经历",
            "建议迁移",
            "medium",
            "实习或岗位实践相关资料。",
        )

    if contains_any(source, ["证书", "荣誉", "获奖", "奖学金", "任职证明", "综合评测", "资格", "评优"]):
        return (
            "300_职业与成长规划/302_证书与荣誉资质",
            "建议迁移",
            "high",
            "证书、荣誉、任职证明或综合测评资料。",
        )

    if contains_any(source, ["蓝桥杯", "BISAOI", "Competition", "竞赛", "大赛", "比赛"]):
        return (
            "200_学业/203_竞赛项目",
            "建议迁移",
            "high",
            "竞赛项目资料。",
        )

    if contains_any(source, ["SCI", "论文", "科研", "课题", "专利", "文献"]):
        return (
            "200_学业/204_科研论文与课题",
            "建议迁移",
            "high",
            "科研、论文、SCI、专利或文献资料。",
        )

    if contains_any(source, ["考研", "升学", "目标院校", "申请材料", "学习计划"]):
        return (
            "200_学业/202_升学深造规划",
            "建议迁移",
            "high",
            "升学深造规划资料。",
        )

    if contains_any(
        source,
        [
            "数学",
            "OOP",
            "Course",
            "高数",
            "Student handbook",
            "英方资料",
            "课程",
            "Lecture",
            "Quiz",
            "Seminar",
            "Practical",
            "作业",
            "考试",
            "本科",
            "Java48",
            "Day",
            "MicroService",
            "Linux",
            "SQL",
            "JDBC",
            "Tomcat",
            "Maven",
        ],
    ):
        return (
            "200_学业/201_本科课程资料",
            "建议迁移",
            "high",
            "课程、课堂、作业、考试或教材资料。",
        )

    if contains_any(source, ["英语", "情商", "skill upgrating", "语言", "个人技能", "兴趣"]):
        return (
            "100_生活/101_个人技能成长",
            "建议迁移",
            "high",
            "语言、软技能或个人兴趣成长资料。",
        )

    if contains_any(source, ["健康", "医疗", "医院", "体检", "疫苗", "健身", "饮食"]):
        return (
            "100_生活/103_健康与医疗管理",
            "建议迁移",
            "high",
            "健康或医疗管理资料。",
        )

    if contains_any(source, ["水电", "物业", "住房", "生活服务"]):
        return (
            "100_生活/104_生活办事台账",
            "建议迁移",
            "high",
            "生活办事、住宿或缴费记录。",
        )

    if contains_any(source, ["WeChat Files", "微信备份", "晋中生活", "日常生活"]):
        return (
            "100_生活/102_日常生活记录",
            "建议迁移",
            "medium",
            "聊天备份或日常生活记录。",
        )

    if contains_any(source, ["源代码", "PycharmProject", "code", "frontend", "dataset", "Medical_dataset", "local_model", "configs", "install_logs", "attention_outputs", "src", "人工智能应用", "见影知医"]):
        return (
            "500_数字资产与素材库/504_代码与开发项目",
            "建议迁移",
            "high",
            "源码、模型、数据集或开发项目资料。",
        )

    if contains_any(source, ["账号", "密钥", "key", "license", "License", "授权信息", "数字证书"]):
        return (
            "500_数字资产与素材库/502_账号与密钥管理",
            "建议迁移",
            "medium",
            "账号、密钥、授权或数字证书资料。",
        )

    if contains_any(source, ["作品", "剪辑", "DaVinCi", "Resolve Project Library", "FFOutput", "原创", "摄影", "设计", "排版"]):
        return (
            "500_数字资产与素材库/501_原创作品库",
            "建议迁移",
            "high",
            "原创作品、剪辑工程或设计项目。",
        )

    if contains_any(source, ["壁纸", "PPT模版", "多媒体资料", "sif照片", "图片", "视频", "录音", "照片", "素材", "模板", "img"]):
        return (
            "500_数字资产与素材库/503_通用素材库",
            "建议迁移",
            "high",
            "图片、视频、录音、PPT模板或通用素材。",
        )

    if contains_any(
        source,
        ["Stardew", "Kingdom.Come", "MCLDownload", "Ksoftware", "Kugou", "Visio", "PsTool", "olfy", "P社", "游戏", "软件", "5.1.0"],
    ):
        return (
            "500_数字资产与素材库/505_软件游戏资源待确认",
            "建议暂存",
            "medium",
            "软件、游戏或资源站资源，需确认是否保留。",
        )

    return (
        "001_待整理/001_来源不明与重复项",
        "建议暂存",
        "low",
        "未命中明确规则，低置信度保留等待人工确认。",
    )


def is_key_directory(path: tuple[str, ...], stats: dict[tuple[str, ...], dict[str, int]]) -> bool:
    if is_protected_anchor(path):
        return True
    if len(path) <= 2:
        return True
    if len(path) == 3 and path[:2] in {
        ("整理中", "D盘"),
        ("整理中", "蓝桥杯"),
        ("整理中", "多媒体资料"),
    }:
        return True
    return len(path) == 3 and stats[path]["entries"] >= 20


def build_rows(
    dir_paths: set[tuple[str, ...]],
    stats: dict[tuple[str, ...], dict[str, int]],
    first_seen: dict[tuple[str, ...], int],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(dir_paths, key=lambda item: (first_seen.get(item, 10**9), len(item), item)):
        if not is_key_directory(path, stats):
            continue
        target, action, confidence, reason = classify(path)
        notes = (
            f"descendant_entries={stats[path]['entries']}; "
            f"likely_files={stats[path]['likely_files']}; "
            f"likely_dirs={stats[path]['likely_dirs']}"
        )
        rows.append(
            {
                "source_path": "/".join(path),
                "target_path": target,
                "action": action,
                "confidence": confidence,
                "reason": reason,
                "notes": notes,
            }
        )
    return rows


def format_target_tree() -> str:
    lines: list[str] = []
    for top, children in TARGET_TREE.items():
        lines.append(top)
        for child in children:
            lines.append(f"  {child}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_markdown(source_file: Path, total_lines: int, parsed_entries: int, rows: list[dict[str, str]]) -> str:
    samples: dict[str, list[str]] = defaultdict(list)
    confidence_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        confidence_counts[row["confidence"]] += 1
        bucket = row["target_path"]
        if len(samples[bucket]) < 8:
            samples[bucket].append(row["source_path"])

    lines: list[str] = [
        "# 网盘新目录结构",
        "",
        "## 来源与范围",
        "",
        f"- 来源清单：`{source_file}`",
        f"- 原始行数：{total_lines}",
        f"- 解析到的目录树条目：{parsed_entries}",
        f"- 本次迁移清单行数：{len(rows)}",
        "- 本轮只生成目录方案和迁移清单，不移动、不删除、不重命名真实文件。",
        "",
        "## 目标目录树",
        "",
        "```text",
        format_target_tree(),
        "```",
        "",
        "## 分类说明与代表性原目录",
        "",
        "| 目标目录 | 分类说明 | 代表性原目录 |",
        "| --- | --- | --- |",
    ]

    for top, children in TARGET_TREE.items():
        for child in children:
            target = f"{top}/{child}"
            sample_text = "<br>".join(samples.get(target, ["暂无直接命中"]))
            lines.append(f"| `{target}` | {TARGET_DESCRIPTIONS[target]} | {sample_text} |")

    lines.extend(
        [
            "",
            "## 迁移清单使用方式",
            "",
            "- `action=建议迁移`：适合后续进入真实整理前的 dry-run。",
            "- `action=建议暂存`：建议先放入 `001_待整理`，人工确认后再细分。",
            "- `action=标记待确认`：系统、依赖、软件库、游戏库等保护目录，不建议直接归档或拆分。",
            "- `confidence=low` 的项目不要自动移动，应先人工核对。",
            "",
            "## 本轮统计",
            "",
            f"- high：{confidence_counts.get('high', 0)}",
            f"- medium：{confidence_counts.get('medium', 0)}",
            f"- low：{confidence_counts.get('low', 0)}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(source_file: Path, total_lines: int, entries: list[Entry], rows: list[dict[str, str]]) -> tuple[Path, Path]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    markdown_path = EXPORT_DIR / "网盘新目录结构.md"
    csv_path = EXPORT_DIR / "网盘迁移清单.csv"

    markdown_path.write_text(
        build_markdown(source_file, total_lines, len(entries), rows),
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["source_path", "target_path", "action", "confidence", "reason", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)

    return markdown_path, csv_path


def validate(total_lines: int, rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    if total_lines != 100001:
        errors.append(f"Expected 100001 source lines, got {total_lines}")

    top_level = {row["source_path"].split("/", 1)[0] for row in rows}
    required_top_level = {
        "源代码",
        "英语",
        "人工智能应用 见影知医 刘金富 18200458853",
        "数学",
        "网络安全密钥SCI",
        "来自：23116PN5BC",
        "峰video18堂高阶情商博弈课",
        "壁纸",
        "PPT模版",
        "OOP",
        "5.1.0",
        "整理中",
    }
    missing_top = sorted(required_top_level - top_level)
    if missing_top:
        errors.append("Missing top-level rows: " + ", ".join(missing_top))

    protected_expectations = {
        ".venv",
        "node_modules",
        ".git",
        ".cache",
        "SteamLibrary",
        "Program Files (x86)",
        "Windows Kits",
        "ProgramData",
    }
    for protected in protected_expectations:
        matches = [row for row in rows if protected in row["source_path"]]
        if matches and any(row["target_path"] != "001_待整理/002_系统软件缓存待确认" for row in matches):
            errors.append(f"Protected directory misclassified: {protected}")

    if not all(row["target_path"] for row in rows):
        errors.append("At least one row has an empty target_path")

    return errors


def main() -> None:
    source_file = find_source_file()
    lines, entries = parse_source(source_file)
    dir_paths, stats, first_seen = build_directory_stats(entries)
    rows = build_rows(dir_paths, stats, first_seen)
    markdown_path, csv_path = write_outputs(source_file, len(lines), entries, rows)
    errors = validate(len(lines), rows)

    print(f"source_lines={len(lines)}")
    print(f"parsed_entries={len(entries)}")
    print(f"migration_rows={len(rows)}")
    print(f"markdown={markdown_path}")
    print(f"csv={csv_path}")
    if errors:
        print("validation=failed")
        for error in errors:
            print(f"error={error}")
        raise SystemExit(1)
    print("validation=passed")


if __name__ == "__main__":
    main()
