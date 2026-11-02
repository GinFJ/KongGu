# tests 目录说明

本目录保存单元测试、集成测试和脱敏回归用例。真实课表、OCR、真值与审计明细保存在版本库外，仓库内测试使用合成材料或脱敏记录。

本页随0.6.0交付迭代更新（2026-11-02）。解析实现与签名沿用0.5.29／65；原件准确性证据引用[0.5.29验收记录](../docs/迭代验收_0.5.29_v1.0.txt)。本轮全量测试、冻结包、安装器和界面验收见[0.6.0交付记录](../docs/交付验收_0.6.0_v1.0.txt)，不能将打包隔离验证当作新电脑或未见课表准确率验收。

## 按职责查找测试

以下按验证目的导航，不以文件数量表示覆盖程度。参数化用例数量与测试函数数量也不能混用。

| 验证目的 | 主要测试与入口 |
|---|---|
| 身份、输入与模型 | `test_pdf_source_service.py`、`test_pdf_inspection.py`、`test_models.py`、`test_legacy_adapter.py` |
| 中英表格边界、尺度与日期行 | `test_bordered_timetables.py`、`test_bordered_scale_regressions.py`、`test_english_divider_alignment.py`、`test_english_row_edges.py`、`test_visual_cell_scale.py`、`test_layout_invariance.py` |
| 规范告示与真实课程反例 | `test_chinese_visual_evidence.py`、`test_course_cell_filters.py` |
| 同页像素建议与矢量字形资格 | `test_visual_notice_consensus.py`、`test_vector_notice_consensus.py`；包含裁切、遮挡、细小轮廓、原PDF子路径与显示列表复用 |
| PDF解析到占用、来源证据与缓存 | `test_vector_notice_pipeline.py`、`test_notice_consensus_evidence.py`、`test_ocr_text_cache.py` |
| 专项复核、快照与导出门禁 | `test_notice_consensus_workflow.py`、`test_vector_notice_workflow.py`、`test_vector_notice_review_boundary.py`；附着细笔画反例用于记录分类器边界，不表示该差异已被识别规则拒绝 |
| 学期／解析签名与任务状态缓存 | `test_workflow_signature_status.py`、`test_job_service_cache.py` |
| 人工修正与结果重算 | `test_review_correction_consistency.py`、`test_review_regressions.py`、`test_desktop_workflow.py`、`test_e2e_workflow_real_core.py` |
| 导出、隐私、离线资源与桌面协议 | `test_export_excel_service.py`、`test_export_privacy.py`、`test_privacy_guard.py`、`test_offline_resources.py`、`test_sidecar.py`、`test_sidecar_guards.py`及`test_frontend_*.py` |
| 普通用户路径与候选交付 | `test_fresh_machine_resources.py`、`test_worker_packaging.py`、`test_worker_isolation_harness.py`、`test_local_bundle_tools.py`；真实冻结包另由`tools/verify_worker_isolation.py`驱动 |
| 双击诊断与进程退出 | `test_portable_diagnostic.py`；包含真实PowerShell/C#子进程、共享超时、无JSON和具体错误保留 |

解析变体审计使用[审计工具](../tools/audit_layout_invariance.py)，条件、复跑方法与限制见[复用性验证](../docs/复用性验证.md)。来源证据和复核动作见[状态与质量门禁](../docs/状态与质量门禁.md)。

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

历史登记中的70份PDF、35组中英配对属于早期样本库及旁路检查口径，见[回归样本登记表](回归样本登记表.md)，不能用其历史状态说明最新审计。

2026-11-02的0.5.29验收按74份原PDF、37个已确认身份的成员组执行。指定第10周（2026-11-02至11-08）中，工作日11-02至11-06的四个节次组共740项“成员×目标时段”与用户指定的修订真值v1.1一致。该真值范围不覆盖周末或全学期，也不代表全部课名、OCR或未见版式已验证。127项受控解析变体与附加检查衡量占用一致性，不是识别准确率；该轮未新跑OCR。完整结果与未完成事项以[对应验收记录](../docs/迭代验收_0.5.29_v1.0.txt)为准。

### 4. 构建与发布验证

从项目根目录按改动范围选择检查；列出命令不表示本次已执行：

- Python 测试：`.venv\Scripts\python.exe -m pytest -q`
- 前端构建：`npm.cmd run build:frontend`
- 桌面层和命令协议检查：`cargo check --manifest-path src-tauri\Cargo.toml --locked --offline`
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
