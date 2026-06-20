<p align="center">
  <img src="assets/konggu-wordmark.png" alt="Konggu" width="260">
</p>

<h1 align="center">Konggu</h1>

<p align="center">
  Local Web tool for timetable PDF parsing and shared availability summaries.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Web%20UI-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="OCR" src="https://img.shields.io/badge/OCR-PaddleOCR-1F6FEB?style=flat-square">
</p>

<p align="center">
  <a href="#zh-cn">中文</a>
  <span>&middot;</span>
  <a href="#english">English</a>
</p>

<p align="center">
  <img src="assets/konggu-readme-hero.png" alt="Konggu timetable availability preview">
</p>

<details open>
<summary><strong>中文</strong></summary>

<a id="zh-cn"></a>

## 项目简介

Konggu 用于导入成员的中方、英方课表 PDF，解析课程占用时段，并生成成员空课汇总。它适合需要收集多人课表、检查成员空闲时间、导出 Excel 结果的本地工作流。

Konggu 是 **青禾计划系列项目** 的一部分，服务于成都理工大学牛津布鲁克斯学院青年志愿者协会的课表收集、志愿服务排班和成员空闲时间整理场景。

项目提供本地 Web 入口，适合在浏览器中查看上传状态、识别明细和结果表格。

## 项目归属

| 项目属性 | 内容 |
| --- | --- |
| 系列 | 青禾计划 |
| 归属组织 | 成都理工大学牛津布鲁克斯学院青年志愿者协会 |
| 项目定位 | 面向协会内部课表整理、空闲时间汇总和志愿服务排班辅助 |

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 批量导入 PDF | 支持一次或分批上传多个成员课表 |
| 课表类型识别 | 根据文件名推断中方、英方课表 |
| 成员检查 | 标记缺失课表、识别失败和解析警告 |
| 空课预览 | 按周次、星期、节次汇总可用成员 |
| Excel 导出 | 将空课结果导出为 `.xlsx` 文件 |
| OCR 辅助 | 文本型 PDF 可直接解析，扫描件可配合 PaddleOCR 模型处理 |

## 处理流程

| 步骤 | 操作 | 输出 |
| --- | --- | --- |
| 1 | 上传课表 PDF | 中方/英方课表文件 |
| 2 | 识别成员与课表类型 | 成员姓名、课表来源 |
| 3 | 解析课程占用 | 周次、星期、节次 |
| 4 | 检查异常结果 | 缺失课表、识别警告 |
| 5 | 生成空课汇总 | 可用时段和成员名单 |
| 6 | 导出结果 | Excel 文件 |

## 快速开始

建议使用 Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

启动本地 Web 端：

```powershell
python web_launcher.py
```

Web 端默认地址：

```text
http://127.0.0.1:8000
```

也可以直接使用 uvicorn：

```powershell
python -m uvicorn app.web.main:app --host 127.0.0.1 --port 8000
```

## 使用方式

推荐文件命名：

```text
张三-中方课表.pdf
张三-英方课表.pdf
```

命名建议：

- 文件名包含成员姓名，便于归并同一成员的多份课表。
- 文件名包含 `中方` 或 `英方`，便于判断课表类型。
- 同一成员的中方、英方课表尽量成对上传。
- 如果缺少其中一侧课表，Konggu 会在成员检查中提示风险。

## OCR 模型

OCR 模型文件不会提交到仓库。需要识别扫描件或图片型 PDF 时，请先下载离线模型：

```powershell
download_ocr_models.bat
```

模型默认保存到 `cache/paddlex/official_models/`，重新启动 Web 服务后即可使用。

## 项目结构

```text
.
├── app/
│   ├── services/         # PDF 处理、空课预览、Excel 导出等服务
│   └── web/              # FastAPI 本地 Web UI
├── core/                 # 课表解析核心、模型和兼容适配
├── config/               # 节次时间、校历、参考库和 OCR 模型配置
├── tools/                # OCR 模型下载和参考库预缓存脚本
├── tests/                # 单元测试和 Web API 测试
├── web_launcher.py       # Web 端启动入口
└── requirements.txt      # Python 依赖
```

## 配置文件

