from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_CSV = WORKSPACE / "export" / "网盘迁移清单.csv"
DEFAULT_REPORT = WORKSPACE / "export" / "网盘本地整理预演.json"

TARGET_DIRS = [
    "001_待整理/001_来源不明与重复项",
    "001_待整理/002_系统软件缓存待确认",
    "001_待整理/003_临时下载与碎片文件",
    "100_生活/101_个人技能成长",
    "100_生活/102_日常生活记录",
    "100_生活/103_健康与医疗管理",
    "100_生活/104_生活办事台账",
    "200_学业/201_本科课程资料",
    "200_学业/202_升学深造规划",
    "200_学业/203_竞赛项目",
    "200_学业/204_科研论文与课题",
    "200_学业/205_学生组织与社会实践",
    "300_职业与成长规划/301_实习实践经历",
    "300_职业与成长规划/302_证书与荣誉资质",
    "300_职业与成长规划/303_求职就业准备",
    "400_重要凭证与存档/401_身份与法律凭证",
    "400_重要凭证与存档/402_财务与资产相关",
    "400_重要凭证与存档/403_合同与协议归档",
    "500_数字资产与素材库/501_原创作品库",
    "500_数字资产与素材库/502_账号与密钥管理",
    "500_数字资产与素材库/503_通用素材库",
    "500_数字资产与素材库/504_代码与开发项目",
    "500_数字资产与素材库/505_软件游戏资源待确认",
]


