# 空谷可信排班架构

本轮把空谷的主流程从“一次性解析并导出”改为“持久任务 → 质量门禁 → 人工复核 → 可信导出”。`core/schedule_core.py` 作为现有解析算法兼容层保留；新增能力不再写入该文件，而是分别进入以下边界：

- `core/signature.py`：parser/profile/OCR/config 联合签名。
- `core/models.py`：组合成员标识、OCR token、课程块、问题、修正和质量状态。
- `core/quality.py`：`accepted / needs_review / blocked` 门禁。
- `core/ocr_engines.py`：稳定与实验 OCR 引擎描述、替换判定。
- `core/runtime_control.py`：文件边界和 OCR 页边界的协作取消。
- `app/services/state_store.py`：SQLite schema、迁移、任务、问题、修正和审计事件。
- `app/services/job_service.py`：单活跃任务、顺序队列、进度、取消和失败重试。
- `app/services/review_service.py`：修正、确认、signature 失效和同源复用。
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

任务命令为 `job.start`、`job.get`、`job.cancel`、`job.retry_failed`；复核命令为 `review.get`、`review.apply`、`review.confirm`；导出命令保持 `exports.excel`。

## 数据和隐私

数据库默认位于 `%LOCALAPPDATA%\Konggu\data\state.sqlite3`。它只保存结构化结果、源路径、内容哈希、问题、修正和审计事件，不复制原始 PDF。用户通过 dialog 选择的路径由 Tauri fs 与 persisted-scope 恢复授权。

人工修正必须同时匹配 `source_hash + parser_signature` 才能自动复用。解析器、profile、OCR 引擎/模型或配置任一变化，旧修正都会显示为 stale。

## 真值基准

`benchmarks/ground_truth/manifest.json` 包含 12 个匿名样本标识、内容哈希、profile 和来源类型，不包含姓名、路径、课程文字或 PDF。初始状态为 `pending`；在人工逐槽核对并改为 `verified` 前，`tools/evaluate_ground_truth.py` 会拒绝输出准确率通过结论。

```powershell
.\.venv\Scripts\python.exe tools\evaluate_ground_truth.py --predictions predictions.json
```

发布门槛固定为 profile 准确率 100%、总体槽位 F1 不低于 98%、任一 profile F1 不低于 95%、已知坏文件误放行数为 0。

## 导出

正式 Excel 在原有兼容工作表后追加：全部空课明细、成员课程明细、解析问题清单、文件处理记录、人工修正历史和使用说明。`blocked` 或未确认的 `needs_review` 会在写文件前被拒绝。

## 当前仍需人工完成的验收

以下事项不能由代码替代：

1. 对 12 份代表样本逐槽标注并双人复核。
2. 使用标注结果比较 `paddle_v4`、`paddle_v6_small` 和 `rapidocr_v6_onnx`；实验模型在达标前不进入生产安装包。
3. 由 3～5 名协会成员完成导入、失败解释、修正、重启恢复、复用和导出试运行。
4. 试运行通过后再更新公开 Roadmap 和发布版本。
