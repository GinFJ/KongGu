# Konggu 空谷

<p align="center">
  <img src="./assets/konggu-wordmark.png" alt="Konggu" width="220" />
</p>

<p align="center">
  面向中英合办学生组织的离线空课生成工具。
  <br />
  批量解析中方、英方课表 PDF，自动合并课程占用时间，生成可直接用于排班的集体空课表。
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.3.0-116c54" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-2563eb" />
  <img alt="Desktop" src="https://img.shields.io/badge/desktop-Tauri-24c8db" />
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-3776ab" />
  <img alt="Tests" src="https://img.shields.io/badge/tests-49%20passed-2f855a" />
</p>

![Konggu README Hero](./assets/konggu-readme-hero.png)

## What Is Konggu?

Konggu（中文名：空谷）是青禾计划系列项目之一，服务中英合办和中外合作办学场景中的课表收集、成员排班、志愿服务和值班协作。

它解决的不是“做一张漂亮课表”，而是把负责人从收课表、看课表、比对节次、统计空闲人员这类重复性高、耗时长、价值低的人力劳动中释放出来。

**核心判断只有一句话：同一成员只有中方课表和英方课表都没有课，才算真正空闲。**

## Table Of Contents

- [Why Konggu](#why-konggu)
- [Features](#features)
- [How It Works](#how-it-works)
- [Input Contract](#input-contract)
- [Output](#output)
- [Core Rules](#core-rules)
- [Quick Start](#quick-start)
- [Development](#development)
- [Architecture](#architecture)
- [Offline Resources](#offline-resources)
- [Testing](#testing)
- [Roadmap](#roadmap)

## Why Konggu?

中英合办学生通常同时拥有中方课表和英方课表。人工排班时，负责人必须逐个成员、逐个周次、逐个节次核对两套课表。只看一张课表会产生误判，中方没课不代表英方没课。

Konggu 把这条工作链路自动化：

```text
收集课表 PDF -> 识别成员和课表类型 -> 解析课程占用 -> 合并中英双课表 -> 反推空闲时间 -> 导出 Excel
```

| Pain | Konggu |
| --- | --- |
| 人工整理耗时高 | 批量解析多名成员课表 |
| 重复劳动价值低 | 自动完成节次比对和名单统计 |
| 中英课表难合并 | 同一成员的中方、英方占用统一计算 |
| 人工统计容易出错 | 缺失、冲突、解析失败都会显性提示 |
| 课表包含个人信息 | 离线 Windows 桌面软件，本地处理 |

## Features

- **Batch PDF import**: 一次选择多名成员的课表 PDF。
- **Schedule type detection**: 根据文件名识别中方、英方课表；无法识别时拒绝解析，避免静默误判。
- **Local PDF parsing**: 优先读取 PDF 文本层，文本质量不足时回退 OCR。
- **Dual schedule merge**: 同一成员的中方、英方课表合并为完整课程占用表。
- **Availability preview**: 按周次、日期、星期、节次展示空闲人数和空闲成员。
- **Member check**: 标记缺中方、缺英方、解析失败、课程冲突等风险。
- **Excel export**: 导出 classic 空课表，用于排班、值班和志愿服务安排。
- **Offline resources**: 内置配置和 PP-OCRv4 mobile OCR 模型，默认不允许在线下载模型。

## How It Works

```text
成员课表 PDF
  -> 文件导入与类型识别
  -> PDF 文本提取 / OCR 识别
  -> 中方、英方课表结构化解析
  -> 成员课程占用时间合并
  -> 空闲时间反推
  -> 成员完整性与异常检查
  -> 空课预览
  -> Excel 导出
  -> 排班 / 活动 / 志愿服务使用
```

数据会从不可直接使用的 PDF 逐步变成可决策的空课结果：

| Stage | Data |
| --- | --- |
| Raw input | 成员提交的中方课表 PDF、英方课表 PDF |
| Recognition | 成员姓名、课表类型、PDF 文本或 OCR 文本 |
| Structured blocks | 成员、周次、星期、节次、课程占用 |
| Merged occupancy | 同一成员的中英双课表占用合集 |
| Availability | 每个时段的空闲人数、空闲成员、有课成员 |
| Export | 可直接用于排班的 Excel 空课表 |

## Input Contract

Konggu 优先保证结果可信，所以输入规则是产品契约，不是建议。

1. 文件必须是 PDF。
2. 文件名必须能看出成员姓名和课表类型。
3. 文件名必须包含 `中方` 或 `英方`。
4. 同一成员应同时提供中方课表和英方课表。
5. 文件名无法识别课表类型时，系统暂停解析，不默认猜测。

Recommended file names:

```text
张三-中方课表.pdf
张三-英方课表.pdf
办公室-李四-中方课表.pdf
办公室-李四-英方课表.pdf
```

Not accepted:

```text
张三.pdf
课表1.pdf
schedule.pdf
办公室成员课表.pdf
```

## Output

解析完成后先看三个结果区，再导出 Excel。

| View | What To Check |
| --- | --- |
| 空课预览 | 哪些周次、日期、星期、节次有多少人空闲，具体是谁 |
| 成员检查 | 每个成员是否同时导入中方和英方课表，是否存在缺失或冲突 |
| 识别明细 | 每个 PDF 是否识别成功，识别为谁、哪类课表、多少课程块 |

导出的 Excel 用于实际排班。导出前建议先处理成员检查中的异常，否则空课表只能作为参考。

## Core Rules

Konggu 把复杂异常压缩成四条底层规则：

| Rule | Decision |
| --- | --- |
| Identity | 一个 PDF 必须能关联到成员，并识别为中方或英方课表 |
| Occupancy | 每个课程块只表达一件事：某人在某周、某天、某节被课程占用 |
| Completeness | 同一成员需要同时具备中方、英方课表；缺失任一类都必须提示 |
| Availability | 某个时段没有课程占用的成员进入空闲名单；存在缺失、冲突或解析失败时显性标记风险 |

这套规则让不同背景的使用者在点击按钮前形成同一理解：Konggu 不替负责人猜，它只整理可证明的空闲时间，并指出不可信的地方。

## Quick Start

### For Users

1. 收集成员课表 PDF。
2. 按输入规则整理文件名。
3. 打开 Konggu。
4. 检查离线材料状态；如提示缺失，点击“修复离线材料”。
5. 选择所有课表 PDF。
6. 确认文件列表中每个文件都能识别出中方或英方。
7. 设置第 1 周周一和导出周数。
8. 点击“开始解析”。
9. 先处理“成员检查”中的异常。
10. 导出 Excel，用于排班。

### For Developers

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm.cmd install
npm.cmd run dev
```

只测试 Python sidecar：

```powershell
python -m app.sidecar --request-json "{\"command\":\"resources.status\"}"
```

## Development

Python:

```powershell
python -m pytest -q
```

Frontend:

```powershell
npm.cmd run build:frontend
```

Prepare offline resources in a networked environment:

```powershell
npm.cmd run prepare:offline
```

If OCR models are temporarily unavailable during development:

```powershell
npm.cmd run prepare:offline:dev
```

Build Windows installer:

```powershell
npm.cmd run build
```

The build packages the Python sidecar first, then copies it to the binary name required by Tauri.

## Architecture

```text
Tauri + Vite/TypeScript UI
  -> Python sidecar command bridge
  -> app/services workflow layer
  -> core/schedule_core.py parser and exporter
  -> local cache / offline OCR / Excel output
```

| Path | Responsibility |
| --- | --- |
| `src/` | Desktop UI, file selection, pre-parse validation, result preview |
| `app/sidecar.py` | Command entry called by Tauri |
| `app/services/` | Workflow orchestration, member checks, preview, export, serialization |
| `core/` | PDF parsing, OCR fallback, course blocks, occupancy table, Excel workbook |
| `config/` | Calendar, period time, OCR model and reference library config |
| `resources/` | Bundled offline manifest and OCR model directories |
| `src-tauri/` | Tauri Windows desktop shell |
| `tests/` | Business rules, workflow, export and resource checks |

## Offline Resources

Release builds should include:

```text
config/
assets/
resources/offline_manifest.json
resources/ocr_models/PP-OCRv4_mobile_det
resources/ocr_models/PP-OCRv4_mobile_rec
```

At runtime, Konggu repairs resources into the user's writable app data directory:

```text
%LOCALAPPDATA%\Konggu\resources
%LOCALAPPDATA%\Konggu\ocr_models
%LOCALAPPDATA%\Konggu\cache
%LOCALAPPDATA%\Konggu\logs
```

By default, PaddleOCR model download is disabled. Missing models should be repaired from bundled resources or placed under the local OCR model directory.

## Testing

Current local verification:

```text
49 passed, 4 warnings
```

Primary command:

```powershell
python -m pytest -q
```

The exception matrix lives in [`docs/exception-test-matrix.md`](./docs/exception-test-matrix.md). Real member PDFs should stay outside the repository and be tested through a sanitized sample library.

## Roadmap

### Keep

- Batch PDF import
- Dual schedule merge
- Member completeness check
- Explicit abnormal state display
- Availability preview
- Classic Excel export
- Offline OCR

### Freeze For Now

- Visual Excel style exporter
- Complex filtering
- Cloud sync
- Member database
- Activity registration
- Attendance statistics

### Reject

- Guessing schedule type when file names are unclear
- Exporting a result that hides parse failures
- Treating logs as a substitute for user-facing warnings

## Name

“空谷”取“空课汇聚成谷”之意。

- “空”对应从大量中方、英方课表中找出真正可用的空闲时间。
- “谷”象征青禾计划中成员时间与协作需求汇聚成形的空间。

Konggu aims to turn scattered schedule PDFs into a clear, trustworthy, reusable group availability view.
