import { open, save } from "@tauri-apps/plugin-dialog";
import type { Calendar } from "@fullcalendar/core";
import wordmarkUrl from "../assets/konggu-wordmark.png";
import aguSplashUrl from "../assets/brand/agu/agu-splash-scene-1280x720.webp";
import { renderAguMotion } from "./aguMotion";
import { mountCalendar } from "./calendarView";
import { mountReview, type ReviewController } from "./reviewView";
import { SidecarClient } from "./sidecarClient";
import { commitAfterExit, createFrameScheduler, onNextFrame } from "./uiMotion";
import type {
  CalendarSettings,
  JobResponse,
  ParseResult,
  ProgressEvent,
  ResourceStatus,
  ReviewPayload
} from "./types";
import "./styles.css";

type Tab = "availability" | "members" | "files" | "issues" | "review" | "calendar";

type AppState = {
  files: string[];
  resourceStatus: ResourceStatus | null;
  calendarSettings: CalendarSettings;
  result: ParseResult | null;
  review: ReviewPayload | null;
  activeTab: Tab;
  busy: boolean;
  jobId: string;
  statusText: string;
  stage: string;
  failedFileCount: number;
  progress: { current: number; total: number };
  logs: string[];
};

const client = new SidecarClient();
const state: AppState = {
  files: [],
  resourceStatus: null,
  calendarSettings: { semester_start_date: "2026-03-02", teaching_weeks: 18 },
  result: null,
  review: null,
  activeTab: "availability",
  busy: false,
  jobId: "",
  statusText: "正在准备本地识别功能",
  stage: "boot",
  failedFileCount: 0,
  progress: { current: 0, total: 0 },
  logs: []
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("App root not found.");
const appRoot: HTMLDivElement = app;
let calendar: Calendar | null = null;
let calendarKey = "";
let calendarViewDate: Date | null = null;
let reviewController: ReviewController | null = null;
let reviewMount: Promise<void> | null = null;
let reviewPayload: ReviewPayload | null = null;
let availabilityPage = 0;
let shellMounted = false;
let renderedResult: ParseResult | null | undefined;
let runAguMode = "";
let completionPlayedFor = "";
let richViewGeneration = 0;
let cancelRequestedJobId = "";
let completionProbeJobId = "";
let completedJobId = "";
let reviewReloading = false;
let savedCalendarSettings: CalendarSettings = { ...state.calendarSettings };
const renderedHtmlCache = new WeakMap<HTMLElement, string>();

const tabs: Tab[] = ["availability", "members", "files", "issues", "review", "calendar"];

client.onEvent((event) => {
  if (event.event === "sidecar.fatal") {
    state.statusText = `本地识别功能启动失败：${friendlyMessage(event.message)}`;
    state.stage = "failed";
    state.busy = false;
    render();
    return;
  }
  // 没有进行中的任务时不接受任何进度事件；已经收尾的任务也不再改写阶段与状态文字。
  if (!state.jobId || event.job_id !== state.jobId) return;
  if (completedJobId === event.job_id) return;
  const cancellationPending = cancelRequestedJobId !== "" && event.job_id === cancelRequestedJobId && event.stage !== "cancelled";
  state.stage = cancellationPending ? "cancelling" : event.stage;
  state.progress = { current: event.current, total: event.total };
  state.statusText = cancellationPending ? "正在完成当前图片页后停止" : friendlyMessage(event.message);
  log(state.statusText, false);
  render();
  if (event.stage === "review" || event.stage === "completed") {
    void settleCompletedJob(event.job_id);
  }
});

async function boot() {
  render();
  try {
    await Promise.all([client.start(), delay(900)]);
    const [resourceRepair, settings] = await Promise.all([
      client.request<{ ok: boolean; status: ResourceStatus; copied: string[] }>("resources.repair"),
      client.request<{ ok: boolean; settings: CalendarSettings }>("settings.calendar.get")
    ]);
    const resources = resourceRepair.status;
    state.resourceStatus = resources;
    state.calendarSettings = settings.settings;
    savedCalendarSettings = { ...settings.settings };
    state.statusText = resources.ready ? "准备完成，可以导入课表" : "本地识别资源未能自动准备，请点击修复";
    state.stage = "ready";
  } catch (error) {
    state.stage = "failed";
    state.statusText = formatError(error);
  }
  render();
}

function resetForNewInput(message: string) {
  // 文件列表变化后旧任务的所有引用与阶段都必须复位，否则界面会残留
  // “处理完成/处理失败”却没有任何结果。
  state.result = null;
  state.review = null;
  state.jobId = "";
  state.failedFileCount = 0;
  state.progress = { current: 0, total: 0 };
  state.stage = "ready";
  state.statusText = message;
  availabilityPage = 0;
  cancelRequestedJobId = "";
  completionProbeJobId = "";
  completedJobId = "";
  render();
}

async function chooseFiles() {
  if (state.busy) return;
  const selected = await open({ multiple: true, filters: [{ name: "PDF", extensions: ["pdf"] }] });
  const paths = Array.isArray(selected) ? selected : typeof selected === "string" ? [selected] : [];
  // 取消选择对话框时保留当前结果与任务引用，不能因为空选择把已生成的结果清掉。
  if (!paths.length) return;
  state.files = Array.from(new Set([...state.files, ...paths]));
  resetForNewInput(state.files.length ? `已选择 ${state.files.length} 份课表` : "请选择课表 PDF");
}

async function parseSchedules() {
  if (state.busy) return;
  if (!state.files.length) return setStatus("请先选择课表 PDF。");
  const invalid = state.files.filter((path) => !inferKind(path));
  if (invalid.length) return setStatus(`${invalid.length} 个文件无法判断中方/英方，请先按规则改名。`);
  state.busy = true;
  state.result = null;
  state.failedFileCount = 0;
  state.review = null;
  // 新任务开始前清掉旧任务引用：启动请求失败时不能留下可被误重试的旧 jobId。
  state.jobId = "";
  cancelRequestedJobId = "";
  completionProbeJobId = "";
  completedJobId = "";
  state.stage = "queued";
  state.statusText = "正在准备处理课表";
  render();
  try {
    await saveSettings(false);
    const started = await client.request<{ ok: boolean; job_id: string }>("job.start", { paths: state.files });
    state.jobId = started.job_id;
    log(`开始处理 ${state.files.length} 份课表`);
    await waitForJob(started.job_id);
  } catch (error) {
    state.busy = false;
    state.stage = "failed";
    state.statusText = `解析失败：${formatError(error)}`;
    log(state.statusText);
    render();
  }
}

async function settleCompletedJob(jobId: string) {
  // 终态事件必须能收尾，即使轮询循环已经退出（busy=false）：否则任务完成但结果永远不载入。
  if (!state.jobId || state.jobId !== jobId || completionProbeJobId === jobId || completedJobId === jobId) return;
  completionProbeJobId = jobId;
  try {
    const response = await client.request<JobResponse>("job.get", { job_id: jobId }, 60_000);
    if (state.jobId !== jobId || response.job.status !== "completed") return;
    await finishCompletedJob(jobId, response.job);
  } catch {
    // The polling loop remains the fallback for transient sidecar errors.
  } finally {
    if (completionProbeJobId === jobId) completionProbeJobId = "";
  }
}

async function finishCompletedJob(jobId: string, job: JobResponse["job"]) {
  if (state.jobId !== jobId || completedJobId === jobId) return;
  completedJobId = jobId;
  cancelRequestedJobId = "";
  state.progress = { current: job.current, total: job.total };
  state.result = job.output || null;
  state.failedFileCount = job.files.filter((file) => file.status === "failed").length;
  state.busy = false;
  state.stage = state.result?.can_export ? "completed" : "review";
  // 检测周数只用于本次展示；不回写学期设置。回写会让“导出周数”被检测值收缩，
  // 并改变解析签名，使上一轮已做的人工修正失效。
  state.activeTab = state.result?.can_export ? "availability" : "issues";
  state.statusText = state.result
    ? state.result.can_export
      ? "空课表已生成，可以导出"
      : resultActionText(state.result.quality_state)
    : "课表已处理，但结果暂时无法载入，请重新生成。";
  render();

  if (!state.result) {
    log(state.statusText);
    return;
  }

  try {
    const review = await client.request<ReviewPayload>("review.get", { job_id: jobId }, 120_000);
    // 等待原文期间用户可能已经换了文件或开始了新任务，旧任务的原文不能覆盖新选择。
    if (state.jobId !== jobId) return;
    state.review = review;
  } catch (error) {
    if (state.jobId !== jobId) return;
    state.statusText = `课表已处理，但原文核对暂时无法载入：${formatError(error)}`;
    showReviewLoadFailure(formatError(error));
  }
  log(state.statusText);
  render();
}

function showReviewLoadFailure(message: string) {
  setHtmlIfChanged(getPanel("review"), `<div class="emptyPanel"><b>课表原文暂时无法载入</b><span>${escapeHtml(message)}</span><button type="button" data-reload-review>重新载入原文</button></div>`);
}

async function reloadReview() {
  const jobId = state.jobId;
  if (!jobId || reviewReloading) return;
  reviewReloading = true;
  state.statusText = "正在重新载入课表原文";
  render();
  try {
    const review = await client.request<ReviewPayload>("review.get", { job_id: jobId }, 120_000);
    if (state.jobId !== jobId) return;
    state.review = review;
    state.statusText = "课表原文已载入";
  } catch (error) {
    if (state.jobId !== jobId) return;
    state.statusText = `课表原文仍无法载入：${formatError(error)}`;
    showReviewLoadFailure(formatError(error));
  } finally {
    reviewReloading = false;
    render();
  }
}

async function waitForJob(jobId: string) {
  let consecutiveFailures = 0;
  while (state.jobId === jobId && completedJobId !== jobId) {
    let response: JobResponse;
    try {
      response = await client.request<JobResponse>("job.get", { job_id: jobId }, 60_000);
      consecutiveFailures = 0;
    } catch (error) {
      consecutiveFailures += 1;
      // 单次轮询失败（识别功能重启、瞬时写入失败、一次超时）不能终止整个等待：
      // 任务可能仍在运行，结果也仍会回来。连续失败才判定为中断。
      if (consecutiveFailures >= 3) throw error;
      state.statusText = `正在重新连接本地识别功能（第 ${consecutiveFailures} 次重试）`;
      render();
      await delay(1000 * consecutiveFailures);
      continue;
    }
    const job = response.job;
    if (state.jobId !== jobId) return;
    state.progress = { current: job.current, total: job.total };
    state.failedFileCount = job.files.filter((file) => file.status === "failed").length;
    if (job.status === "completed") {
      await finishCompletedJob(jobId, job);
      return;
    }
    if (["failed", "cancelled", "interrupted"].includes(job.status)) {
      cancelRequestedJobId = "";
      state.busy = false;
      state.stage = job.status;
      state.statusText = friendlyMessage(job.error || `处理状态：${stageLabel(job.status)}`);
      log(state.statusText);
      render();
      return;
    }
    await delay(650);
  }
}

async function cancelJob() {
  if (!state.jobId || !state.busy) return;
  const jobId = state.jobId;
  if (cancelRequestedJobId === jobId) return;
  cancelRequestedJobId = jobId;
  state.statusText = "正在完成当前图片页后停止";
  state.stage = "cancelling";
  render();
  window.setTimeout(async () => {
    if (state.busy && state.jobId === jobId && cancelRequestedJobId === jobId) {
      state.statusText = "停止响应较慢，正在恢复识别功能";
      render();
      let restartError = "";
      try {
        await client.forceRestart();
      } catch (error) {
        restartError = formatError(error);
      }
      if (!restartError) {
        // 重启后的 worker 不再持有本批文件路径（隐私设计），旧任务无法续跑。
        // 直接清掉任务引用，引导用户用保留下来的文件列表重新生成，避免留下必失败的重试按钮。
        resetJobAfterRestart("本地识别功能已重启，原来的处理无法续跑；文件列表已保留，请点击“生成空课表”重新处理。");
        return;
      }
      state.busy = false;
      state.stage = "interrupted";
      state.statusText = `处理已中断；本地识别功能需要重新启动：${restartError}`;
      cancelRequestedJobId = "";
      render();
    }
  }, 10_000);
  await client.request("job.cancel", { job_id: jobId }).catch(() => undefined);
}

function resetJobAfterRestart(message: string) {
  state.jobId = "";
  state.result = null;
  state.review = null;
  state.failedFileCount = 0;
  state.progress = { current: 0, total: 0 };
  state.busy = false;
  state.stage = "ready";
  state.statusText = message;
  cancelRequestedJobId = "";
  completionProbeJobId = "";
  completedJobId = "";
  reviewPayload = null;
  log(message);
  render();
}

async function retryFailed() {
  if (!state.jobId || state.busy) return;
  const previousJobId = state.jobId;
  const previousStage = state.stage;
  state.busy = true;
  state.stage = "queued";
  state.statusText = "正在重新处理本批课表";
  cancelRequestedJobId = "";
  completionProbeJobId = "";
  completedJobId = "";
  render();
  try {
    const started = await client.request<{ ok: boolean; job_id: string }>("job.retry_failed", {
      job_id: previousJobId
    });
    state.jobId = started.job_id;
    state.result = null;
    state.review = null;
    state.failedFileCount = 0;
    await waitForJob(started.job_id);
  } catch (error) {
    const message = formatError(error);
    if (message.includes("没有保留本批课表的文件路径")) {
      resetJobAfterRestart("本地识别功能重启后无法自动重试；文件列表已保留，请点击“生成空课表”重新处理。");
      return;
    }
    state.busy = false;
    state.stage = state.jobId === previousJobId ? previousStage : "failed";
    setStatus(message);
  }
}

async function exportExcel() {
  if (state.busy) return;
  const result = state.result;
  if (!result?.result_ref) return setStatus("请先生成空课表。");
  if (!result.can_export) {
    state.activeTab = "issues";
    return setStatus("当前结果还不能导出，请先核对或修正“待处理”中的内容。");
  }
  const target = await save({
    defaultPath: "Konggu_availability.xlsx",
    filters: [{ name: "Excel", extensions: ["xlsx"] }]
  });
  if (!target) return;
  // 保存对话框期间结果可能已被新任务替换，导出必须绑定用户刚才看到的那份结果。
  if (state.result !== result) return setStatus("结果已更新，请重新确认后再导出。");
  state.busy = true;
  state.statusText = "正在生成 Excel 文件";
  render();
  try {
    const response = await client.request<{ ok: boolean; path: string }>("exports.excel", {
      result_ref: result.result_ref,
      target_path: target,
      mode: "classic",
      // 用已保存的学期设置：输入框改动未点保存时，导出不能提前套用未生效的周数。
      export_week_count: savedCalendarSettings.teaching_weeks
    });
    state.statusText = `空课表已导出：${response.path}`;
    state.stage = "exported";
    log(state.statusText);
  } catch (error) {
    state.statusText = `暂时无法导出：${formatError(error)}`;
    state.activeTab = "issues";
  } finally {
    state.busy = false;
    render();
  }
}

async function repairResources() {
  if (state.busy) return;
  state.busy = true;
  setStatus("正在检查并修复本地识别功能");
  try {
    const response = await client.request<{ ok: boolean; status: ResourceStatus; copied: string[]; failed?: unknown[] }>("resources.repair");
    state.resourceStatus = response.status;
    const failed = Array.isArray(response.failed) ? response.failed.length : 0;
    state.statusText = response.status.ready
      ? "识别功能已恢复"
      : failed
        ? `识别功能仍有 ${failed} 项资源修复失败，请重试或重新安装空谷`
        : resourceRepairStatus(response.status);
  } catch (error) {
    state.statusText = formatError(error);
  } finally {
    state.busy = false;
    render();
  }
}

async function clearLocalData() {
  if (state.busy) return;
  if (!window.confirm("这会删除空谷保存的结果、人工修正和本地解析缓存，原始 PDF 不会被删除。继续吗？")) return;
  state.busy = true;
  setStatus("正在清理本机记录");
  try {
    await client.request<{ ok: boolean }>("data.clear");
    state.files = [];
    state.result = null;
    state.review = null;
    state.jobId = "";
    state.logs = [];
    state.stage = "ready";
    state.statusText = "本机记录和解析缓存已清理";
  } catch (error) {
    state.statusText = `清理失败：${formatError(error)}`;
  } finally {
    state.busy = false;
    render();
  }
}

async function saveSettings(showFeedback = true) {
  const next = { ...state.calendarSettings };
  const wouldChange = savedCalendarSettings.semester_start_date !== next.semester_start_date
    || savedCalendarSettings.teaching_weeks !== next.teaching_weeks;
  if (wouldChange && showFeedback) {
    const hasWork = Boolean(state.result || state.review);
    const warning = hasWork
      ? "学期设置变化会让已生成的结果和已做的人工修正失效，需要重新生成空课表。继续保存吗？"
      : "学期设置保存后，下次生成会使用新设置。继续吗？";
    if (!window.confirm(warning)) {
      state.calendarSettings = { ...savedCalendarSettings };
      render();
      return;
    }
  }
  try {
    const response = await client.request<{ ok: boolean; settings: CalendarSettings }>("settings.calendar.save", {
      settings: next
    });
    state.calendarSettings = response.settings;
    const changed = savedCalendarSettings.semester_start_date !== response.settings.semester_start_date
      || savedCalendarSettings.teaching_weeks !== response.settings.teaching_weeks;
    savedCalendarSettings = { ...response.settings };
    if (changed) {
      state.result = null;
      state.review = null;
      state.jobId = "";
      state.failedFileCount = 0;
      state.activeTab = "availability";
      if (!state.busy) state.stage = "ready";
      completedJobId = "";
    }
    if (showFeedback) {
      setStatus(
        changed
          ? "学期设置已保存；旧结果和原人工修正已失效，请重新生成空课表。"
          : "学期设置已保存。"
      );
    }
  } catch (error) {
    state.calendarSettings = { ...savedCalendarSettings };
    setStatus(`学期设置保存失败：${formatError(error)}`);
    throw error;
  }
}

async function applyReview(blockId: string, field: string, value: unknown, reason: string) {
  const jobId = state.jobId;
  if (!jobId) return;
  if (!reason.trim()) throw new Error("请填写修正原因。");
  try {
    const review = await client.request<ReviewPayload>("review.apply", {
      job_id: jobId,
      block_id: blockId,
      field,
      new_value: value,
      reason,
      operator_id: "本机用户"
    });
    // 修正响应只回填发起它的那次任务，避免切文件后把旧任务的复核结果写进新任务。
    if (state.jobId !== jobId) return;
    state.review = review;
    try {
      await refreshJobOutput();
    } catch (error) {
      // 修正已经写入本地记录，刷新失败只是界面暂时落后；不能谎报“没有保存”，
      // 也不能让周视图的拖拽把已经保存的修正回滚掉。
      setStatus(`修正已保存，但结果刷新失败：${formatError(error)}`);
    }
  } catch (error) {
    setStatus(`修正没有保存：${formatError(error)}`);
    throw error;
  }
}

async function confirmIssue(issueId: string) {
  const jobId = state.jobId;
  if (!jobId) return;
  try {
    const review = await client.request<ReviewPayload>("review.confirm", {
      job_id: jobId,
      issue_id: issueId
    });
    if (state.jobId !== jobId) return;
    state.review = review;
    try {
      await refreshJobOutput();
    } catch (error) {
      setStatus(`已确认，但结果刷新失败：${formatError(error)}`);
    }
  } catch (error) {
    setStatus(`暂时无法确认：${formatError(error)}`);
  }
}

async function refreshJobOutput() {
  const jobId = state.jobId;
  if (!jobId) return;
  const response = await client.request<JobResponse>("job.get", { job_id: jobId }, 60_000);
  if (state.jobId !== jobId) return;
  state.result = response.job.output || state.result;
  if (state.result) {
    state.stage = state.result.can_export ? "completed" : "review";
    state.statusText = resultActionText(state.result.quality_state);
  }
  render();
}

const render = createFrameScheduler(() => {
  if (!shellMounted) mountShell();
  updateShell();
});

function mountShell() {
  appRoot.innerHTML = `
    <main class="shell">
      <section id="launchScreen" class="launchScreen is-visible" aria-live="polite" aria-label="空谷正在启动">
        <img class="launchScene" src="${aguSplashUrl}" alt="阿谷启动场景" />
        <div class="launchCopy">
          <img src="${wordmarkUrl}" alt="空谷" />
          <p>青心如禾，向阳而生</p>
          <span id="launchStatus"></span>
          <i aria-hidden="true"></i>
        </div>
      </section>
      <aside class="rail">
        <div class="brand"><img src="${wordmarkUrl}" alt="空谷" /></div>
        <nav class="primaryActions" aria-label="主要操作">
          <button id="chooseFiles" class="primary">＋ 导入课表</button>
          <button id="parseSchedules">生成空课表</button>
          <button id="cancelJob" class="danger" hidden>停止处理</button>
          <button id="retryFailed">重新处理本批课表</button>
          <button id="exportExcel">导出空课表</button>
        </nav>
        <section class="railSection">
          <div class="sectionLabel">学期设置</div>
          <label>第一周开始日期<input id="semesterStart" type="date" /></label>
          <label>教学周数<input id="teachingWeeks" type="number" min="1" max="30" /></label>
          <button id="saveSettings">保存设置</button>
        </section>
        <section class="railSection resourceState">
          <div class="sectionLabel">本地识别</div>
          <strong id="resourceTitle" class="warning">正在检查</strong>
          <small id="resourceDetail">正在确认图片课表识别能力</small>
          <button id="repairResources">修复识别功能</button>
          <button id="clearLocalData" class="quiet">清除本机记录</button>
        </section>
      </aside>
      <section class="workbench">
        <header class="masthead">
          <div>
            <h1>青心如禾，向阳而生</h1>
            <p id="statusText" aria-live="polite"></p>
          </div>
          <div id="qualitySeal" class="qualitySeal pending">
            <span id="qualityTitle">等待生成结果</span>
            <small id="qualityDetail">导入课表后查看</small>
          </div>
        </header>
        <section class="runStrip" aria-label="处理进度">
          <div class="runMeta"><b id="stageLabel"></b><span id="runHint"></span></div>
          <div id="progressTrack" class="progressTrack" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i id="progressFill"></i></div>
          <div class="runCounts"><strong id="fileCount">0</strong><span>文件</span><strong id="memberCount">0</strong><span>成员</span><strong id="issueCount">0</strong><span>待处理</span></div>
          <div id="runAguSlot"></div>
        </section>
        <section class="sourceRibbon">
          <div class="sourceHeader">
            <div class="sourceHeaderCopy"><b>已选课表</b><span id="sourceSummary">尚未选择文件</span></div>
            <div class="sourceHeaderActions"><small id="sourceViewHint">文件列表可独立滚动</small><button id="clearFiles">清空</button></div>
          </div>
          <div id="sourceChips" class="sourceChips" role="list" aria-label="已选择的课表文件"></div>
        </section>
        <section class="contentCard">
          <div class="tabs" role="tablist" aria-label="结果视图">
            ${tabButton("availability", "共同空闲")}
            ${tabButton("members", "成员课表", 0)}
            ${tabButton("files", "文件检查", 0)}
            ${tabButton("issues", "待处理", 0)}
            ${tabButton("review", "原文核对")}
            ${tabButton("calendar", "课程周视图")}
            <i class="tabIndicator" aria-hidden="true"></i>
          </div>
          <div class="tabBody" id="tabBody">
            ${tabs.map((tab) => `<section id="panel-${tab}" class="tabPanel" data-panel="${tab}" role="tabpanel"></section>`).join("")}
          </div>
        </section>
        <footer class="activityLog">
          <b>最近操作</b>
          <div id="activityItems"><span>等待操作。</span></div>
        </footer>
      </section>
    </main>
  `;
  shellMounted = true;
  bindActions();
}

function updateShell() {
  const result = state.result;
  const quality = result?.quality_state || "pending";
  const invalidCount = state.files.filter((path) => !inferKind(path)).length;
  const canParse = state.files.length > 0 && invalidCount === 0 && !state.busy;
  const progressPercent = state.progress.total
    ? Math.min(100, Math.round((state.progress.current / state.progress.total) * 100))
    : state.busy ? 8 : 0;

  const launch = getElement<HTMLElement>("#launchScreen");
  launch.classList.toggle("is-visible", state.stage === "boot");
  launch.setAttribute("aria-hidden", String(state.stage !== "boot"));
  setText("#launchStatus", state.statusText);

  setDisabled("#chooseFiles", state.busy);
  setDisabled("#parseSchedules", !canParse);
  const cancel = getElement<HTMLButtonElement>("#cancelJob");
  const retry = getElement<HTMLButtonElement>("#retryFailed");
  cancel.hidden = !state.busy || !state.jobId;
  const canRetry = !state.busy && Boolean(state.jobId) && (
    state.failedFileCount > 0 || ["failed", "interrupted", "cancelled"].includes(state.stage)
  );
  retry.hidden = !canRetry;
  retry.disabled = !state.jobId;
  setDisabled("#exportExcel", !result?.can_export || state.busy);
  setDisabled("#saveSettings", state.busy);
  setDisabled("#repairResources", state.busy);
  setDisabled("#clearLocalData", state.busy);
  setDisabled("#clearFiles", state.busy || !state.files.length);
  setInputValue("#semesterStart", state.calendarSettings.semester_start_date);
  setInputValue("#teachingWeeks", String(state.calendarSettings.teaching_weeks));

  const resourceTitle = getElement<HTMLElement>("#resourceTitle");
  resourceTitle.className = state.resourceStatus?.ready ? "good" : "warning";
  resourceTitle.textContent = state.resourceStatus?.ready ? "已就绪" : "需要修复";
  setText("#resourceDetail", resourceDetail(state.resourceStatus));
  setText("#statusText", state.statusText);

  const seal = getElement<HTMLElement>("#qualitySeal");
  seal.className = `qualitySeal ${quality}`;
  setText("#qualityTitle", result ? resultStatusLabel(quality) : "等待生成结果");
  setText("#qualityDetail", result ? resultStatusDetail(result) : "导入课表后查看");
  setText("#stageLabel", stageLabel(state.stage));
  setText("#runHint", runHint(result));
  setText("#fileCount", String(result?.summary.pdf_count || state.files.length));
  setText("#memberCount", String(result?.summary.member_count || 0));
  setText("#issueCount", String(result?.issues.filter((issue) => !issue.confirmed).length || 0));
  const progress = getElement<HTMLElement>("#progressTrack");
  progress.classList.toggle("indeterminate", state.busy && !state.progress.total);
  progress.setAttribute("aria-valuenow", String(progressPercent));
  getElement<HTMLElement>("#progressFill").style.setProperty("--progress-scale", String(progressPercent / 100));

  updateRunAgu(result);
  updateSources(invalidCount);
  updateTabs(result);
  updateActivity();
  updatePanels(result);
}

function bindActions() {
  document.querySelector("#chooseFiles")?.addEventListener("click", chooseFiles);
  document.querySelector("#parseSchedules")?.addEventListener("click", parseSchedules);
  document.querySelector("#cancelJob")?.addEventListener("click", cancelJob);
  document.querySelector("#retryFailed")?.addEventListener("click", retryFailed);
  document.querySelector("#exportExcel")?.addEventListener("click", exportExcel);
  document.querySelector("#repairResources")?.addEventListener("click", repairResources);
  document.querySelector("#clearLocalData")?.addEventListener("click", clearLocalData);
  document.querySelector("#saveSettings")?.addEventListener("click", () => {
    if (state.busy) return;
    void saveSettings().catch(() => undefined);
  });
  document.querySelector("#clearFiles")?.addEventListener("click", () => {
    state.files = [];
    resetForNewInput("已清空文件列表，请重新导入课表");
  });
  document.querySelector<HTMLInputElement>("#semesterStart")?.addEventListener("change", (event) => {
    state.calendarSettings.semester_start_date = (event.target as HTMLInputElement).value;
  });
  document.querySelector<HTMLInputElement>("#teachingWeeks")?.addEventListener("change", (event) => {
    state.calendarSettings.teaching_weeks = Number((event.target as HTMLInputElement).value);
  });
  appRoot.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    const tab = target.closest<HTMLButtonElement>("[data-tab]");
    if (tab) {
      state.activeTab = tab.dataset.tab as Tab;
      render();
      return;
    }
    const remove = target.closest<HTMLButtonElement>("[data-remove]");
    if (remove) {
      // 处理中移除文件会清空 jobId，但任务仍在跑；此时 UI 会同时失去取消和重试入口。
      if (state.busy) {
        setStatus("正在处理课表，完成后再调整文件。");
        return;
      }
      const chip = remove.closest<HTMLElement>(".sourceChip");
      const commit = () => {
        // 延迟提交期间用户可能已开始生成：此时改文件列表会清掉正在跑的任务引用。
        if (state.busy) {
          setStatus("课表正在处理，完成后再调整文件列表。");
          return;
        }
        state.files = state.files.filter((path) => path !== remove.dataset.remove);
        resetForNewInput(state.files.length ? `已选择 ${state.files.length} 份课表` : "请选择课表 PDF");
      };
      chip ? commitAfterExit(chip, commit) : commit();
      return;
    }
    const confirm = target.closest<HTMLButtonElement>("[data-confirm-inline]");
    if (confirm) {
      void confirmIssue(confirm.dataset.confirmInline || "");
      return;
    }
    const reload = target.closest<HTMLButtonElement>("[data-reload-review]");
    if (reload) {
      void reloadReview();
      return;
    }
    const pager = target.closest<HTMLButtonElement>("[data-availability-page]");
    if (pager) {
      const previousPage = availabilityPage;
      const nextPage = Math.max(0, Number(pager.dataset.availabilityPage || 0));
      const goForward = nextPage > previousPage;
      const pagerGroup = pager.closest<HTMLElement>(".tablePager");
      const groups = Array.from(getPanel("availability").querySelectorAll<HTMLElement>(".tablePager"));
      const groupIndex = pagerGroup ? Math.max(0, groups.indexOf(pagerGroup)) : 0;
      availabilityPage = nextPage;
      if (state.result) setHtmlIfChanged(getPanel("availability"), renderPanel("availability", state.result));
      // 分页会整体替换面板内容；焦点要回到同一组、同一方向的按钮，否则键盘用户会掉到文档开头。
      onNextFrame(() => {
        const panel = getPanel("availability");
        const group = panel.querySelectorAll<HTMLElement>(".tablePager")[groupIndex] || panel.querySelector<HTMLElement>(".tablePager");
        const sameButton = group?.querySelector<HTMLButtonElement>(`[data-availability-page="${nextPage + (goForward ? 1 : -1)}"]:not(:disabled)`);
        const fallback = group?.querySelector<HTMLButtonElement>("button:not(:disabled)");
        (sameButton || fallback)?.focus();
      });
      return;
    }
  });
  appRoot.addEventListener("keydown", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-tab]");
    if (!button || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = tabs.indexOf(button.dataset.tab as Tab);
    const next = event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    state.activeTab = tabs[next];
    render();
    onNextFrame(() => document.querySelector<HTMLButtonElement>(`[data-tab="${state.activeTab}"]`)?.focus());
  });
  window.addEventListener("resize", render);
}