@dataclass
class Operation:
    operation: str
    source: str
    target: str
    status: str
    reason: str


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def fallback_target(name: str, is_dir: bool) -> tuple[str, str, str]:
    lower = name.lower()
    if contains_any(name, ["PycharmProject", "源代码", "code", "frontend", "dataset"]) or contains_any(
        lower, ["pycharm", "project", "src"]
    ):
        return "500_数字资产与素材库/504_代码与开发项目", "建议迁移", "本地项目/源码目录 fallback 规则"
    if contains_any(name, ["青志协", "志愿", "社会实践", "三下乡", "牛布"]):
        return "200_学业/205_学生组织与社会实践", "建议迁移", "学生组织或社会实践 fallback 规则"
    if contains_any(name, ["证书", "荣誉", "任职证明", "综合评测"]):
        return "300_职业与成长规划/302_证书与荣誉资质", "建议迁移", "证书荣誉 fallback 规则"
    if contains_any(name, ["壁纸", "模板", "模版", "照片", "视频", "录音", "素材"]):
        return "500_数字资产与素材库/503_通用素材库", "建议迁移", "素材类 fallback 规则"
    if not is_dir and Path(name).suffix.lower() in {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt"}:
        return "001_待整理/001_来源不明与重复项", "建议暂存", "文档文件未命中清单，先暂存等待人工确认"
    return "001_待整理/001_来源不明与重复项", "建议暂存", "未命中清单或 fallback 规则"


def load_mapping(csv_path: Path) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    if not csv_path.exists():
        return mapping
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            mapping[row["source_path"].replace("/", "\\")] = row
            mapping[Path(row["source_path"]).name] = row
    return mapping


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}__dup{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def should_move(action: str, confidence: str, include_pending: bool, include_low_confidence: bool) -> bool:
    if action == "标记待确认":
        return False
    if action == "建议暂存" and not include_pending:
        return False
    if confidence == "low" and not include_low_confidence:
        return False
    return True


def build_operations(
    source_roots: list[Path],
    target_root: Path,
    mapping: dict[str, dict[str, str]],
    include_pending: bool,
    include_low_confidence: bool,
) -> list[Operation]:
    operations: list[Operation] = []

    for target_dir in TARGET_DIRS:
        operations.append(
            Operation(
                operation="mkdir",
                source="",
                target=str(target_root / Path(target_dir)),
                status="planned",
                reason="目标目录结构",
            )
        )

    for root in source_roots:
        if not root.exists():
            operations.append(
                Operation(
                    operation="scan",
                    source=str(root),
                    target="",
                    status="missing",
                    reason="来源目录不存在",
                )
            )
            continue

        for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if item.name in {".cache", "desktop.ini"}:
                operations.append(
                    Operation(
                        operation="skip",
                        source=str(item),
                        target="",
                        status="skipped",
                        reason="同步盘系统文件或缓存入口",
                    )
                )
                continue

            row = mapping.get(item.name)
            if row:
                target_path = row["target_path"]
                action = row["action"]
                confidence = row["confidence"]
                reason = row["reason"]
            else:
                target_path, action, reason = fallback_target(item.name, item.is_dir())
                confidence = "medium" if action == "建议迁移" else "low"

            if not should_move(action, confidence, include_pending, include_low_confidence):
                operations.append(
                    Operation(
                        operation="skip",
                        source=str(item),
                        target=str(target_root / Path(target_path) / item.name),
                        status="skipped",
                        reason=f"{action}; confidence={confidence}; {reason}",
                    )
                )
                continue

            destination = unique_destination(target_root / Path(target_path) / item.name)
            operations.append(
                Operation(
                    operation="move",
                    source=str(item),
                    target=str(destination),
                    status="planned",
                    reason=f"{action}; confidence={confidence}; {reason}",
                )
            )

    return operations


def apply_operations(operations: list[Operation], execute: bool) -> None:
    if not execute:
        return

    for operation in operations:
        if operation.operation == "mkdir":
            Path(operation.target).mkdir(parents=True, exist_ok=True)
            operation.status = "done"
        elif operation.operation == "move":
            source = Path(operation.source)
            target = Path(operation.target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not source.exists():
                operation.status = "missing"
                operation.reason += "; 执行时来源不存在"
                continue
            final_target = unique_destination(target)
            shutil.move(str(source), str(final_target))
            operation.target = str(final_target)
            operation.status = "done"


def write_report(operations: list[Operation], report_path: Path, execute: bool) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "execute" if execute else "dry-run",
        "summary": {
            "mkdir": sum(1 for op in operations if op.operation == "mkdir"),
            "move": sum(1 for op in operations if op.operation == "move"),
            "skip": sum(1 for op in operations if op.operation == "skip"),
            "scan_missing": sum(1 for op in operations if op.operation == "scan" and op.status == "missing"),
        },
        "operations": [asdict(operation) for operation in operations],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the Baidu Netdisk catalog plan to local synced/downloaded folders.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--target-root", type=Path, default=Path(r"D:\BaiduSyncdisk"))
    parser.add_argument(
        "--source-root",
        type=Path,
        action="append",
        default=[Path(r"D:\BaiduSyncdisk"), Path(r"D:\BaiduNetdiskDownload")],
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--include-pending", action="store_true", help="Also move 建议暂存 items into 001_待整理.")
    parser.add_argument("--include-low-confidence", action="store_true", help="Also move low-confidence items.")
    parser.add_argument("--execute", action="store_true", help="Actually create directories and move files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mapping = load_mapping(args.csv)
    operations = build_operations(
        source_roots=args.source_root,
        target_root=args.target_root,
        mapping=mapping,
        include_pending=args.include_pending,
        include_low_confidence=args.include_low_confidence,
    )
    apply_operations(operations, args.execute)
    write_report(operations, args.report, args.execute)
    print(f"mode={'execute' if args.execute else 'dry-run'}")
    print(f"report={args.report}")
    print(f"mkdir={sum(1 for op in operations if op.operation == 'mkdir')}")
    print(f"move={sum(1 for op in operations if op.operation == 'move')}")
    print(f"skip={sum(1 for op in operations if op.operation == 'skip')}")
    print(f"scan_missing={sum(1 for op in operations if op.operation == 'scan' and op.status == 'missing')}")


if __name__ == "__main__":
    main()
