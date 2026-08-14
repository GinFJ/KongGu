# 空谷当前工作区安全与隐私审查报告

审查对象：当前工作区代码（含未提交改动），审查日期：2026-08-14。
审查方式：静态代码审查、依赖与敏感信息检索、Excel 输出探针、Python 全量测试、前端生产构建。
本报告已根据本轮修复更新；修复发生在当前未提交工作区，尚未代表安装包发布。

## 执行摘要

当前代码没有发现明文密钥、密码、令牌或运行时向外上传课表的实现；OCR 模型下载位于显式的准备脚本中，正式运行会清除模型下载开关；压缩包解压也有路径穿越检查。

本轮修复状态：K-SEC-001 至 K-SEC-007 已完成代码级修复或治理加固；全量 Python 测试、前端构建、Python 编译、密钥扫描和 Tauri 配置静态检查已通过。真实安装包、断网实机和多账户 Windows ACL 仍未验证。

但当前存在 2 个需要优先处理的问题：

1. 课表中的可控文本会被直接写入 Excel，形如 `=HYPERLINK(...)` 的内容会成为公式，打开导出文件时可能触发公式注入风险。
2. 解析结果、成员/课程信息、人工修正、绝对文件路径和 OCR 缓存会长期保存在本机 AppData/SQLite/缓存中，当前没有数据清理、保留期限或敏感字段最小化机制。

另外还有桌面权限、CSP、恶意 PDF 资源消耗、离线资源清单路径边界和错误信息脱敏方面的中风险加固项。

## 高风险

### K-SEC-001：Excel 公式注入（已修复）

- 严重性：High
- 位置：`core/schedule_core.py:1064-1067`、`core/schedule_core.py:1015-1017`；输入来自 `core/schedule_core.py:352-534`。
- 证据：数据框单元格通过 `sheet.append(row)` 原样写入；周表也直接写入包含成员名称的 `value`。
- 已验证：用 `=HYPERLINK("https://example.invalid","x")` 作为成员/课程文本写入工作簿后，重新读取得到 `data_type == 'f'`，即 Excel 公式。
- 影响：攻击者或恶意/被篡改的 PDF 可以把课程名、姓名、问题文本等变成公式。用户打开导出文件时可能触发外部链接、危险函数或其他 Excel 公式行为，形成代码执行、数据外带或钓鱼风险；具体可利用程度取决于 Excel 版本和安全策略。
- 修复：在所有进入工作簿的字符串边界统一做 Excel 注入防护。至少对首字符为 `=`, `+`, `-`, `@`、制表符或换行的字符串前置单引号或使用安全文本写入策略；同时对周表、明细表、问题清单、文件记录、修正历史和说明表全部覆盖，并增加回归测试。
- 缓解：打开工作簿前提示“文件来自本地解析结果，若来源不可信请勿启用内容”；不要只在 UI 显示阶段过滤，因为风险发生在导出文件中。
- 误报说明：`openpyxl` 本身不会在生成时执行公式；风险出现在用户后续用 Excel/兼容软件打开文件时，因此需要按导出给第三方使用的场景处理。
- 修复证据：`core/schedule_core.py` 在所有 DataFrame 工作表和周表单元格写入边界统一转义；`tests/test_export_excel_service.py` 覆盖 `=`, `+`, `-`, `@`、制表符输入，验证重新打开工作簿后没有公式单元格。

## 中风险

### K-SEC-002：个人课表派生数据长期留存且包含绝对路径（已修复，保留当前会话复核路径）