function updateRunAgu(result: ParseResult | null) {
  const slot = getElement<HTMLElement>("#runAguSlot");
  const nextMode = state.busy
    ? "working"
    : result?.can_export && ["completed", "exported"].includes(state.stage)
      ? "complete"
      : "";
  if (nextMode === runAguMode) return;

  if (!nextMode) {
    slot.replaceChildren();
    runAguMode = "";
    return;
  }

  if (nextMode === "working") {
    slot.innerHTML = renderAguMotion("working", true);
  } else {
    const completionKey = state.jobId || result?.result_ref || "result";
    slot.innerHTML = renderAguMotion("complete", true);
    if (completionPlayedFor === completionKey) {
      slot.querySelector(".aguMotion")?.classList.add("aguMotion--settled");
    } else {
      completionPlayedFor = completionKey;
    }
  }
  runAguMode = nextMode;
}

let sourceKey: string | null = null;
function updateSources(invalidCount: number) {
  setText("#sourceSummary", state.files.length
    ? `${state.files.length} 份 PDF · ${invalidCount ? `${invalidCount} 份无法判断课表类型` : "文件名检查通过"}`
    : "尚未选择文件");
  setText("#sourceViewHint", state.files.length > 6 ? "列表可独立滚动" : "导入后显示文件列表");
  const nextKey = state.files.join("\u0000");
  if (nextKey === sourceKey) return;
  sourceKey = nextKey;
  getElement<HTMLElement>("#sourceChips").innerHTML = state.files.length
    ? state.files.map(fileChip).join("")
    : `<span class="emptySource">请同时导入每位成员的中方和英方课表，文件名需写明成员、部门、职务和课表类型。</span>`;
}

