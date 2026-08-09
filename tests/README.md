# tests 目录说明

本目录保存单元测试、集成测试、脱敏真实样本回归配置和发布验证脚本。当前包含 17 个 `test_*.py` 文件与 `conftest.py`（2026-08-09 盘点）。

## 当前测试文件

`test_audit_timetable_archive`、`test_availability_preview_service`、`test_calendar_settings`、`test_config_files`、`test_coordinate_parsers`、`test_desktop_workflow`、`test_export_excel_service`、`test_generate_availability_service`、`test_legacy_adapter`、`test_models`、`test_ocr_handling`、`test_offline_resources`、`test_pdf_inspection`、`test_pdf_source_service`、`test_quality_persistence_and_benchmark`、`test_result_view_service`、`test_sidecar`。

## 测试分层

### 1. 单元测试

覆盖：

- 文件名和身份解析；
- 课表类型判断；
- 文本字段清洗；
- 周次、星期、节次和时间转换；
- 课程块规范化；
- 占用合并；
- 空闲计算；
- 质量状态和导出数据模型。

### 2. 规则与边界测试

覆盖：

- 缺失文件；
- 重复文件；
- 同内容异身份；
- 正文与文件名冲突；
- 无文本层 PDF；
- 文本型、扫描型 PDF 与逐页 OCR 原因；
- PDF Inspector 不可用时的 PyMuPDF 回退；
- PDF 结构检查的大小上限和隐私安全错误；
- 低置信 OCR；
- 跨周、跨节和异常时间；
- 空成员、空结果和不完整配对；
- `accepted / needs_review / blocked` 状态流转。

### 3. 脱敏真实样本回归

要求：

- 样本来源可追溯；
- 已去除真实姓名、路径和完整课程文本；
- 记录内容哈希；
- 记录版式、预期结果和人工真值状态；
- 解析器、配置或 OCR 变化后重新运行；
- 不得只用人工构造样本证明真实 PDF 兼容性。

当前状态：仓库外正式样本库现有 70 份 PDF、35 组完整中英课表；已完成 PDF Inspector 旁路聚合检查，但课程块人工真值仍未建立，登记见 `回归样本登记表.md`。

### 4. 构建与发布验证

根据当前技术栈执行：

- Python 测试：`.venv\Scripts\python.exe -m pytest -q`
- 前端构建：`npm.cmd run build:frontend`
- 桌面层和命令协议检查：`cargo check --manifest-path src-tauri\Cargo.toml --locked`
- 离线资源检查；
- 安装包构建；
- 全新用户目录和断网运行；
- 导入、预览、复核和导出关键流程。

## 结果记录

每次关键验证记录：

- 日期；
- 分支和提交；
- 执行命令；
- 环境；
- 通过、失败和跳过项；
- 失败原因；
- 对交付的影响。

未运行的验证必须标记为“未验证”，不得默认视为通过。
