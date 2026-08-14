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
  progress: { current: 0, total: 0 },
  logs: []
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("App root not found.");
const appRoot: HTMLDivElement = app;
let calendar: Calendar | null = null;
let calendarKey = "";
let reviewController: ReviewController | null = null;
let reviewMount: Promise<void> | null = null;
let reviewPayload: ReviewPayload | null = null;
let shellMounted = false;
let renderedResult: ParseResult | null | undefined;
let runAguMode = "";
let completionPlayedFor = "";
let richViewGeneration = 0;
let cancelRequestedJobId = "";
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
  if (state.jobId && event.job_id !== state.jobId) return;
  const cancellationPending = cancelRequestedJobId !== "" && event.job_id === cancelRequestedJobId && event.stage !== "cancelled";
  state.stage = cancellationPending ? "cancelling" : event.stage;
  state.progress = { current: event.current, total: event.total };
  state.statusText = cancellationPending ? "正在完成当前图片页后停止" : friendlyMessage(event.message);
  log(state.statusText, false);
  render();
});

async function boot() {
  render();
  try {
    await Promise.all([client.start(), delay(900)]);
    const [resources, settings] = await Promise.all([
      client.request<ResourceStatus>("resources.status"),
      client.request<{ ok: boolean; settings: CalendarSettings }>("settings.calendar.get")
    ]);
    state.resourceStatus = resources;
    state.calendarSettings = settings.settings;
    state.statusText = resources.ready ? "准备完成，可以导入课表" : "识别功能不完整，请先修复";
    state.stage = "ready";
  } catch (error) {
    state.stage = "failed";
    state.statusText = formatError(error);
  }
  render();
}

async function chooseFiles() {
  const selected = await open({ multiple: true, filters: [{ name: "PDF", extensions: ["pdf"] }] });
  const paths = Array.isArray(selected) ? selected : typeof selected === "string" ? [selected] : [];
  state.files = Array.from(new Set([...state.files, ...paths]));
  state.result = null;
  state.review = null;
  state.statusText = state.files.length ? `已选择 ${state.files.length} 份课表` : state.statusText;
  render();
}

async function parseSchedules() {
  if (!state.files.length) return setStatus("请先选择课表 PDF。");
  const invalid = state.files.filter((path) => !inferKind(path));
  if (invalid.length) return setStatus(`${invalid.length} 个文件无法判断中方/英方，请先按规则改名。`);
  state.busy = true;
  state.result = null;
  state.review = null;
  cancelRequestedJobId = "";
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
    state.statusText = `解析失败：${formatError(error)}`;
    log(state.statusText);
    render();
  }
}

async function waitForJob(jobId: string) {
  while (state.jobId === jobId) {
    const response = await client.request<JobResponse>("job.get", { job_id: jobId });
    const job = response.job;
    state.progress = { current: job.current, total: job.total };
    if (job.status === "completed") {
      cancelRequestedJobId = "";
      state.result = job.output || null;
      state.busy = false;
      state.stage = "completed";
      if (state.result?.detected_max_week) {
        state.calendarSettings.teaching_weeks = state.result.detected_max_week;
      }
      state.review = await client.request<ReviewPayload>("review.get", { job_id: jobId });
      state.activeTab = state.result?.can_export ? "availability" : "issues";
      state.statusText = state.result?.can_export
        ? "空课表已生成，可以导出"
        : resultActionText(state.result?.quality_state || "blocked");
      log(state.statusText);
      render();
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
      state.busy = false;
      state.stage = "interrupted";
      state.statusText = restartError
        ? `处理已中断；本地识别功能需要重新启动：${restartError}`
        : "处理已中断，可点击“重新处理失败文件”。";
      cancelRequestedJobId = "";
      render();
    }
  }, 10_000);
  await client.request("job.cancel", { job_id: jobId }).catch(() => undefined);
}

async function retryFailed() {
  if (!state.jobId || state.busy) return;
  state.busy = true;
  cancelRequestedJobId = "";
  render();
  try {
    const started = await client.request<{ ok: boolean; job_id: string }>("job.retry_failed", {
      job_id: state.jobId
    });
    state.jobId = started.job_id;
    await waitForJob(started.job_id);
  } catch (error) {
    state.busy = false;
    setStatus(formatError(error));
  }
}