function updateTabs(result: ParseResult | null) {
  const counts: Partial<Record<Tab, number>> = {
    members: result?.members.length || 0,
    files: result?.details.length || 0,
    issues: unresolvedIssueCount(result)
  };
  const tabList = getElement<HTMLElement>(".tabs");
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) => {
    const tab = button.dataset.tab as Tab;
    const active = tab === state.activeTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    const count = button.querySelector<HTMLElement>("em");
    if (count && typeof counts[tab] === "number") {
      count.textContent = String(counts[tab]);
      count.hidden = counts[tab] === 0 && tab !== "issues";
    }
  });
  const activeButton = tabList.querySelector<HTMLButtonElement>(`[data-tab="${state.activeTab}"]`);
  if (activeButton) {
    tabList.style.setProperty("--indicator-x", `${activeButton.offsetLeft}px`);
    tabList.style.setProperty("--indicator-width", `${activeButton.offsetWidth}px`);
  }
}

function updateActivity() {
  const html = state.logs.slice(0, 3).map((item) => `<span>${escapeHtml(item)}</span>`).join("") || "<span>等待操作。</span>";
  setHtmlIfChanged(getElement<HTMLElement>("#activityItems"), html);
}

function updatePanels(result: ParseResult | null) {
  tabs.forEach((tab) => {
    const panel = getElement<HTMLElement>(`#panel-${tab}`);
    const active = state.activeTab === tab;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
    panel.setAttribute("aria-hidden", String(!active));
  });

  if (renderedResult !== result) {
    const previousResultRef = renderedResult?.result_ref || "";
    const refreshingSameResult = Boolean(result && previousResultRef === result.result_ref);
    // 同一结果内的修正刷新要保留各表格的滚动位置，只有换任务结果才回到顶部第一页。
    const scrollPositions = refreshingSameResult ? captureTableScroll() : null;
    renderedResult = result;
    if (!result || result.result_ref !== previousResultRef) availabilityPage = 0;
    if (!result) {
      resetRichViews();
      setHtmlIfChanged(getPanel("availability"), renderWelcome(state.stage !== "failed"));
      for (const tab of tabs.filter((item) => item !== "availability")) {
        setHtmlIfChanged(getPanel(tab), `<div class="emptyPanel"><b>先生成空课表</b><span>导入成员课表并完成处理后，可在这里查看${tabEmptyLabel(tab)}。</span></div>`);
      }
    } else {
      setHtmlIfChanged(getPanel("availability"), renderPanel("availability", result));
      setHtmlIfChanged(getPanel("members"), renderPanel("members", result));
      setHtmlIfChanged(getPanel("files"), renderPanel("files", result));
      setHtmlIfChanged(getPanel("issues"), renderPanel("issues", result));
      if (!reviewController && !reviewMount) setHtmlIfChanged(getPanel("review"), `<div class="loadingPanel">打开后载入课表原文…</div>`);
      if (!calendar) setHtmlIfChanged(getPanel("calendar"), `<div class="calendarHost"></div>`);
      const nextCalendarKey = calendarSignature(result);
      if (calendar && calendarKey !== nextCalendarKey) {
        calendarViewDate = calendar.getDate();
        calendar.destroy();
        calendar = null;
        calendarKey = "";
        getPanel("calendar").innerHTML = `<div class="calendarHost"></div>`;
      }
    }
    if (scrollPositions) restoreTableScroll(scrollPositions);
  }

  if (reviewController && state.review && reviewPayload !== state.review) {
    reviewPayload = state.review;
    void reviewController.update(state.review);
  }
  if (state.activeTab === "review" && state.review) ensureReview();
  if (state.activeTab === "calendar" && result) ensureCalendar(result);
}

