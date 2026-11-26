# 空谷可信排班架构

核对日期：2026-11-15。本页说明当前源码的职责与协议；版本、测试与安装状态见 [项目总览](../MEMORY/MEMORY.md)。主流程为“持久任务 → 质量门禁 → 人工复核 → 可信导出”。`core/schedule_core.py` 保留解析编排与兼容实现，专用识别规则和工作流能力分别进入以下边界：

- `core/signature.py`：parser/profile/OCR/config 联合签名。
- `core/models.py`：组合成员标识、OCR token、课程块、问题、修正和质量状态。
- `core/quality.py`：`accepted / needs_review / blocked` 门禁。
- `core/ocr_engines.py`：稳定与实验 OCR 引擎描述、替换判定。
- `core/runtime_control.py`：文件边界和 OCR 页边界的协作取消。
- `core/english_bordered_grid.py`、`core/english_clock_axis.py`、`core/english_dates.py`、`core/english_popup_underlay.py`：英方网格、午间时间、日期与浮层证据。
- `core/timetable_coverage.py`：以原件实际日期范围限制可计算的共同完整工作周。
- `app/services/state_store.py`：SQLite schema、迁移、任务、问题、修正和审计事件。
- `app/services/job_service.py`：单活跃任务、顺序队列、进度、取消和失败重试。
- `app/services/review_service.py`：修正、确认、signature 失效和同源复用。
- `app/services/review_evidence.py`：将 warning 确认绑定完整结果摘要，复用证据与重新评估相分离。
- `app/services/workflow_persistence.py`：版本化 JSON 结果；不再用 pickle 作为跨版本引用。

## Sidecar 协议

常驻模式：

```powershell
.\src-tauri\binaries\konggu-worker-x86_64-pc-windows-msvc.exe --serve
```

输入每行一个请求：

```json
{"id":"request-id","command":"job.get","payload":{"job_id":"uuid"}}
```

同步响应：

```json
{"id":"request-id","type":"response","ok":true,"data":{}}
```

异步事件：

```json
{"type":"event","event":"job.progress","job_id":"uuid","source_id":"uuid","stage":"course_parse","current":0,"total":2,"message":"正在解析课程占用槽"}
```

任务命令为 `job.start`、`job.get`、`job.cancel`、`job.retry_failed`；复核命令为 `review.get`、`review.apply`、`review.mutate`、`review.confirm`；导出命令为 `exports.excel`。另有 `resources.status`、`resources.repair`、`settings.calendar.get`、`settings.calendar.save`、`pdf.inspect` 和 `data.clear`。实际协议见 [sidecar](../app/sidecar.py)；响应和事件共用启动时固定的输出管道，防止 OCR 重定向 stdout 后丢失回复。

任务仅在复核凭据复用、质量与文件状态保存完毕后报告 `completed`；任务结束不等于质量已接受，导出仍须检查实时门禁。

## 数据和隐私

数据库默认位于 `%LOCALAPPDATA%\Konggu\data\state.sqlite3`，保存结构化结果、内容哈希、问题、修正和审计事件，不复制原始 PDF。任务请求及结果快照剔除源绝对路径；活动任务的文件记录暂存路径，进入终态或启动时恢复中断任务后清除。原文核对使用当前 sidecar 会话的内存路径，重启后须重新选择来源。

前端通过文件对话框选择 PDF，使用 Tauri fs 读取原件、shell 启动固定 `--serve` worker；当前没有 persisted-scope 插件，也不承诺跨重启恢复文件授权。默认保留期和清理范围见 [配置与持久化边界](配置与持久化边界.md)。

人工修正复用需要匹配源哈希、完整解析签名、课程块标识及可编辑字段；签名包含有效学期设置。warning 确认还校验成员、问题和占用等完整摘要。依据变化的历史修正标为 stale，不能继续导出旧结果；error 不能靠确认或复用放行。

## 真值基准

`benchmarks/ground_truth/manifest.json` 包含 12 个匿名样本标识、内容哈希、profile 和来源类型，不包含姓名、路径、课程文字或 PDF。初始状态为 `pending`；在人工逐槽核对并改为 `verified` 前，`tools/evaluate_ground_truth.py` 会拒绝输出准确率通过结论。

```powershell
.\.venv\Scripts\python.exe tools\evaluate_ground_truth.py --predictions predictions.json
```

该真值评估器的通过条件为 profile 准确率 100%、总体槽位 F1 不低于 98%、任一 profile F1 不低于 95%、已知坏文件误放行数为 0。它只评价已标注样本，不是完整发布门禁，也不代表尚为 pending 的 12 份样本已达到这些数值；发布还须完成 [发布检查清单](发布检查清单.md)。

## 导出

正式 Excel 在原有兼容工作表后追加：全部空课明细、成员课程明细、解析问题清单、文件处理记录、人工修正历史和使用说明。`blocked` 或未确认的 `needs_review` 会在写文件前被拒绝。

## 当前仍需人工完成的验收

以下事项不能由代码替代：

1. 对 12 份代表样本逐槽标注并双人复核。
2. 使用标注结果比较 `paddle_v4`、`paddle_v6_small` 和 `rapidocr_v6_onnx`；实验模型在达标前不进入生产安装包。
3. 由 3～5 名协会成员完成导入、失败解释、修正、重启恢复、复用和导出试运行。
4. 试运行通过后再更新公开 Roadmap 和发布版本。


0.8.0 的 `review.mutate` 处理补录、删除、恢复、区域排除/恢复和最近操作撤销；必须提供当前结果 revision、唯一操作编号、来源哈希与原因。服务校验当前签名及会话中的原文件哈希，事务中一并保存修正、快照、问题及重算结果。删除记录单独保留在快照的 `removed_blocks`，不会进入占用，但继续证明成员及来源已提交。现有 SQLite 修正表存储事件，无数据库结构迁移；重放验证来源绑定、前置课程状态及原区域证据，失配时停止该来源后续操作并要求核对。
