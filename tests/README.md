# tests 目录说明

本目录保存单元测试、集成测试和脱敏回归用例。真实课表、OCR、真值与审计明细保存在版本库外，仓库内测试使用合成材料或脱敏记录。

截至 2026-11-15，源码标识为 0.7.0（在研），解析签名 69。最近完整验收记录为 [0.6.9](../docs/交付验收_0.6.9_v1.0.md)，其 671 项 Python 与 3 项 Rust 结果不代表后续工作区已通过。界面相关 65 项回归、TypeScript、生产构建及两尺寸浏览器检查通过，范围和限制见[界面验证](../docs/design/ui-0.7.0/README.md)；春季日期、告示及表尾回归另行更新，不把局部检查当作整版验收。

0.6.6 的课程格完整性、坏日期恢复、日期覆盖及导出范围回归分别见 `test_english_cell_completeness.py`、`test_layout_invariance.py`、`test_timetable_coverage.py`。对应历史输入及交付状态见[0.6.6 交付记录](../docs/交付验收_0.6.6_v1.0.md)；下方 0.6.0 数字也属于历史记录。

PDF 原文预览的离线映射和字体文件完整性见 `test_pdf_preview_resources.py`；中文 PDF 的实际像素渲染回归及原生窗口验收单独记录在交付文档，不用静态源码断言代替显示结果。

以下 0.6.0 历史记录对应 2026-11-02：当时解析实现与签名沿用 0.5.29／65，原件准确性证据引用 [0.5.29 验收记录](../docs/迭代验收_0.5.29_v1.0.txt)。该版本全量测试、冻结包、安装器和界面验收见 [0.6.0 交付记录](../docs/交付验收_0.6.0_v1.0.txt)，不能将打包隔离验证当作新电脑或未见课表准确率验收。

0.7.0 识别任务补充：`pytest tests -q` 681 项通过；新增 `test_english_split_date.py`、`test_ocr_notice_refinement.py`、`test_chinese_grid_footer.py` 验证通用边界及拒绝条件。真实独立批次与安装进度见[0.7.0 验收](../docs/交付验收_0.7.0_v1.0.md)。

## 按职责查找测试

以下按验证目的导航，不以文件数量表示覆盖程度。参数化用例数量与测试函数数量也不能混用。

| 验证目的 | 主要测试与入口 |
|---|---|
| 空课周表、界面分组与状态 | `test_availability_week_view.py`、`test_frontend_user_language.py`、`test_frontend_semester_recovery.py`；真实浏览器专项记录见上方界面验证 |
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
| 英方网格、午间与浮层 | `test_english_cell_completeness.py`、`test_english_midday_axis.py`、`test_english_popup_underlay.py` |
| 日期范围与复核复用 | `test_timetable_coverage.py`、`test_review_evidence_reuse.py`；旧确认失效与完成状态顺序 |
| 春季跨页、告示与表尾 | `test_english_split_date.py`、`test_ocr_notice_refinement.py`、`test_chinese_grid_footer.py`；真实样本结论另行记录 |
| 空课周表展示 | `test_availability_week_view.py`；缺失格、重复记录、节次时间与名单转义，不验证解析准确率 |

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

0.6.9 新增 `test_english_midday_axis.py`、`test_english_popup_underlay.py` 及分页日期回归：覆盖午间单/双节、缩放与留白、畸变拒绝、文字层与图片路径、真实底层课程和整页图片拒绝、页脚网址日期排除、带标点星期及跨页孤立日期。真实原件与 AI 标注在版本库外验证；测试数和版式变体数不代表识别准确率。

每次关键验证记录：

- 日期；
- 分支和提交；
- 执行命令；
- 环境；
- 通过、失败和跳过项；
- 失败原因；
- 对交付的影响。

未运行的验证必须标记为“未验证”，不得默认视为通过。