function ensureReview() {
  if (reviewController || reviewMount || !state.review) return;
  const panel = getPanel("review");
  const generation = richViewGeneration;
  panel.innerHTML = `<div class="loadingPanel"><span class="loadingDot" aria-hidden="true"></span>正在载入课表原文…</div>`;
  const initialPayload = state.review;
  const currentMount = mountReview(panel, initialPayload, { apply: applyReview, confirm: confirmIssue })
    .then(async (controller) => {
      if (generation !== richViewGeneration) {
        controller.destroy();
        return;
      }
      reviewController = controller;
      reviewPayload = initialPayload;
      if (state.review && state.review !== initialPayload) {
        reviewPayload = state.review;
        await controller.update(state.review);
      }
    })
    .catch((error) => {
      if (generation !== richViewGeneration) return;
      panel.innerHTML = `<div class="emptyPanel"><b>课表原文暂时无法打开</b><span>${escapeHtml(formatError(error))}</span></div>`;
    })
    .finally(() => {
      if (reviewMount === currentMount) reviewMount = null;
    });
  reviewMount = currentMount;
}

function ensureCalendar(result: ParseResult) {
  const key = calendarSignature(result);
  if (calendar && calendarKey === key) {
    onNextFrame(() => calendar?.updateSize());
    return;
  }
  // 重建周视图时回到用户正在看的周，而不是又跳回第一门课所在周。
  const initialDate = calendar?.getDate() || calendarViewDate || undefined;
  calendar?.destroy();
  calendar = null;
  calendarKey = "";
  const host = getPanel("calendar").querySelector<HTMLElement>(".calendarHost");
  if (!host) return;
  calendar = mountCalendar(host, result.courses, state.calendarSettings, async (block, patch) => {
    for (const [field, value] of Object.entries(patch)) {
      await applyReview(block.block_id, field, value, "周课表拖拽修正");
    }
  }, initialDate, (message) => setStatus(message));
  calendarKey = key;
  onNextFrame(() => calendar?.updateSize());
}

