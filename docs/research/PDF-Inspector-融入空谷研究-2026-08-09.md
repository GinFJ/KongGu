# PDF Inspector 融入空谷研究

> 研究日期：2026-08-09
>
> 研究范围：仅分析 GitHub 仓库、README、API 文档、源码、清单、许可证与安全策略；本轮不引入依赖、不修改正式解析链路。
> 首选仓库快照：`firecrawl/pdf-inspector@493fed498eb6ee9a7026b2810b7a58d19b261275`

## 一、结论

首选是 [firecrawl/pdf-inspector](https://github.com/firecrawl/pdf-inspector)。它最适合空谷的部分不是“PDF 转 Markdown”，而是以下三项能力：

1. 在解析前区分原生文本、扫描、图片和混合型 PDF；
2. 给出逐页 OCR 路由与机器可读原因；
3. 输出带字体和坐标的文本项，用于解释“文本层、OCR、版式规则”到底在哪一层出错。

建议先把它接入 Python sidecar 的**旁路诊断模式**，与现有 PyMuPDF 结果对账，不立即替换现有解析器，也不让它直接决定 `accepted / needs_review / blocked`。只有脱敏真实课表回归证明它对中方、英方版式稳定后，才允许它参与逐页 OCR 路由。

另一个同名仓库 [charlie-tomlinson/pdf-inspector](https://github.com/charlie-tomlinson/pdf-inspector) 是 PyMuPDF + Streamlit 可视化工具。它的边界框分层、OCR 模式切换和提取 flag 对比值得借鉴，但不应把 Streamlit 运行时嵌入空谷。

## 二、同名项目辨析

### 2.1 Firecrawl PDF Inspector：首选研究对象

[项目 README](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/README.md) 将其定义为 Rust PDF 分类与文本提取库，支持 Python、Node.js 和浏览器 WebAssembly。其主要输出包括：

- `TextBased / Scanned / ImageBased / Mixed` 分类；
- 置信度；
- 逐页 `pages_needing_ocr`；
- `scanned / no_text / vector_text / suspected_garbled_text` 等机器可读 OCR 原因；
- 带页码、坐标、宽高、字体、字号和样式的 `TextItem`；
- 表格、多栏、复杂版式与字体编码问题提示；
- 指定区域的文本提取及 `needs_ocr` 判断。

这些字段在 [Python API](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/docs/python.md)、[公共 Rust 类型与 OCR 原因](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/src/lib.rs) 和 [坐标文本类型](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/src/types.rs) 中可以交叉核对。

### 2.2 PyMuPDF 可视化 PDF Inspector：只借鉴交互

[charlie-tomlinson/pdf-inspector README](https://github.com/charlie-tomlinson/pdf-inspector/blob/7f23a6853a05646a55e8ff7ba448cd1ab63eca9e/README.md) 描述了以下交互：

- 在页面图像上叠加 `blocks / lines / spans / words` 边界框；
- 切换 OCR 的 `off / auto / full` 模式；
- 切换 PyMuPDF 文本提取 flags；
- 缩放、平移并观察提取结果变化。

这个仓库只有很小的 Streamlit 应用形态，技术栈与空谷现有 Tauri 前端重叠。更合理的做法是把交互思想复用到 `src/reviewView.ts` 已有的 PDF.js 画布和课程块叠加层中，而不是引入 Streamlit、Plotly 和另一套上传入口。该项目采用 [MIT 许可证](https://github.com/charlie-tomlinson/pdf-inspector/blob/7f23a6853a05646a55e8ff7ba448cd1ab63eca9e/LICENSE)。

### 2.3 Prawn PDF Inspector：不是本次目标

[prawnpdf/pdf-inspector](https://github.com/prawnpdf/pdf-inspector) 是 Ruby 的 PDF 输出测试辅助库，主要服务 Prawn 生成结果的文本绘制操作和页数断言，[README](https://github.com/prawnpdf/pdf-inspector/blob/master/README.md) 明确说明了这一定位。它可以启发“对导出 PDF 做结构断言”的测试思想，但与空谷的输入预检、OCR 路由和课表坐标解析不匹配，引入 Ruby 运行时也没有收益。

## 三、能力、架构与离线属性

### 3.1 技术结构

Firecrawl PDF Inspector 的核心是 Rust，并以 `lopdf` 解析 PDF；它通过 PyO3 提供 Python binding、通过 napi-rs 提供 Node binding、通过 wasm-bindgen 提供浏览器包。[Cargo.toml](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/Cargo.toml) 展示了核心依赖与可选 Python feature，[Node 清单](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/napi/package.json) 展示了 Windows x64 原生包。

它的处理链大致是：

```text
PDF 字节
→ 内容流与页树检查
→ 文本 / 图片 / 矢量轮廓 / 字体编码信号
→ 文档与逐页分类
→ 坐标文本、矩形和线段
→ 行序、多栏和表格分析
→ Markdown 或区域文本
```

项目的分类不是 OCR：它检查 `Tj / TJ` 文本操作、图片对象、字体解码能力和矢量轮廓等信号，然后指出哪些页仍应进入 OCR。[检测器源码](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/src/detector.rs) 与 [README 的分类说明](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/README.md#how-classification-works) 对此一致。

### 3.2 离线、网络与遥测

项目 README 声明核心库不使用 ML 模型和外部服务；[WASM 文档](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/wasm/README.md) 进一步说明解析在本地执行、PDF 字节不上传、CMaps 内嵌。对当前快照的核心 `src/`、`napi/src/`、`wasm/src/` 和 Cargo 清单检索，未发现 HTTP 客户端、遥测或上传实现。

因此它与空谷“离线 Windows、本地处理”的方向兼容。但这只是源码级初步审计，不等于完成所有传递依赖和已发布二进制的供应链审计。正式引入前仍需固定版本与哈希、核对 wheel 内容，并执行断网运行验证。

浏览器示例中的 `fetch('/document.pdf')` 是调用方取得输入的示例，不是解析库主动上传。空谷若以后试用 WASM，应直接把 Tauri 读取的本地字节传入，不加载远程 URL。

### 3.3 许可证

Firecrawl PDF Inspector 使用 [MIT License](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/LICENSE)，允许使用、修改和再分发，但分发时需保留版权与许可文本。若打入空谷安装包，应把许可证加入第三方许可清单。

## 四、与空谷当前实现的对应关系

当前空谷已经具备：

- `core/schedule_core.py`：PyMuPDF 文本与坐标提取、版式识别、必要时本地 PaddleOCR；
- `app/services/pdf_source_service.py`：文件存在性、课表类别、占位姓名和同内容异身份预检；
- `src/reviewView.ts`：PDF.js 页面渲染、课程块边界框叠加与人工修正；
- `src/types.ts`：课程块 `bbox / page_width / page_height / confidence` 与质量问题结构；
- `requirements.txt` 和 `KongguWorker.spec`：Python sidecar 与 PyInstaller 打包边界。

PDF Inspector 因而不应再造一条独立“PDF 阅读器”链路。它最适合补充当前尚不完整的文件级和逐页诊断：

| PDF Inspector 信号 | 空谷用途 | 不能替代的判断 |
|---|---|---|
| `pdf_type` | 文件级输入画像 | 中方 / 英方课表类别 |
| `pages_needing_ocr` | 逐页 OCR 候选 | 课程是否完整、占用是否正确 |
| `ocr_reasons_by_page` | 面向用户的可解释原因 | `accepted / needs_review / blocked` 最终门禁 |
| `has_encoding_issues` | 乱码文本层回退依据 | 成员身份核验 |
| 坐标 `TextItem` | 与 PyMuPDF / OCR 对账、调试叠加 | 专用课表版式规则 |
| 表格 / 多栏页 | 版式复杂度提示 | 课表单元格的周次与节次语义 |

尤其要避免把 `confidence` 命名为“识别准确率”。它是通用 PDF 分类信号，不是经过空谷人工真值验证的课表准确率。

## 五、推荐的集成方案

### 5.1 第一阶段：Python sidecar 旁路诊断

首选在 Python sidecar 内建立独立适配器，例如 `core/pdf_inspection.py`，固定输出空谷自有的数据契约：

```text
PDFInspectionResult
├─ engine / engine_version
├─ source_hash
├─ pdf_type
├─ confidence
├─ page_count
├─ has_encoding_issues
├─ pages[]
│  ├─ page_number      # 空谷面向工作流与界面统一使用 1-based
│  ├─ needs_ocr
│  └─ reasons[]
└─ layout
   ├─ pages_with_tables[]
   └─ pages_with_columns[]
```

适配器必须隔离第三方 API，不让 `pdf_inspector` 类型扩散到工作流、持久化和前端。首轮只记录诊断结果、耗时和与当前 PyMuPDF 判断的差异，不改变正式课程块，也不改变质量门禁。

选择 Python binding 的理由：

- 解析决策目前集中在 Python sidecar，职责位置一致；
- Windows x64 有预构建 Python wheel，[Python 文档](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/docs/python.md#install) 声明支持 CPython 3.8 及以上；
- 不需要把 PDF 再从 sidecar 传回 Tauri 或在前端复制解析规则；
- 后续如果试验失败，可以移除适配器，不影响现有 PyMuPDF 主链。

### 5.2 第二阶段：验证后参与 OCR 路由

只有旁路回归通过后，才把逐页 `needs_ocr` 用于控制现有 PaddleOCR。当前 `_extract_pdf_ocr_items` 会遍历整份 PDF；若改为逐页 OCR，需要同时完成：

- 给 OCR 提取函数增加明确页集合；
- 把页集合与引擎版本写入缓存签名；
- 升级 OCR / 解析缓存版本，防止旧缓存掩盖行为变化；
- 合并原生文本页和 OCR 页时保持坐标系、页码与阅读顺序一致；
- 对混合型 PDF、旋转页和乱码文本层建立回归。

即使 PDF Inspector 判定某页为原生文本页，空谷专用解析器仍需核验成员、课程块、周次、星期和节次；结果稀疏、身份冲突或版式未知时，仍进入 `needs_review` 或 `blocked`。

### 5.3 第三阶段：在现有复核页增加诊断图层

借鉴 PyMuPDF 可视化项目，在 `src/reviewView.ts` 已有 PDF.js 画布上增加可切换图层：

- 最终课程块；
- PyMuPDF 原生文本词框；
- OCR 文本框与置信度；
- PDF Inspector 文本项；
- 当前被判定为需 OCR 的页面或区域；
- 点击框后显示来源、提取引擎、坐标、原始文本和问题原因。

该功能应优先作为内部诊断开关，稳定后再决定是否向普通用户展示。正式界面只呈现可行动的原因，如“第 2 页没有可靠文本层，已使用本地 OCR”，不暴露第三方异常堆栈。

## 六、不建议的接入方式

- **不建议直接替换 PyMuPDF。** 空谷现有专用课表解析和真实样本经验均围绕 PyMuPDF 坐标建立；PDF Inspector 的通用 Markdown 优势不能证明课表语义解析更准。
- **不建议在 Tauri WebView 里使用 Node N-API 包。** Tauri 前端不是 Node 运行时，原生 `.node` 模块不应作为普通浏览器依赖加载。
- **不建议把 WASM 作为首个接入点。** 它会在前端形成第二套解析真值；文档还说明初始化后提取为同步操作，大文件应放到 Web Worker。
- **不建议嵌入 Streamlit 应用。** 这会引入第二套 UI、服务生命周期和上传入口；只需复用边界框和分层开关的设计。
- **不建议直接采用项目方通用基准结论。** 官方基准衡量通用文档的阅读顺序、表格和标题，不等于中英方课表真值准确率。

## 七、主要风险与防护

### 7.1 API 与版本稳定性

当前快照中 Rust crate、Python 包和 npm 包使用不同版本序列；必须按具体 binding 记录版本，不能只写一个“PDF Inspector 版本”。此外，接口存在页码基数差异：完整处理结果的 OCR 页通常是 1-based，轻量分类和部分筛选接口使用 0-based，[Python 文档的类型说明](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/docs/python.md#types) 和 [Rust `classify_pdf_mem`](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/src/lib.rs#L391-L400) 均能看到这种边界。

防护方式：锁定 Python 包精确版本；所有页码只在适配器入口转换一次；持久化时记录 `engine_version`；升级依赖必须重跑脱敏回归。

### 7.2 不可信 PDF 与拒绝服务

[安全策略](https://github.com/firecrawl/pdf-inspector/blob/493fed498eb6ee9a7026b2810b7a58d19b261275/SECURITY.md) 明确把恶意 PDF 导致的 panic、越界和拒绝服务列为安全范围。当前研究快照的最新提交本身就是 AcroForm `/Kids` 自循环导致栈溢出的修复，[提交记录](https://github.com/firecrawl/pdf-inspector/commit/493fed498eb6ee9a7026b2810b7a58d19b261275) 说明该风险是现实存在的。

防护方式：

- 保持解析在 sidecar 进程中；
- 设置文件大小、页数、单文件耗时和内存上限；
- 第三方解析崩溃或超时时生成可理解的 `blocked` 问题，不自动放行；
- 固定已审核版本，不跟随浮动最新版；
- 对损坏、加密、深层嵌套、自引用和超大页面 PDF 建回归。

### 7.3 打包与供应链

Python 包包含 Rust 原生扩展。PyInstaller 是否自动收集 `.pyd`、CMaps 和类型无关资源必须实际验证，不能只以开发环境 `import` 成功为依据。正式候选需要执行：

- 冻结 sidecar；
- 检查原生扩展和许可证进入包内；
- 全新 Windows 用户目录运行；
- 断网导入、预览、诊断、OCR 与导出；
- 记录安装包体积变化和 SHA-256；
- 确认没有运行时下载或遥测。

## 八、试验与验收建议

仓库外真实样本库已经完成页级文本层对账，但课程块人工真值尚未建立，因此现阶段仍不能得出“能提升课表准确率”的结论。下一轮真值回归建议至少覆盖：

1. 中方原生文本课表；
2. 英方原生文本课表；
3. 图片型 PDF；
4. 文本与图片混合 PDF；
5. CID / ToUnicode 异常或乱码文本层；
6. 矢量轮廓文字；
7. 旋转页；
8. 损坏、加密和极端嵌套 PDF；
9. 当前 PyMuPDF 可解析但 PDF Inspector 判断需 OCR 的反例；
10. PDF Inspector 判断为文本型但空谷课程块稀疏或错误的反例。

每份样本记录以下指标：

- 两种引擎的文件分类和逐页 OCR 判断；
- 文本项数量、关键锚点、坐标差异；
- 当前课程块结果是否变化；
- OCR 页数与耗时变化；
- `accepted / needs_review / blocked` 是否正确；
- 是否出现崩溃、超时、内存异常或乱码；
- 人工真值下的课程块、周次、星期和节次正确性。

通过标准不是“PDF Inspector 与 PyMuPDF 一致”，而是：差异可解释、错误不会被静默接受、正式结果质量不下降，并且逐页 OCR 路由在真实课表上带来可复核的时间或可靠性收益。

## 九、最终建议

- **首选：** Firecrawl Python binding + 空谷自有适配器 + 旁路对账；同时在现有复核页借鉴 PyMuPDF Inspector 的诊断图层。
- **可以接受：** 暂不加第三方依赖，只把逐页 OCR 原因、坐标差分和多层叠加的设计用 PyMuPDF 实现；这条路风险更低，但需要自己维护分类规则。
- **不建议：** 直接替换解析主链、直接嵌入 Streamlit、在前端加载 Node N-API，或在没有脱敏课表真值的情况下将第三方置信度接入正式质量门禁。

下一步最小可交付物应是一份不影响正式结果的 `PDFInspectionResult` 适配器、单元测试和脱敏样本差分报告。完成这些证据后，再决定是否进入逐页 OCR 路由和用户可见界面。

## 十、本轮落地与验证补记

研究完成后，项目按第一阶段方案实际落地：

- 新增 `core/pdf_inspection.py`，固定 Python binding `pdf-inspector==0.2.6`；
- 空谷对外数据契约统一使用 1-based 页码，隔离第三方 API 的页码差异；
- 单文件结构检查限制为 50 MB、100 页，第三方绑定失败时回退 PyMuPDF；
- 诊断结果进入工作流快照和 PDF 复核侧栏，但不改变课程块、质量状态或正式 OCR 路由；
- PyInstaller 已收集 `pdf_inspector.pyd`、包元数据、SBOM 与 MIT 许可证。

对仓库外正式样本库执行了只读、本地、仅聚合输出的旁路检查：70 份 PDF、109 页均由 Firecrawl 引擎成功处理；分类为 53 份文本型、16 份图片型、1 份扫描型，共建议 OCR 26 页，其中 25 页原因为 `vector_text`、1 页为 `scanned`。PyMuPDF 同样发现 26 个无文本层页面，两者页集合完全一致；没有出现“PyMuPDF 有文本而 PDF Inspector 建议 OCR”的页面。

该对账只证明当前样本上的页级文本层判断一致，不证明课程块、周次、星期或节次准确率提升。逐页 OCR 路由仍需在人工真值和缓存失效策略完成后再启用。