| 文件 | 说明 |
| --- | --- |
| `config/period_time.json` | 节次与时间段配置 |
| `config/school_calendar.json` | 校历和周次日期配置 |
| `config/reference_library.json` | 本地参考库元数据 |
| `config/ocr_models.json` | OCR 模型名称和下载地址 |

修改配置后，重新启动 Web 端即可生效。

## 开发与运行

运行测试：

```powershell
python -m pytest -q
```

本地运行产物包括 `.runtime/`、`cache/`、日志和导出的 Excel 文件，不应提交到 Git。

</details>

<details>
<summary><strong>English</strong></summary>

<a id="english"></a>

## Overview

Konggu imports Chinese-program and English-program timetable PDFs, parses occupied class periods, and generates shared availability summaries. It is designed for local workflows that need to collect many timetables, inspect member availability, and export results to Excel.

Konggu is part of the **Qinghe Plan** project series for the Youth Volunteer Association of the Oxford Brookes College, Chengdu University of Technology.

The project now provides a local Web interface for checking upload status, parsing details, warnings, and result tables in the browser.

## Ownership

| Property | Value |
| --- | --- |
| Series | Qinghe Plan |
| Organization | Youth Volunteer Association, Oxford Brookes College, Chengdu University of Technology |
| Purpose | Internal timetable collection, availability summary, and volunteer scheduling support |

## Core Features

| Feature | Description |
| --- | --- |
| Batch PDF import | Upload multiple member timetable PDFs at once or in batches |
| Timetable type detection | Infer Chinese-program or English-program timetables from filenames |
| Member checks | Flag missing timetables, recognition failures, and parsing warnings |
| Availability preview | Summarize available members by week, weekday, and class period |
| Excel export | Export availability results as `.xlsx` files |
| OCR support | Parse text PDFs directly and use PaddleOCR models for scanned PDFs |

## Processing Flow

| Step | Action | Output |
| --- | --- | --- |
| 1 | Upload timetable PDFs | Chinese-program / English-program timetable files |
| 2 | Identify members and timetable types | Member names and timetable sources |
| 3 | Parse occupied periods | Weeks, weekdays, and class periods |
| 4 | Check abnormal results | Missing timetables and recognition warnings |
| 5 | Generate availability summary | Available time slots and member lists |
| 6 | Export results | Excel file |

## Quick Start

Python 3.12 is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start the local Web app:

```powershell
python web_launcher.py
```

Default Web URL:

```text
http://127.0.0.1:8000
```

You can also run uvicorn directly:

```powershell
python -m uvicorn app.web.main:app --host 127.0.0.1 --port 8000
```

## Usage

Recommended filename format:

```text
Zhang San-Chinese timetable.pdf
Zhang San-English timetable.pdf
```

Filename guidance:

- Include the member name so multiple files can be grouped correctly.
- Include `中方` / `Chinese` or `英方` / `English` to help identify the timetable type.
- Upload Chinese-program and English-program timetables in pairs where possible.
- If one side is missing, Konggu reports the risk in member checks.

## OCR Models

OCR model files are not committed to the repository. To recognize scanned or image-based PDFs, download the offline models first:

```powershell
download_ocr_models.bat
```

Models are saved to `cache/paddlex/official_models/` by default. Restart the Web service after downloading them.

## Project Structure

```text
.
├── app/
│   ├── services/         # PDF processing, availability preview, Excel export, etc.
│   └── web/              # FastAPI local Web UI
├── core/                 # Timetable parsing core, models, and compatibility adapters
├── config/               # Period times, school calendar, reference library, OCR model config
├── tools/                # OCR model download and reference-library pre-cache scripts
├── tests/                # Unit tests and Web API tests
├── web_launcher.py       # Web entry point
└── requirements.txt      # Python dependencies
```

## Configuration

| File | Description |
| --- | --- |
| `config/period_time.json` | Period and time-slot configuration |
| `config/school_calendar.json` | School calendar and week-date mapping |
| `config/reference_library.json` | Local reference-library metadata |
| `config/ocr_models.json` | OCR model names and download URLs |

Restart the Web service after changing configuration files.

## Development

Run tests:

```powershell
python -m pytest -q
```

Local runtime artifacts such as `.runtime/`, `cache/`, logs, and exported Excel files should not be committed.

</details>

<p align="center">
  <img src="assets/konggu-readme-footer.png" alt="Konggu timetable availability illustration">
</p>