function captureTableScroll() {
  const positions = new Map<Tab, number>();
  tabs.forEach((tab) => {
    const wrap = getPanel(tab).querySelector<HTMLElement>(".tableWrap");
    if (wrap) positions.set(tab, wrap.scrollTop);
  });
  return positions;
}

function restoreTableScroll(positions: Map<Tab, number>) {
  positions.forEach((value, tab) => {
    const wrap = getPanel(tab).querySelector<HTMLElement>(".tableWrap");
    if (wrap) wrap.scrollTop = value;
  });
}

function resetRichViews() {
  richViewGeneration += 1;
  reviewController?.destroy();
  reviewController = null;
  reviewMount = null;
  reviewPayload = null;
  calendar?.destroy();
  calendar = null;
  calendarKey = "";
  calendarViewDate = null;
}

function calendarSignature(result: ParseResult) {
  const courseKey = result.courses.map((course) => [
    course.block_id,
    course.week,
    course.weekday,
    course.periods.join(","),
    course.course || ""
  ].join(":")).join("|");
  return `${result.result_ref}|${courseKey}|${state.calendarSettings.semester_start_date}|${state.calendarSettings.teaching_weeks}`;
}

function getPanel(tab: Tab) {
  return getElement<HTMLElement>(`#panel-${tab}`);
}

