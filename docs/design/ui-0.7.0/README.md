# 空谷 0.7.0 界面结构与设计

日期：2026-11-15。状态：源码已实现，浏览器与相关回归已验证；未提交、未发布、未构建本版安装器。

## 问题与采用方案

面向批量收集课表、安排组织活动的负责人。原界面把导入、生成、导出画成侧栏导航，又将结果、成员、文件、问题、PDF 和课程周视图放在同一排标签中。删减说明文字后，导航与操作仍然混在一起。

采用三个工作区，主要操作随页面变化：

| 工作区 | 内容 | 操作 |
|---|---|---|
| 课表资料 | 初始导入、本批文件；生成后可查看文件检查、成员课表和课程占用周视图 | 选择/添加文件、移除、清空、生成空课表 |
| 核对问题 | 待处理列表、对应 PDF 与修正表单 | 查看原文、现有修正和确认；阻断错误不能直接确认 |
| 空课结果 | 按周显示共同空闲；明细视图与周次保持一致；时段弹窗显示完整人员 | 切周、切换周表/明细、导出 |

学期设置与本地维护收纳在侧栏底部。启动失败会自动展开维护入口。原文核对中的文件识别说明默认折叠。移除宣传式长标题、重复流程、常驻日志及无结果时的空标签；主体文字 14–16 px，辅助文字主要为 13 px。

依据：[同类产品结构参考](同类产品结构参考.md)。这些结论来自官方文档研究，不是对竞品的实机体验结论。

## 设计与实现

- [参考设计图](reference.png)：内置 image_gen 生成；工具未暴露可选择或可核验的型号，未声称使用 image 2.5。
- [完整对照](comparison.png)：左侧参考，右侧浏览器实际渲染，均按 1280×820 归一化。
- [导入页](welcome.png)、[空课结果](result-week.png)、[1080×720 结果页](result-week-1080.png)、[资料页](sources-1080.png)、[原文核对](review-1080.png)。均使用合成示例，不是真实课表或准确率证据。
- [设计检查](../../../design-qa.md)、[布局检查](layout-checks.json)、[交互检查](interaction-checks.json)。

参考图中的合并节次属于视觉示意。实现按后端提供的实际节次、时间和人员逐条展示，不合并推断。午间按实际钟点排序；没有记录显示“未覆盖”，0 人显示“无人空闲”；同格多条记录显示“请查看明细”。没有运行时下载、新依赖、字体服务或数据上传。

主要实现：`src/main.ts` 负责工作区和动作，`src/availabilityView.ts` 只负责已有空闲记录的展示，`src/reviewView.ts` 支持定位对应原件，`src/workbench.css` 统一层级与字号。六个原有结果面板和 PDF/日历生命周期保留，解析算法、数据契约及导出门禁没有更改。

## 验证与限制

2026-11-15：TypeScript 检查、`npm.cmd run build:frontend` 通过；65 项相关 Python/Node 回归通过。Vite 保留大于 500 kB 的已有主包体积提示。没有运行整套解析回归或重新计算真实原件，这些不属于本轮界面改动。

独立 headless Edge 在 1280×820 与 1080×720 检查初始、文件选择、处理中、accepted、needs_review、blocked、启动失败与桌面环境不可用；外层无溢出。检查了导航、上下文按钮、分组标签键盘操作、滚动保留、学期设置展开/保存、周次筛选、弹窗与 Escape、合成 PDF 渲染以及第二份原件定位，未捕获到未处理 JavaScript 异常。

浏览器的原生文件选择与识别服务使用受控替身，只验证界面调用链。未将其写成 Windows 原生流程、安装、断网或真实 OCR 验收。本版安装器尚未重建；已安装的 0.6.9 不会因修改源码而自动更新。

## 图像提示词

实际生成使用内置 image_gen，并附上用户提供的原界面截图作为品牌与待改造结构参考。核心提示词如下：

> Use case: ui-mockup. ONE realistic production desktop app UI design for 空谷, offline Chinese group timetable processing. Reference attached is OLD UI solely for brand colors/wordmark. Redesign the information architecture completely, do not reproduce its action sidebar and six flat tabs.
>
> 1280x820 desktop frame, no browser/device chrome. Calm warm ivory #f8f9f4, solid forest-green #173d2e sidebar 216px, white/ivory data canvas, thin separators, generous spacing, legible Chinese. Header text 26px, body/table 14-16px, auxiliary >=13px. Local Chinese fonts, serif only wordmark/title, sans serif UI. No gradients, no marketing copy, no extra card dashboards.
>
> Sidebar top white 空谷 wordmark, small 青禾计划孵化项目. Three navigation destinations, not numbered workflow commands: 课表资料 with count 8; 核对问题; 空课结果 selected in pale sage. Bottom two disclosures 学期设置 and 本地维护, one quiet status 本地识别 已就绪. No import/generate/export buttons in sidebar.
>
> Show the completed result workspace. Main top header title 空课结果 and 4 位成员 · 8 份课表; right side 可导出 and primary button 导出空课表. Below: 第 11 周, 11月9日—11月13日, segmented toggle 周表 / 明细. Current-date anchor 2026-11-15. Main content is a weekly availability table, not a course calendar: Monday through Friday, representative period rows, counts and synthetic names. Full availability has stronger sage fill; zero says 无人空闲; an uncovered cell says 未覆盖 and must not appear green. Keep names and counts consistent. Clicking cells will show the full people list in implementation.
>
> Use 3 navigation destinations, one page title, one primary action, one local view toolbar and one data surface. No nested cards or secondary global navigation. Mascot is reserved for the empty import state. Render Chinese accurately.

首轮仅精简视觉的参考稿保留在 `../ui-0.6.10/reference.png`，它是本次任务中的初稿，不代表曾交付或发布 0.6.10。
