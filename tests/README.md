# tests 目录说明

## 0.14.0 本机历史与缓存

`test_local_history.py` 覆盖跨批次恢复、旧库升级、学期匹配及原件位置过期。`test_local_storage_usage.py` 核对识别缓存大小、文件数和清理范围。`test_local_history_restart.py` 在独立桌面服务进程之间重开两份合成课表，验证清缓存、保存核验、Excel 导出和清除全部记录。可通过 `KONGGU_CLOUD_TEST_WORKER` 指定待验收的 Windows worker。

## 0.13.0 日期批次核对

日期选择和模板文件名见 `batchDates.test.mjs`。服务端对所选日期的逐人确认、单节修改、多来源重叠、并发修订及正式导出条件使用 `test_date_review.py` 验证。跨周、跨月和周末的工作簿内容见 `test_date_range_export.py`，日期草稿和账号备份分别由工作区恢复用例和云批次用例覆盖。[本版验收](../docs/交付验收_0.13.0_v1.0.md)

## 0.12.0 制表人账号

云文档回归覆盖跨数据目录恢复、学期与核验状态。独立服务端测试包含账号隔离和并发版本冲突。桌面到服务端的联合验证记录见[云端批次](../docs/云端批次_0.12.0.md)。

## 0.11.0 续做与原件关联

`test_workspace_session.py` 覆盖草稿、旧版任务恢复、脱机原件的分次关联、哈希校验、处理被中断以及清理期限。`test_course_verification.py` 检查简短核验回执、修订号和完整状态回退。`test_frontend_semester_recovery.py` 和 `test_worker_startup_recovery.py` 覆盖重置及重启入口。五人逐节对照、界面回归和耗时见[本版验收](../docs/交付验收_0.11.0_v1.0.md)。

## 0.10.0 逐项核验与部门导出

在支持直接运行 TypeScript 的 Node.js 环境执行 `node --test tests/reviewRisks.test.mjs tests/departmentView.test.mjs`，检查原件配对、异常分类及按部门显示名单。`test_department_availability.py` 和 `test_department_exports.py` 检查同名成员的归属、周次覆盖、完整工作簿和青禾周表。

`test_course_verification.py` 检查原件与核验凭据。打包流程的 `tools/verify_worker_isolation.py` 使用合成原件检查核验前拒绝导出，逐项确认后再导出 Excel，并另外检查图片解析的质量阻断。

Windows 安装态兼容性自动检查使用 `tools/windows_compatibility/windows-selfcheck.ps1`，在目标电脑的 PowerShell 5.1 直接驱动冻结 worker，不依赖 Python。只使用工具内合成 PDF，不发送 API 请求。它与本目录单元测试分别记录，详见[0.9.0 Windows 验收补充](../docs/Windows跨电脑验收_0.9.0.md)。

## 0.9.0 AI 原页核对

`tests/test_ai_review.py` 覆盖发送同意、遮盖像素、单次预览、原件变化、结构/日期校验、疑似漏课补录重算、导出门禁、缓存、本机额度、失败不改课表、跨任务与旧界面防护、清理和重导入。真实 API 验证只使用不含个人信息的合成页；不把它称为真实课表准确率。最终结果见[验收](../docs/交付验收_0.9.0_v1.0.md)。

2026-11-16：`pytest tests -q` 最终 739 passed，其中 AI 专项 17 项；Rust 3 项、TypeScript、Cargo 检查通过。请明确指定正式 `tests` 目录：仓库忽略的 `tmp` 中存在历史备份测试，裸 `pytest` 会误收集它们，不能作为本版套件结果。

本目录保存单元测试、集成测试和脱敏回归用例。真实课表、OCR、真值与审计明细保存在版本库外，仓库内测试使用合成材料或脱敏记录。

截至 2026-11-15，本地验证版 0.8.0 / 签名 71：Python 722、Rust 3、TypeScript、Vite、Cargo、42 项包检查及本机安装目录新增操作流程通过，见[验收记录](../docs/交付验收_0.8.0_v1.0.md)。

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

0.7.1 / 签名 70 增加 `test_identity_completion.py`、`test_english_split_course.py`、`test_ocr_location_refinement.py`，并扩展告示复识别反例。覆盖配对歧义/冲突、非相邻或错列分页、浅色边框、两次读取分歧、漏字高置信告示及校外地点不得跨单元格推定。该版本完整单测 706 项通过；真实原件与安装结果另见 [0.7.1 验收](../docs/交付验收_0.7.1_v1.0.md)。


0.8.0 新增 `test_review_operations.py`：多节课程逐节导出、补录去重、删除最后课程保留成员、误排除恢复、部分区域撤销、同源重导、身份/区域变化拒绝复用、旧界面版本拒绝、失败事务回滚与坏案例门禁。真实 PDF 和界面回归证据保存在仓库外，受控故障注入与自然识别结果分开报告。