- 严重性：Medium（隐私）
- 位置：`app/services/state_store.py:41`、`app/services/state_store.py:61-63`、`app/services/state_store.py:72-79`、`app/services/state_store.py:164-185`；`app/services/workflow_persistence.py:29-53`；`core/schedule_core.py:1143-1219`；`src/types.ts:97-102`。
- 证据：SQLite 使用 WAL；任务请求保存 `request_json`，文件表保存 `source_path`；结果快照保存源文件路径、成员、课程块、占用、问题和 OCR 结构数据；OCR 文本缓存保存完整识别文本，布局缓存保存识别 token 和坐标；复核负载还将 `source_path` 返回前端。
- 影响：课表姓名、课程、时间、组织身份和原始 Windows 路径属于敏感的个人/组织数据。应用关闭、重新安装或任务结束后仍可能留在 `state.sqlite3`、`-wal`、`-shm` 和缓存目录中；备份、故障采集、多人共用 Windows 账户或恶意本地程序都可能扩大暴露面。当前没有用户可见的清理入口、保留期限或按任务删除机制。
- 修复：明确数据生命周期；默认只保留当前工作流，增加“删除本次数据/清理全部本地数据”入口；删除任务、结果、问题、修正、OCR/解析缓存并执行 SQLite checkpoint；默认只持久化文件名、内容哈希和必要结果，避免保存绝对路径；对诊断输出做统一脱敏。
- 缓解：继续保持原始 PDF 不复制进数据库；将 AppData 目录权限和隐私说明写入产品文档；禁止把 SQLite、缓存、导出和日志打包上传到 issue 或公开基准。
- 误报说明：这是离线单机产品，代码没有发现网络上传，因此不是“已向外泄露”；问题是本地留存范围和保留控制不足。
- 修复证据：任务请求不再持久化 `paths`；结果快照不保存绝对路径；任务进入完成、失败或取消终态后立即以不可逆占位符替换数据库源路径；所有任务和修正默认 30 天清理；增加 `data.clear` 入口清理结果、修正、审计、解析/OCR缓存并执行 WAL checkpoint。待复核任务仅在当前 sidecar 进程内保留路径以支持原文核对，重启后旧任务只保留脱敏结果。

### K-SEC-003：Tauri CSP 被关闭，且渲染层权限偏宽（已修复）

- 严重性：Medium
- 位置：`src-tauri/tauri.conf.json:22-24`、`src-tauri/capabilities/default.json:8-31`、`src/sidecarClient.ts:67-77`、`src/reviewView.ts:205-212`。
- 证据：生产配置为 `"csp": null`；前端同时获得 `fs:default`、`fs:allow-read-file`、sidecar `spawn/execute`、任意参数 `args: true`、stdin 写入和 kill 权限。前端会按结果中的路径读取 PDF，并可启动本地 worker。
- 影响：当前 UI 对课表文本大部分做了 HTML 转义，未确认存在可由普通 PDF 文本直接触发的 XSS；但一旦出现 XSS、供应链脚本污染或被篡改的本地状态，攻击面可以读取选定文件、读取更多文件范围、启动 worker 并调用其本地处理/导出能力。关闭 CSP 也失去了浏览器层的纵深防御。
- 修复：为生产构建配置最小 CSP，至少限制脚本、连接、对象和资源来源；移除不需要的 `fs:default`，只保留明确的文件读取范围；sidecar 仅允许固定的 `--serve` 参数，前端不需要 `execute` 时移除 execute；将文件读取和导出能力收敛到最小命令接口。
- 缓解：对所有来自 sidecar/SQLite 的枚举值做 allowlist；`src/main.ts:737-741` 和 `src/reviewView.ts:497-504` 中动态 class/data 属性改用 DOM API 或显式白名单，不要依赖字符串模板。
- 误报说明：Tauri 的命令调用不是网络 API，当前没有发现远程调用者；本项主要是桌面渲染器被攻破后的纵深防御问题。
- 修复证据：生产 CSP 已限制脚本、连接、对象、表单和资源来源；移除 `fs:default`、`shell:allow-execute` 和任意 sidecar 参数，仅保留固定 `--serve`；前端命令调用增加 allowlist。

### K-SEC-004：PDF 检查上限没有真正阻止后续解析，存在资源耗尽风险（已修复）

- 严重性：Medium
- 位置：`core/pdf_inspection.py:19-20`、`core/pdf_inspection.py:147-159`；`app/services/desktop_workflow.py:91-106`；`core/schedule_core.py:352-417`。
- 证据：结构检查定义了 50 MB/100 页上限，但超限只返回 `skipped`；桌面工作流随后仍将所有 `add_result.added` 传给 `generate_availability`，而解析核心继续打开 PDF、提取文本或逐页 OCR。任务输入也没有文件数量、总字节数或总页数上限。
- 影响：恶意 PDF、超高分辨率扫描件、压缩炸弹或一次选择的大量文件可消耗大量内存/CPU，阻塞 worker，触发 OCR 崩溃或让桌面端不可用。单机产品中这是本地 DoS；若未来接入批量目录/外部输入，风险会升高。
- 修复：把检查结果中的 `skipped/unavailable` 转为阻断问题，不再进入解析；在任务入口增加单文件大小、总大小、文件数量、总页数和 OCR 页预算；解析核心再次独立执行限制，不能只依赖前置 UI/检查层。
- 缓解：保留现有取消机制；对每页 OCR 设置时间/像素预算，并在异常后释放 PDF、图像和 OCR 对象。
- 误报说明：当前文件选择来自本地文件对话框，降低了远程攻击可能性，但不能消除恶意本地文件和资源耗尽问题。
- 修复证据：桌面工作流把 `skipped/unavailable` 检查结果转为阻断问题；入口限制 32 份、单文件 50 MB、批量 200 MB/1000 页；解析核心再次执行大小、页数和加密检查；新增无效 PDF 和批量上限回归。

