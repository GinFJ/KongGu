<p align="center">
  <img src="assets/konggu-wordmark.png" alt="Konggu" width="260">
</p>

<h1 align="center">Konggu</h1>

<p align="center">
  本地运行的课表 PDF 解析与空课汇总工具。
</p>

<p align="center">
  <strong>青禾计划系列项目</strong><br>
  成都理工大学牛津布鲁克斯学院青年志愿者协会
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-本地桌面端-0078D4?style=flat-square&logo=windows&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Web%20UI-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="OCR" src="https://img.shields.io/badge/OCR-PaddleOCR-1F6FEB?style=flat-square">
</p>

<p align="center">
  <a href="#项目简介">项目简介</a>
  <span>&middot;</span>
  <a href="#项目归属">项目归属</a>
  <span>&middot;</span>
  <a href="#核心功能">核心功能</a>
  <span>&middot;</span>
  <a href="#快速开始">快速开始</a>
  <span>&middot;</span>
  <a href="#使用方式">使用方式</a>
  <span>&middot;</span>
  <a href="#开发与打包">开发与打包</a>
</p>

<p align="center">
  <img src="assets/konggu-readme-hero.png" alt="Konggu 课表空课汇总预览">
</p>

## 项目简介

Konggu 用于导入成员的中方、英方课表 PDF，解析课程占用时段，并生成成员空课汇总。它适合需要收集多人课表、检查成员空闲时间、导出 Excel 结果的本地工作流。

Konggu 是 **青禾计划系列项目** 的一部分，服务于成都理工大学牛津布鲁克斯学院青年志愿者协会的课表收集、志愿服务排班和成员空闲时间整理场景。

项目提供两个入口：

| 入口 | 适合场景 |
| --- | --- |
| 桌面端 | 日常本机使用，打开后直接上传课表 |
| Web 端 | 希望用浏览器查看上传状态、识别明细和结果表格 |

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

启动桌面端：

```powershell
python desktop_clean.py
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

模型默认保存到 `cache/paddlex/official_models/`。如果已经存在 `dist/Konggu/`，脚本也会同步写入打包目录旁的缓存位置，方便打包程序使用 OCR。

## 项目结构

```text
.
├── app/
│   ├── gui/              # 桌面端界面
│   ├── services/         # PDF 处理、空课预览、Excel 导出等服务
│   └── web/              # FastAPI 本地 Web UI
├── core/                 # 课表解析核心、模型和兼容适配
├── config/               # 节次时间、校历、参考库和 OCR 模型配置
├── tools/                # OCR 模型下载和参考库预缓存脚本
├── tests/                # 单元测试和 Web API 测试
├── desktop_clean.py      # 桌面端启动入口
├── web_launcher.py       # Web 端启动入口
├── Konggu.spec           # PyInstaller 打包配置
└── requirements.txt      # Python 依赖
```

## 配置文件

| 文件 | 说明 |
| --- | --- |
| `config/period_time.json` | 节次与时间段配置 |
| `config/school_calendar.json` | 校历和周次日期配置 |
| `config/reference_library.json` | 本地参考库元数据 |
| `config/ocr_models.json` | OCR 模型名称和下载地址 |

修改配置后，重新启动桌面端或 Web 端即可生效。

## 开发与打包

运行测试：

```powershell
python -m pytest -q
```

打包 Windows 程序：

```powershell
pyinstaller Konggu.spec
```

打包结果生成在 `dist/Konggu/`。如需在打包程序中支持 OCR，建议打包后再次运行 `download_ocr_models.bat`。

本地运行和构建产物包括 `.runtime/`、`cache/`、`build/`、`dist/`、日志和导出的 Excel 文件，不应提交到 Git。

<p align="center">
  <img src="assets/konggu-readme-footer.png" alt="Konggu 课表空课汇总插图">
</p>
