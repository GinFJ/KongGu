# Konggu 异常样本测试矩阵

这份矩阵用于把协会真实使用中容易暴露的问题拆成可自动化检查的层级。原则是：服务层异常先用 pytest 固化，PDF/OCR/版式异常必须进入样本库做回归。

## 已覆盖或可单元测试覆盖

| 场景 | 风险 | 当前自动化检查 |
| --- | --- | --- |
| 文件名不规范 | 成员姓名推断错误，导致成员检查错配 | `tests/test_legacy_adapter.py::test_infer_member_name_handles_irregular_filename_parts` |
| 文件名缺少中方/英方 | 课表类型被静默误判，污染空课结果 | `tests/test_pdf_source_service.py::test_add_pdf_sources_uses_explicit_kind_and_rejects_unknown_kind`、`tests/test_desktop_workflow.py::test_desktop_workflow_rejects_unknown_schedule_kind` |
| 文件名包含测试占位姓名 | `路人甲` 等测试文件进入正式空课统计 | `tests/test_pdf_source_service.py::test_add_pdf_sources_rejects_placeholder_member_file_name`、`tests/test_audit_timetable_archive.py::test_classify_parse_status_rejects_placeholder_filename` |
| 重复选择同一 PDF | 重复解析、重复统计 | `tests/test_pdf_source_service.py::test_add_pdf_sources_infers_kind_and_skips_duplicates` |
| 同内容重复 PDF 副本 | 同一课表被重复上传后重复解析 | `tests/test_pdf_source_service.py::test_add_pdf_sources_skips_same_content_duplicate_files` |
| 同内容但文件名成员不同 | 源文件复制错人，导致某成员被错误统计 | `tests/test_pdf_source_service.py::test_add_pdf_sources_reports_same_content_with_different_member_names`、`tests/test_audit_timetable_archive.py::test_parse_inventory_preflight_rejects_duplicate_name_mismatch`、样本库中成员A/成员B错配 |
| 缺中方或缺英方 | 成员完整性误判 | `tests/test_models.py`、`tests/test_result_view_service.py`、`tests/test_generate_availability_service.py` |
| 节次冲突 | 中方/英方或多份课表同一成员同一节次出现不同课程 | `tests/test_legacy_adapter.py::test_build_member_schedules_flags_period_conflicts` |
| 课程跨周次 | 周次展开错误，空课统计偏差 | `tests/test_coordinate_parsers.py` 覆盖坐标解析片段，仍需要样本库扩展 |

## 必须用真实样本库回归

| 场景 | 最小样本要求 | 验收口径 |
| --- | --- | --- |
| PDF 扫描质量差 | 至少 3 份低清/倾斜/阴影扫描 PDF | 能给出明确失败提示；清晰度可接受的样本应能走 OCR |
| 课表截图嵌入 PDF | 至少 2 份无文本层、图片嵌入 PDF | 触发 OCR，不误判为空文件 |
| 英方课表格式变化 | 每种英方版式至少 1 份 | 日期、星期、节次映射正确 |
| 同名成员 | 至少 2 位同名但部门或角色不同的成员 | 当前仅按姓名聚合，需产品层增加唯一标识后再验收 |
| 多份重复课表但文件名不同 | 原件、副本、重命名副本各 1 份 | 同内容副本跳过；不同成员真实文件不能误跳过 |
| 节假日调课 | 带官方调课/补课校历样本 | 空课日期以校历为准，而不是只按默认学期周历 |

## 建议回归流程

1. 每次改解析器或成员检查逻辑，先运行 `python -m pytest -q`。
2. 把真实异常 PDF 放进仓库外的参考样本目录，避免泄露成员个人课表。
3. 为每份样本维护一个脱敏期望结果：成员名、课表类型、课程块数量、关键周次/节次。
4. 样本库回归只输出汇总和差异，不把原始 PDF 内容写入测试日志。