### K-SEC-005：离线资源清单驱动的复制/删除缺少最终路径边界校验（已修复）

- 严重性：Medium
- 位置：`app/offline_resources.py:131-143`、`app/offline_resources.py:213-219`、`app/offline_resources.py:258-267`。
- 证据：`entry["source"]` 和 `entry["target"]` 来自 manifest；目标路径通过字符串前缀拼接，未在 `_target_path` 中调用 `resolve()` 并确认仍位于 `user_resources_root`/`ocr_models_root`；目录目标存在时会直接 `shutil.rmtree(target)`。
- 影响：如果安装目录中的 manifest、测试环境变量或可配置 bundled root 被篡改，`../` 路径可能使修复流程复制到目标目录之外，或删除目标目录之外的目录。当前 manifest 是随应用打包的，普通远程攻击者不能直接改它，但这是一个可避免的本地篡改/供应链边界问题。
- 修复：对 source 和 target 分别做 `resolve()`；使用 `Path.is_relative_to()` 或安全的 `relative_to()` 确认 source 在 bundled root、target 在允许的用户资源根目录；拒绝绝对路径、盘符路径、UNC 路径和 `..`；删除前再次检查目标根和文件类型。
- 缓解：对 manifest 本身做签名或至少做固定位置/固定 schema 校验；不要允许生产环境通过任意环境变量替换 bundled root。
- 误报说明：当前仓库生成的 manifest 由固定脚本产生，且 OCR 下载归档有 SHA-256 和 tar 路径检查；本发现针对运行时 repair 的 manifest 边界，不否定已有下载/解压保护。
- 修复证据：source/target 都拒绝绝对路径、盘符、UNC 和 `..`，并在 `resolve()` 后确认 containment；删除目录前使用同一安全目标解析；资源状态和修复失败结果不返回绝对路径。

## 低风险与待加固项

### K-SEC-006：错误、资源状态和一次性 sidecar 模式可能暴露诊断路径（已修复）

- 严重性：Low/Medium（隐私加固）
- 位置：`app/sidecar.py:57-63`、`app/sidecar.py:124-125`、`app/sidecar.py:189-200`、`app/services/job_service.py:186-197`、`core/schedule_core.py:105-107`。
- 证据：一次性 sidecar 请求返回完整 `traceback`；资源修复失败结果包含 source/target/error 的绝对路径；任务失败会将原始异常字符串保存并发送到 UI；OCR 异常消息保留第三方运行时错误文本。
- 影响：诊断文本可能包含用户名、模型目录、文件路径、依赖路径和底层组件信息。当前持久 sidecar 不把 traceback 返回前端，但相关异常仍可能进入任务数据库或 UI。
- 修复：面向用户只返回稳定错误码和脱敏消息；详细异常写入受控本地诊断日志并过滤路径、姓名、课程文本；生产模式不返回 traceback；资源状态只返回相对资源标识和状态。
- 缓解：日志默认不启用或使用轮转并设置大小/保留期限；公开报告、测试输出和 issue 模板禁止包含原始路径和课表文本。
- 误报说明：本轮没有发现 `setup_logging()` 被业务主流程广泛调用，实际日志暴露面需要结合发布包启动路径再确认。
- 修复证据：一次性模式不再返回 traceback；错误消息统一移除 Windows/POSIX 绝对路径并限制长度；OCR异常只返回异常类型；资源状态只返回相对标识；新增 sidecar 脱敏回归。

### K-SEC-007：Python 依赖没有锁定版本（已加固）