async function exportExcel() {
  if (!state.result?.result_ref) return setStatus("请先生成空课表。");
  if (!state.result.can_export) {
    state.activeTab = "issues";
    return setStatus("当前结果还不能导出，请先核对或修正“待处理”中的内容。");
  }
  const target = await save({
    defaultPath: "Konggu_availability.xlsx",
    filters: [{ name: "Excel", extensions: ["xlsx"] }]
  });
  if (!target) return;
  state.busy = true;
  state.statusText = "正在生成 Excel 文件";
  render();
  try {
    const response = await client.request<{ ok: boolean; path: string }>("exports.excel", {
      result_ref: state.result.result_ref,
      target_path: target,
      mode: "classic",
      export_week_count: state.calendarSettings.teaching_weeks
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
  state.busy = true;
  setStatus("正在检查并修复本地识别功能");
  try {
    const response = await client.request<{ ok: boolean; status: ResourceStatus; copied: string[] }>("resources.repair");
    state.resourceStatus = response.status;
    state.statusText = response.status.ready ? "识别功能已恢复" : "仍有识别组件需要修复";
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
  const response = await client.request<{ ok: boolean; settings: CalendarSettings }>("settings.calendar.save", {
    settings: state.calendarSettings
  });
  state.calendarSettings = response.settings;
  if (showFeedback) setStatus("学期设置已保存。");
}

async function applyReview(blockId: string, field: string, value: unknown, reason: string) {
  if (!state.jobId) return;
  if (!reason.trim()) throw new Error("请填写修正原因。");
  state.review = await client.request<ReviewPayload>("review.apply", {
    job_id: state.jobId,
    block_id: blockId,
    field,
    new_value: value,
    reason,
    operator_id: "本机用户"
  });
  await refreshJobOutput();
}

async function confirmIssue(issueId: string) {
  if (!state.jobId) return;
  state.review = await client.request<ReviewPayload>("review.confirm", {
    job_id: state.jobId,
    issue_id: issueId
  });
  await refreshJobOutput();
}

async function refreshJobOutput() {
  const response = await client.request<JobResponse>("job.get", { job_id: state.jobId });
  state.result = response.job.output || state.result;
  if (state.result) {
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
          <button id="retryFailed">重新处理失败文件</button>
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
  cancel.hidden = !state.busy;
  retry.hidden = state.busy;
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
  setText("#resourceDetail", state.resourceStatus?.ocr.ready ? "支持识别图片课表" : "暂不能识别图片课表");
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
  document.querySelector("#saveSettings")?.addEventListener("click", () => void saveSettings());
  document.querySelector("#clearFiles")?.addEventListener("click", () => {
    state.files = []; state.result = null; state.review = null; render();
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
      const chip = remove.closest<HTMLElement>(".sourceChip");
      const commit = () => {
        state.files = state.files.filter((path) => path !== remove.dataset.remove);
        state.result = null;
        state.review = null;
        render();
      };
      chip ? commitAfterExit(chip, commit) : commit();
      return;
    }
    const confirm = target.closest<HTMLButtonElement>("[data-confirm-inline]");
    if (confirm) void confirmIssue(confirm.dataset.confirmInline || "");
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
    renderedResult = result;
    if (!result) {
      resetRichViews();
      setHtmlIfChanged(getPanel("availability"), renderWelcome());
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
        calendar.destroy();
        calendar = null;
        calendarKey = "";
        getPanel("calendar").innerHTML = `<div class="calendarHost"></div>`;
      }
    }
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
  calendar?.destroy();
  const host = getPanel("calendar").querySelector<HTMLElement>(".calendarHost");
  if (!host) return;
  calendar = mountCalendar(host, result.courses, state.calendarSettings, async (block, patch) => {
    for (const [field, value] of Object.entries(patch)) {
      await applyReview(block.block_id, field, value, "周课表拖拽修正");
    }
  });
  calendarKey = key;
  onNextFrame(() => calendar?.updateSize());
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

function renderWelcome() {
  return `<div class="emptyPanel welcomePanel">
    <div class="welcomeCopy">
      <span class="welcomeKicker">先导入课表</span>
      <b>把大家的课表放在一起，看看什么时候都有空。</b>
      <p>请导入同一批成员的中方和英方课表。空谷会整理每个人的上课时间，把不确定的内容列出来核对，然后生成共同空闲时段。</p>
      <div class="welcomeSteps"><span><i>1</i>导入课表</span><span><i>2</i>生成空课表</span><span><i>3</i>核对并导出</span></div>
    </div>
    ${renderAguMotion("welcome")}
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
    return `<div class="issueTable">${result.issues.map((issue) => `<article class="${issue.confirmed ? "confirmed" : issue.severity}">
      <div><b>${issueStateLabel(issue)}</b></div>
      <h3>${escapeHtml(friendlyMessage(issue.message))}</h3><p>${escapeHtml(friendlyMessage(issue.suggestion || "请在“原文核对”中对照课表。"))}</p>
      ${issue.confirmed ? "" : `<button data-confirm-inline="${issue.issue_id}">确认已核对</button>`}
    </article>`).join("")}</div>`;
  }
  if (tab === "review") return `<div class="loadingPanel">正在载入课表原文…</div>`;
  if (tab === "calendar") return `<div class="calendarHost"></div>`;
  return table(result.availability_preview.slice(0, 800), ["周次", "日期", "星期", "节次", "时间", "空闲人数", "空闲人员", "占用人数", "有课人员"]);
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
  const text = path.toLowerCase();
  if (path.includes("英方") || text.includes("english") || text.includes("uk")) return "英方";
  if (path.includes("中方") || text.includes("chinese")) return "中方";
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