function renderWelcome(showAgu = true) {
  return `<div class="emptyPanel welcomePanel">
    <div class="welcomeCopy">
      <span class="welcomeKicker">先导入课表</span>
      <b>把大家的课表放在一起，看看什么时候都有空。</b>
      <p>请导入同一批成员的中方和英方课表。空谷会整理每个人的上课时间，把不确定的内容列出来核对，然后生成共同空闲时段。</p>
      <div class="welcomeSteps"><span><i>1</i>导入课表</span><span><i>2</i>生成空课表</span><span><i>3</i>核对并导出</span></div>
    </div>
    ${showAgu ? renderAguMotion("welcome") : ""}
  </div>`;
}

function renderPanel(tab: Tab, result: ParseResult) {
  if (tab === "members") {
    return table(result.members, [
      { key: "member", label: "成员" },
      { key: "department", label: "部门" },
      { key: "role", label: "职务" },
      { key: "chinese_schedule", label: "中方课表" },
      { key: "english_schedule", label: "英方课表" },
      { key: "course_block_count", label: "有课时段" },
      { key: "status", label: "检查结果" }
    ]);
  }
  if (tab === "files") {
    return table(result.details, [
      { key: "filename", label: "文件名" },
      { key: "member", label: "成员" },
      { key: "source_type", label: "课表类型" },
      { key: "page_count", label: "页数" },
      { key: "ocr_pages", label: "图片识别页" },
      { key: "course_block_count", label: "有课时段" },
      { key: "status", label: "处理结果" },
      { key: "warning", label: "说明" }
    ]);
  }
  if (tab === "issues") {
    if (!unresolvedIssueCount(result)) return `<div class="emptyPanel"><b>没有待处理问题</b><span>本次结果已检查完成，可以导出空课表。</span></div>`;
    return `<div class="issueTable">${result.issues.map((issue) => `<article class="${escapeHtml(issue.confirmed ? "confirmed" : issue.severity)}">
      <div><b>${issueStateLabel(issue)}</b></div>
      <h3>${escapeHtml(friendlyMessage(issue.message))}</h3><p>${escapeHtml(friendlyMessage(issue.suggestion || "请在“原文核对”中对照课表。"))}</p>
      ${issueAction(issue)}
    </article>`).join("")}</div>`;
  }
  if (tab === "review") return `<div class="loadingPanel">正在载入课表原文…</div>`;
  if (tab === "calendar") return `<div class="calendarHost"></div>`;
  const rows = result.availability_preview;
  const pageSize = 800;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(availabilityPage, pageCount - 1);
  const pageRows = rows.slice(page * pageSize, (page + 1) * pageSize);
  const columns = ["周次", "日期", "星期", "节次", "时间", "空闲人数", "空闲人员", "占用人数", "有课人员"];
  const pager = availabilityPager(rows.length, page, pageCount);
  return `${pager}${table(pageRows, columns)}${pager}`;
}

