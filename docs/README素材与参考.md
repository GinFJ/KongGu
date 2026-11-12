# README 素材与参考

整理日期：2026-11-12；对应源码版本：0.6.4。

## IPD 内容

本轮使用用户提供的 IPD Part A 中文终稿 `Konggu_PartA_中文版_v2.22.docx`。旧版 v2.4 仅用于定位资料，实际采用终稿中的内容与嵌入图片。

正文取用了办公室逐人比对中英课表、转录姓名时出现错列和漏列、核查后交付 Excel 的场景，以及空课名单只表示没有课程、仍需联系成员确认安排的边界。未引用人数、用时作为效率证明，未将作业中的识别实验和试用计划写成现有功能。

三张 PNG 从终稿媒体目录原样提取；图中没有成员姓名、学号或真实课表。随图嵌入的 diagrams.net 模型已检查，另汇集为[可编辑图表](images/readme/konggu-readme-diagrams.drawio)。原始 Word、封面和本机来源路径未放入仓库。

| 图表 | PNG SHA-256 |
| --- | --- |
| [工作人员制作与核查流程](images/readme/konggu-workflow.png) | `d3acdf6060bb3ae1eb2102fd506b0d2a521d3d9150ae063a12cee6050103b87e` |
| [双课表合并规则](images/readme/konggu-merge.png) | `ef6473a692ad24395488d2ea19dc0f783ae1dd36d2ce191b2429c3cff89c0d85` |
| [本机处理流程](images/readme/konggu-architecture.png) | `c29773aee2f5198b62aa83f632117b2d331336c9df90a0251085d379c29a70b0` |

图 1 表示工作人员流程，含人工对照原件核查，不把它解释成程序能自动证明名单准确。图 2 表示本机数据处理与核查步骤，不作为代码模块依赖图。图 4 中的“未知”表示缺件或识别问题未解决；应用界面的实际状态名仍是 accepted、needs_review、blocked。

本轮对照了 `core/schedule_core.py` 的占用合并、`core/quality.py` 的缺件与导出门禁、`app/services/review_service.py` 的修正重算和 `app/services/desktop_workflow.py` 的导出调用。没有重新运行识别准确率或安装包验收。

PDF 识别分流图未用于 README：终稿中的逐页选择、漏读格检测等部分内容属于测试计划，不能直接用来说明当前实现。

## GitHub README 参考

以下仓库的 README 于 2026-11-12 实际读取。只参考信息安排，没有复制它们的正文、截图或品牌素材。

| 来源 | 本次采用的做法 |
| --- | --- |
| [LocalSend](https://github.com/localsend/localsend/blob/main/README.md) | 简短介绍后提供清晰导航，使用入口与开发构建分开，原理另作说明 |
| [Immich](https://github.com/immich-app/immich/blob/main/README.md) | 标识、简短定位和图示放在前面，复杂说明通过链接继续阅读 |

空谷用自己的流程图展示用途；没有仿造正式下载、许可证、持续集成状态或用户数量徽章。安装候选和源码版本分别说明，保持原有验收限制。

## 首屏调整

2026-11-12，按用户提供的旧版截图恢复原主视觉图 `assets/konggu-readme-hero.png`、居中介绍和四个徽章。导航与 IPD 图表保留在主视觉之后，图片本身未修改。
