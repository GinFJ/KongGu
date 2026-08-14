from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_main_flow_uses_user_task_language() -> None:
    main = _source("src/main.ts")

    assert ">生成空课表<" in main
    assert ">导出空课表<" in main
    assert 'tabButton("issues", "待处理"' in main
    assert 'tabButton("review", "原文核对"' in main
    for internal_copy in (
        "排班复核工作台",
        ">开始解析与质检<",
        ">导出可信 Excel<",
        ">问题门禁",
        ">交互周课表",
        ">审计轨迹<",
        "绿色 · 可导出",
        "黄色 · 待人工确认",
        "红色 · 阻止导出",
        "`签名 ${",
    ):
        assert internal_copy not in main


def test_startup_repairs_local_resources_automatically() -> None:
    main = _source("src/main.ts")
    boot = main.split("async function boot()", 1)[1].split("async function chooseFiles", 1)[0]

    assert 'client.request<{ ok: boolean; status: ResourceStatus; copied: string[] }>("resources.repair")' in boot
    assert "const resources = resourceRepair.status;" in boot
    assert "本地识别资源未能自动准备，请点击修复" in boot


def test_blocking_issues_do_not_render_confirm_only_actions() -> None:
    main = _source("src/main.ts")
    review = _source("src/reviewView.ts")

    assert "${issueAction(issue)}" in main
    assert "function issueAction(issue: ParseResult[\"issues\"][number])" in main
    assert "if (issue.severity === \"error\")" in main
    assert "请先补充或修正课表，再重新生成空课表。" in main
    assert "${issueAction(issue)}" in review
    assert "function issueAction(issue: ParseIssue)" in review
    assert "if (issue.severity === \"error\")" in review
    assert "请先补充或修正课表，再重新生成空课表。" in review


def test_welcome_copy_uses_confirmed_slogan_and_concrete_language() -> None:
    main = _source("src/main.ts")

    assert "<h1>青心如禾，向阳而生</h1>" in main
    assert "把大家的课表放在一起，看看什么时候都有空。" in main
    for rejected_copy in (
        "多人课表协作工具",
        "课表只在这台电脑处理",
        "从共同空课时间出发",
        "找到可以发生的时间",
        "今天也和阿谷一起",
    ):
        assert rejected_copy not in main


def test_brand_header_keeps_only_green_zhong_song_slogan() -> None:
    main = _source("src/main.ts")
    styles = _source("src/styles.css")

    assert "青禾计划 · 空谷" not in main
    assert 'color: var(--green); font: 700 25px/1.1 "STZhongsong", "华文中宋"' in styles


def test_pdf_review_exposes_scroll_and_clear_file_switch_state() -> None:
    review = _source("src/reviewView.ts")
    styles = _source("src/styles.css")

    assert "滚轮浏览 · Ctrl+滚轮缩放" in review
    assert 'addEventListener("wheel", handlePdfWheel' in review
    assert "reviewSourceMeta" in review
    assert ".reviewToolbar select option:checked" in styles
    assert ".reviewInspector { min-width: 0; min-height: 0; overflow: auto" in styles


def test_result_tables_hide_internal_field_names() -> None:
    main = _source("src/main.ts")

    for label in ("成员", "部门", "职务", "中方课表", "英方课表", "有课时段", "检查结果"):
        assert f'label: "{label}"' in main
    assert 'label: "member_key"' not in main
    assert 'label: "course_block_count"' not in main


def test_review_view_hides_engine_metadata_and_issue_codes() -> None:
    review = _source("src/reviewView.ts")

    assert "文件识别情况" in review
    assert "修正课程时间" in review
    assert "分类置信度" not in review
    assert "engineLabel(" not in review
    assert "escapeHtml(issue.code)" not in review


def test_progress_messages_do_not_expose_quality_gate_language() -> None:
    workflow = _source("app/services/desktop_workflow.py")
    jobs = _source("app/services/job_service.py")

    assert "空课表已生成，可以导出" in workflow
    assert "空课表已生成，可以导出" in jobs
    assert "解析与质量门禁已通过" not in workflow
    assert "任务完成并通过质量门禁" not in jobs