function availabilityPager(total: number, page: number, pageCount: number) {
  if (pageCount <= 1) return "";
  return `<div class="tablePager" role="group" aria-label="共同空闲分页">
    <span aria-live="polite">共 ${total} 个时段，当前第 ${page + 1} / ${pageCount} 页</span>
    <button type="button" data-availability-page="${page - 1}" ${page === 0 ? "disabled" : ""}>上一页</button>
    <button type="button" data-availability-page="${page + 1}" ${page >= pageCount - 1 ? "disabled" : ""}>下一页</button>
  </div>`;
}

function tabEmptyLabel(tab: Tab) {
  const labels: Record<Tab, string> = {
    availability: "共同空闲时段",
    members: "成员课表",
    files: "文件检查结果",
    issues: "待处理内容",
    review: "课表原文",
    calendar: "课程周视图"
  };
  return labels[tab];
}

type TableColumn = { key: string; label: string };

function table(rows: Array<Record<string, unknown>>, columns: Array<string | TableColumn>) {
  if (!rows.length) return `<div class="emptyPanel">暂无数据。</div>`;
  const normalized = columns.map((column) => typeof column === "string" ? { key: column, label: column } : column);
  return `<div class="tableWrap"><table><thead><tr>${normalized.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${normalized.map((column) => `<td>${escapeHtml(tableCell(row[column.key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function tableCell(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (value === "accepted") return "可以导出";
  if (value === "needs_review") return "需要核对";
  if (value === "blocked") return "暂不能导出";
  return friendlyMessage(String(value));
}

function tabButton(tab: Tab, label: string, count?: number) {
  return `<button id="tab-${tab}" role="tab" aria-controls="panel-${tab}" data-tab="${tab}">${label}${typeof count === "number" ? `<em>${count}</em>` : ""}</button>`;
}

function fileChip(path: string) {
  const name = path.split(/[\\/]/).pop() || path;
  const kind = inferKind(path);
  return `<span class="sourceChip ${kind ? "" : "invalid"}" role="listitem"><i aria-hidden="true">${kind || "?"}</i><b title="${escapeHtml(name)}">${escapeHtml(name)}</b><button type="button" aria-label="移除课表 ${escapeHtml(name)}" title="移除这份课表" data-remove="${escapeHtml(path)}">×</button></span>`;
}

function inferKind(path: string) {
  // 与后端 infer_pdf_kind 保持一致：文件名优先，目录只在文件名无标记时作中文回退。
  // 否则路径里出现 uk / english / Luke 之类的片段会把明确写了“中方”的文件判成英方。
  const name = path.split(/[\\/]/).pop() || path;
  const nameKind = inferKindFromText(name, false);
  if (nameKind) return nameKind;
  const directory = path.slice(0, Math.max(0, path.length - name.length));
  return inferKindFromText(directory, true);
}

function inferKindFromText(text: string, chineseOnly: boolean) {
  const lowered = text.toLowerCase();
  if (text.includes("英方") || (!chineseOnly && (lowered.includes("uk") || lowered.includes("english")))) return "英方";
  if (text.includes("中方") || (!chineseOnly && lowered.includes("chinese"))) return "中方";
  return "";
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    boot: "正在启动", ready: "等待导入", queued: "准备处理", waiting: "等待处理", text_layer: "读取课表文字",
    pdf_inspection: "检查文件可读性",
    profile: "识别课表版式", ocr: "识别图片文字", course_parse: "整理课程时间", review: "等待核对",
    completed: "处理完成", exported: "导出完成", failed: "处理失败", cancelling: "正在停止", cancelled: "已停止", interrupted: "已中断"
  };
  return labels[stage] || stage;
}

function resultStatusLabel(stateValue: string) {
  if (stateValue === "accepted") return "可以导出";
  if (stateValue === "needs_review") return "需要核对";
  return "暂不能导出";
}

function resultActionText(stateValue: string) {
  if (stateValue === "needs_review") return "课表已处理，请先核对标记内容";
  if (stateValue === "blocked") return "发现影响结果的问题，请先修正";
  return "空课表已生成，可以导出";
}

function unresolvedIssueCount(result: ParseResult | null) {
  return result?.issues.filter((issue) => !issue.confirmed).length || 0;
}

function resultStatusDetail(result: ParseResult) {
  const count = unresolvedIssueCount(result);
  if (result.can_export) return "所有问题均已处理";
  if (result.quality_state === "blocked") return `${count} 项必须先修正`;
  return `${count} 项需要核对`;
}

function runHint(result: ParseResult | null) {
  if (result?.can_export) return "空课表已生成";
  if (result) return unresolvedIssueCount(result) ? "请完成待处理项目" : "请检查本次结果";
  if (state.busy) return state.progress.total ? `${state.progress.current} / ${state.progress.total} 份课表` : "正在处理课表";
  if (state.files.length) return `已选择 ${state.files.length} 份课表`;
  return "请先导入课表";
}

function issueStateLabel(issue: ParseResult["issues"][number]) {
  if (issue.confirmed) return "已核对";
  if (issue.severity === "error") return "必须处理";
  if (issue.severity === "warning") return "请核对";
  return "请留意";
}

function issueAction(issue: ParseResult["issues"][number]) {
  if (issue.confirmed) return "";
  if (issue.severity === "error") return `<small class="issueActionHint">请先补充或修正课表，再重新生成空课表。</small>`;
  return `<button data-confirm-inline="${escapeHtml(issue.issue_id)}">确认已核对</button>`;
}

function resourceDetail(status: ResourceStatus | null) {
  if (!status) return "正在确认图片课表识别能力";
  if (status.ocr.ready) return "支持识别图片课表";
  return `${resourceIssueLabel(status)}，请点击修复`;
}

function resourceRepairStatus(status: ResourceStatus) {
  if (status.ready) return "识别功能已恢复";
  return `${resourceIssueLabel(status)}，修复未完成`;
}

function resourceIssueLabel(status: ResourceStatus) {
  if (status.ocr.issue === "path-incompatible") return "识别模型路径不兼容";
  if (status.ocr.issue === "missing") return "识别模型缺失";
  if (status.ocr.issue === "invalid") return "识别模型校验失败";
  return "本地识别资源未就绪";
}

function friendlyMessage(message: string) {
  return message
    .replaceAll("质量门禁", "结果检查")
    .replaceAll("持久任务", "处理")
    .replaceAll("课程占用槽", "课程时间")
    .replaceAll("时间块", "有课时段")
    .replaceAll("阻断错误", "必须处理的问题")
    .replaceAll("组合标识", "成员信息")
    .replaceAll("角色", "职务")
    .replaceAll("PDF 复核页", "“原文核对”")
    .replaceAll("OCR 框", "识别标记")
    .replaceAll("PDF 文本层", "课表文字")
    .replaceAll("文本层", "可读取文字")
    .replaceAll("sidecar", "本地识别功能")
    .replaceAll("OCR", "图片文字识别");
}

function setStatus(message: string) {
  state.statusText = message;
  log(message);
  render();
}

function log(message: string, rerender = true) {
  const row = `${new Date().toLocaleTimeString("zh-CN", { hour12: false })}  ${message}`;
  if (state.logs[0] !== row) state.logs = [row, ...state.logs].slice(0, 60);
  if (rerender) render();
}

function formatError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes("__TAURI") || message.includes("invoke") || message.includes("Command") || message.includes("transformCallback")) {
    return "桌面运行环境未连接，请在 Konggu 桌面端中使用该功能。";
  }
  return friendlyMessage(message.replace(/^Error:\s*/, ""));
}

function escapeHtml(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function getElement<T extends HTMLElement>(selector: string) {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing UI element: ${selector}`);
  return element;
}

function setText(selector: string, value: string) {
  const element = getElement<HTMLElement>(selector);
  if (element.textContent !== value) element.textContent = value;
}

function setDisabled(selector: string, disabled: boolean) {
  getElement<HTMLButtonElement>(selector).disabled = disabled;
}

function setInputValue(selector: string, value: string) {
  const input = getElement<HTMLInputElement>(selector);
  if (document.activeElement !== input && input.value !== value) input.value = value;
}

function setHtmlIfChanged(element: HTMLElement, html: string) {
  if (renderedHtmlCache.get(element) === html) return;
  renderedHtmlCache.set(element, html);
  element.innerHTML = html;
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

void boot();

// 处理中关闭窗口会让任务中断且结果不可续跑：至少在关闭前给用户一次确认。
window.addEventListener("beforeunload", (event) => {
  if (!state.busy) return;
  event.preventDefault();
  event.returnValue = "课表仍在处理中，关闭窗口会中断任务。";
});

// 页面刷新（Ctrl+R/F5）会重新执行脚本：先结束旧 worker，避免两个进程同时写同一份状态。
window.addEventListener("pagehide", () => {
  void client.shutdown();
});