- 严重性：Low（供应链风险，未确认具体漏洞）
- 位置：`requirements.txt:1-8`。
- 证据：`pandas`、`openpyxl`、`PyMuPDF`、`numpy`、`paddlepaddle`、`paddleocr`、`opencv-python-headless`、`pytest` 和 `pyinstaller` 均未固定版本；前端有 lockfile，而 Python 没有等价的完整锁定文件。
- 影响：不同环境可能安装不同解析器、PDF 引擎或 OCR 运行时版本，难以及时确认恶意 PDF 修复、依赖漏洞修复和构建可复现性。
- 修复：为发布环境生成并审核 Python lock/constraints 文件，记录 Python、PyMuPDF、pdf-inspector、PaddlePaddle/PaddleOCR 版本；把依赖安全扫描纳入发布门禁，尤其关注 PDF 解析和原生扩展。
- 误报说明：本轮没有联网查询漏洞库，因此不能据此断言某个当前版本存在已知漏洞；这是版本治理缺口。
- 修复证据：`requirements.txt` 已锁定当前验证环境的 pandas、openpyxl、PyMuPDF、pdf-inspector、numpy、PaddlePaddle/PaddleOCR、OpenCV、pytest 和 PyInstaller 版本。联网漏洞库查询仍未执行。

## 未发现或已有保护

- 敏感信息检索未发现实际 API key、密码、私钥、令牌或数据库凭据。
- 应用业务代码未发现 `requests/httpx/fetch` 等运行时上传路径；OCR 下载只出现在显式的资源准备脚本中，运行时会移除模型下载环境开关。
- 未发现 `pickle`、动态 `eval/exec`、shell=True 或把用户输入拼入操作系统命令的业务路径。
- HTML 模板中姓名、课程、文件名、问题消息等主要文本经过 `escapeHtml`；当前没有确认“普通恶意 PDF 文本直接变成前端脚本”的可利用链；sidecar 命令也已在前端增加 allowlist。
- OCR 模型下载有 HTTPS、大小和 SHA-256 校验；tar 解压在 `tools/prepare_offline_resources.py:119-127` 做了目标路径检查并使用 `filter="data"`。
- PDF 结构检查和解析核心均有大小、页数与加密门禁，桌面工作流另有限制 32 份/批量 200 MB/1000 页。

## 验证记录

- Excel 公式探针：初始审查确认 `=HYPERLINK(...)` 可成为公式；修复后回归验证所有公式样式字符串均为文本单元格。
- 前端生产构建：通过，`npm.cmd run build:frontend` 成功；Vite 仅提示 chunk 大于 500 kB，不是安全失败。
- 安全相关/前端契约测试：专项回归 41 passed，最终全量 156 passed、2 warnings。
- Python 全量测试：156 passed，2 warnings；使用项目内临时目录运行，避免系统临时目录 ACL 问题。
- Python 编译检查：通过，`app`、`core`、`tools` 可编译。
- 本轮未进行联网漏洞库查询、真实安装包运行、Windows 多账户 ACL 审计或恶意 PDF 模糊测试，因此这些项目仍是未验证项。

## 修复后状态与剩余验证

- 已修复：K-SEC-001、K-SEC-002、K-SEC-003、K-SEC-004、K-SEC-005、K-SEC-006。
- 已加固：K-SEC-007（版本已锁定，但依赖漏洞库查询待联网授权/发布门禁执行）。
- 已验证：156 个 Python 测试通过、前端生产构建通过、TypeScript 和 Cargo locked check 通过、Python `compileall` 通过、无明显密钥扫描命中、CSP 已配置且 `fs:default`/`shell:allow-execute`/跨重启 persisted scope 已移除。
- 未验证：真实安装包重新构建、断网启动/导入/导出/卸载、多 Windows 账户 ACL、恶意 PDF 模糊测试、联网漏洞库查询。

## 原审查建议顺序（已执行）

1. 先修 K-SEC-001：统一 Excel 文本安全写入并增加恶意字符串回归测试。
2. 再修 K-SEC-002：确定保留策略、清理入口和缓存/SQLite 删除闭环。
3. 同一轮收紧 K-SEC-003：配置 CSP、收窄 Tauri fs/shell 权限和动态属性白名单。
4. 修 K-SEC-004/K-SEC-005：让资源限制真正阻断后续解析，并为 manifest 路径做 resolve/containment 校验。
5. 最后处理 K-SEC-006/K-SEC-007：统一错误脱敏、日志生命周期和 Python 依赖锁定。
