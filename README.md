# Konggu

Konggu 是一个离线 Windows 桌面软件，用于导入成员的中方、英方课表 PDF，解析课程占用时段，生成成员空课汇总，并导出 Excel 结果。它面向青禾计划和协会内部的课表收集、志愿服务排班、成员空闲时间整理场景。

## 当前形态

- 桌面端：Tauri + Vite/TypeScript
- 处理端：Python sidecar
- 解析核心：PyMuPDF、PaddleOCR、pandas、openpyxl
- 运行方式：安装和使用均按离线设计，不启动浏览器，不依赖本地 Web 服务

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 批量导入 PDF | 选择多个成员课表文件 |
| 课表类型识别 | 根据文件名推断中方、英方课表 |
| 成员检查 | 标记缺失课表、识别失败和解析警告 |
| 空课预览 | 按周次、星期、节次汇总可用成员 |
| Excel 导出 | 输出当前 classic 空课表 |
| 离线 OCR | 从本地内置模型加载 PP-OCRv4 mobile 检测和识别模型 |

## 离线资源

发布版安装包应内置：

- `config/`：节次时间、校历、OCR 模型配置、参考库配置
- `assets/`：桌面端和文档展示素材
- `resources/ocr_models/PP-OCRv4_mobile_det`
- `resources/ocr_models/PP-OCRv4_mobile_rec`
- `resources/offline_manifest.json`

运行时会把资源修复到用户可写目录：

```text
%LOCALAPPDATA%\Konggu\resources
%LOCALAPPDATA%\Konggu\ocr_models
%LOCALAPPDATA%\Konggu\cache
%LOCALAPPDATA%\Konggu\logs
```

默认不允许 PaddleOCR 在线下载模型。模型缺失时，软件只会提示或尝试从安装包内置资源修复。

## 开发准备

Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Node/Tauri：

```powershell
npm.cmd install
```

Tauri 最终打包还需要 Rust/Cargo 和 Windows WebView2 环境。

## 发布前准备离线材料

开发者或 CI 在联网环境中执行：

```powershell
npm.cmd run prepare:offline
```

这会把 `config/ocr_models.json` 中声明的 OCR 模型下载到 `resources/ocr_models/`，并生成带 hash 的 `resources/offline_manifest.json`。

开发阶段如果暂时没有 OCR 模型，可以只生成配置和素材 manifest：

```powershell
npm.cmd run prepare:offline:dev
```

## 本地运行

开发桌面端：

```powershell
npm.cmd run dev
```

只测试 Python sidecar：

```powershell
python -m app.sidecar --request-json "{\"command\":\"resources.status\"}"
```

构建安装包：

```powershell
npm.cmd run build
```

构建流程会先打包 Python sidecar，再把它复制为 Tauri 要求的 sidecar 二进制名称。

## 项目结构

```text
app/
  services/              Python 业务 workflow、预览、导出和序列化
  sidecar.py             Tauri 调用的 Python sidecar CLI
  offline_resources.py   离线资源检查、修复、manifest 逻辑
core/                    课表解析核心、模型和兼容适配
config/                  默认配置
resources/               离线 manifest 和发布内置 OCR 模型目录
src/                     Tauri 前端 UI
src-tauri/               Tauri Windows 桌面壳
tools/                   发布前资源准备脚本
tests/                   Python 核心和 sidecar 测试
```

## 测试

```powershell
python -m pytest -q
```

当前 `visual` Excel 导出模式只是预留接口。后续提供可视化表格样式或样表后，再作为新的导出器接入。
