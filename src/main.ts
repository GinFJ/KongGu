import { open, save } from "@tauri-apps/plugin-dialog";
import type { Calendar } from "@fullcalendar/core";
import wordmarkUrl from "../assets/konggu-wordmark.png";
import aguSplashUrl from "../assets/brand/agu/agu-splash-scene-1280x720.webp";
import { renderAguMotion } from "./aguMotion";
import { mountCalendar } from "./calendarView";
import type { CourseSlot } from "./calendarCourseEvents";
import { availabilityWeeks, availabilityToolbar, availabilityWeekGrid, availabilitySlotDetail, departmentPicker } from "./availabilityView";
import { availabilityForDepartment, departmentOptions, memberDepartmentOptions, membersForDepartment } from "./departmentView";
import { chooseExportOptions, exportDialog, workbookName } from "./exportOptions";
import { currentMonth, dateFields, outsideSemester, semesterDates, suggestedDates, type BatchDates } from "./batchDates";
import { mountReview, type ReviewController } from "./reviewView";
import { SidecarClient } from "./sidecarClient";
import { CloudWorkspace } from "./cloudWorkspace";
import { showLocalHistory, type LocalHistory } from "./localHistory";
import { needsOriginal, rememberedChoice, readLinkedPdf, type SavedWorkspace } from "./workspaceSession";
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
import "./workbench.css";
import "./verification.css";
import { renderReviewOverview, type ReviewFilter } from "./reviewOverview";

type Tab = "availability" | "members" | "files" | "issues" | "review" | "calendar";
type Workspace = "sources" | "review" | "results";
const workspaceTabs: Record<Workspace, Tab[]> = { sources: ["files", "members", "calendar"], review: ["issues", "review"], results: ["availability"] };
const workspaceTitles: Record<Workspace, string> = { sources: "课表资料", review: "课表核验", results: "空课结果" };
const lastWorkspaceTab: Record<Workspace, Tab> = { sources: "files", review: "issues", results: "availability" };
function currentWorkspace(): Workspace {
  if (!state.result) return "sources";
  return state.activeTab === "availability" ? "results" : workspaceTabs.sources.includes(state.activeTab) ? "sources" : "review";
}

type AppState = {
  batchDates: BatchDates;
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
  startupDiagnostic: string;
};

const client = new SidecarClient();
const freshState = (): AppState => ({
  batchDates: currentMonth(),
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
  logs: [],
  startupDiagnostic: ""
});
const state = freshState();

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("App root not found.");
let appRoot: HTMLDivElement = app;
let calendar: Calendar | null = null;
let calendarKey = "";
const calendarScope = { member: "" };
let calendarViewDate: Date | null = null;
let reviewController: ReviewController | null = null;
let reviewMount: Promise<void> | null = null;
let reviewFilter: ReviewFilter = "all";
let renderedOverviewReview: ReviewPayload | null = null;
let reviewPayload: ReviewPayload | null = null;
let availabilityPage = 0;
let availabilityWeek = 0;
let availabilityDepartment = "";
let availabilityMode: "week" | "list" = "week";
let pendingReviewSource = "";
let pendingReviewBlock = "";
let rememberSources = true;
let draftWrite: Promise<unknown> = Promise.resolve();
let shellMounted = false;
let renderedResult: ParseResult | null | undefined;
let runAguMode = "";
let completionPlayedFor = "";
let richViewGeneration = 0;
let cancelRequestedJobId = "";
let completionProbeJobId = "";
let completedJobId = "";
let reviewReloading = false;
let emptyPanelKey = "";
let sourceListExpanded = true;
let savedCalendarSettings: CalendarSettings = { ...state.calendarSettings };
const renderedHtmlCache = new WeakMap<HTMLElement, string>();

const tabs: Tab[] = ["availability", "members", "files", "issues", "review", "calendar"];
const INSTALLATION_STARTUP_MESSAGE = "安装组件未能启动，请使用完整安装包重新安装；仍无法启动时请联系维护者。";

const cloud = new CloudWorkspace(client, {
  current: () => ({
    job: state.result ? state.jobId : "", revision: state.review?.revision || "",
    memberCount: state.result?.summary.member_count || 0, hasFiles: state.files.length > 0,
    busy: state.busy || state.stage === "boot" || reviewReloading
  }),
  async drain() {
    await draftWrite.catch(() => undefined);
    if (state.busy || reviewReloading || client.hasPendingRequests) throw new Error("本机操作还在进行，请稍后重试。");
  },
  reopen: reopenWorkbench, render: () => render()
});
client.onAccountWorkerLoss = () => { state.busy = false; cloud.workerLost(); };

async function reopenWorkbench() {
  resetWorkbench();
  await startWorkbench(true);
}

function resetWorkbench() {
  // Detach the previous profile's dialogs, PDF canvases, results and event targets together.
  resetRichViews();
  const freshRoot = appRoot.cloneNode(false) as HTMLDivElement;
  appRoot.replaceWith(freshRoot);
  appRoot = freshRoot;
  Object.assign(state, freshState());
  savedCalendarSettings = { ...state.calendarSettings };
  shellMounted = false;
  renderedResult = undefined;
  renderedOverviewReview = null;
  sourceKey = null;
  sourceListExpanded = true;
  runAguMode = completionPlayedFor = emptyPanelKey = "";
  completedJobId = cancelRequestedJobId = completionProbeJobId = "";
  reviewFilter = "all";
  availabilityPage = availabilityWeek = 0;
  availabilityDepartment = "";
  availabilityMode = "week";
  rememberSources = true;
  draftWrite = Promise.resolve();
  Object.assign(lastWorkspaceTab, {sources: "files", review: "issues", results: "availability"});
}

client.onEvent((event) => {
  if (event.event === "sidecar.fatal") {
    const message = formatError(event.message, true);
    state.statusText = state.startupDiagnostic ? message : `本地识别功能启动失败：${message}`;
    state.stage = "failed";
    if (state.resourceStatus) state.busy = false;
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
  await startWorkbench(false);
}

async function startWorkbench(resumeSelected: boolean) {
  state.stage = "boot";
  state.busy = true;
  state.statusText = "正在启动本地识别服务";
  render();
  try {
    const [resourceRepair, settings] = await Promise.all([
      client.request<{ ok: boolean; status: ResourceStatus; copied: string[] }>("resources.repair"),
      client.request<{ ok: boolean; settings: CalendarSettings }>("settings.calendar.get"),
      delay(900)
    ]);
    const resources = resourceRepair.status;
    state.resourceStatus = resources;
    state.calendarSettings = settings.settings;
    savedCalendarSettings = { ...settings.settings };
    state.batchDates = suggestedDates(savedCalendarSettings);
    state.startupDiagnostic = "";
    state.statusText = resources.ready ? "准备完成，可以导入课表" : "本地识别资源未能自动准备，请点击修复";
    state.stage = "ready";
    try {
      if (!resumeSelected) {
        const history = await client.request<LocalHistory>("workspace.history");
        if (history.batches.length || history.draft) displayLocalHistory(history);
      } else if (resources.ready) await resumeWorkspace();
    } catch (error) { state.statusText = `本机记录暂时无法打开：${formatError(error)}`; }
  } catch (error) {
    state.stage = "failed";
    state.statusText = formatError(error, true);
  }
  if (["boot", "ready", "failed"].includes(state.stage)) state.busy = false;
  render();
}

async function resumeWorkspace(selected?: SavedWorkspace) {
  const saved = selected || await client.request<SavedWorkspace>("workspace.get");
  if (saved.calendar) {
    state.calendarSettings = { ...saved.calendar };
    savedCalendarSettings = { ...saved.calendar };
    state.batchDates = suggestedDates(saved.calendar);
  }
  if (saved.start_date && saved.end_date) state.batchDates = {start_date: saved.start_date, end_date: saved.end_date};
  rememberSources = saved.remember;
  state.files = saved.files.map(file => file.path || file.name);
  if (saved.job_id && saved.status === "completed") {
    state.jobId = saved.job_id;
    const { job } = await client.request<JobResponse>("job.get", { job_id: saved.job_id });
    if (job.output?.warnings.some(text => text.includes("与当前学期设置或解析规则不一致"))) {
      state.jobId = "";
      state.statusText = "学期设置或识别规则已变化，请用已恢复的课表重新生成。";
      return;
    }
    await finishCompletedJob(saved.job_id, job);
    if (state.jobId === saved.job_id && state.result && state.review) state.statusText = "课表已打开，核验进度已恢复。";
  } else if (state.files.length) {
    state.statusText = saved.status === "interrupted"
      ? "上次识别被中断，已恢复文件列表，可重新生成。" : "已恢复上次选择的课表。";
  }
}

function persistDraft() {
  const files = [...state.files];
  const remember = rememberSources;
  const dates = { ...state.batchDates };
  draftWrite = draftWrite.catch(() => undefined).then(() =>
    client.request("workspace.save", { files, remember, ...(dates.start_date && dates.end_date && dates.start_date <= dates.end_date ? dates : {}) }));
  void draftWrite.catch(error => setStatus(`本批文件列表暂未保存：${formatError(error)}`));
}

async function reconnectOriginals() {
  if (state.busy) return;
  const jobId = state.jobId;
  const owner = appRoot;
  try {
    const selected = await open({ multiple: true, title: "选择本批原件，可分次关联", filters: [{name: "PDF", extensions: ["pdf"]}] });
    const paths = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (!paths.length || jobId !== state.jobId || owner !== appRoot) return;
    await draftWrite;
    if (owner !== appRoot) return;
    const reply = await client.request<Pick<SavedWorkspace, "files">>("workspace.attach", {job_id: jobId, paths, remember: rememberSources});
    if (jobId !== state.jobId) return;
    state.files = reply.files.map((file, index) => file.path || state.files[index] || file.name);
    if (state.result) await reloadReview();
    setStatus("原件已关联，可继续本批工作。");
  } catch (error) { setStatus(formatError(error)); }
}

function resetForNewInput(message: string) {
  // 文件列表变化后旧任务的所有引用与阶段都必须复位，否则界面会残留
  // “处理完成/处理失败”却没有任何结果。
  state.result = null;
  sourceListExpanded = true;
  state.review = null;
  state.jobId = "";
  state.failedFileCount = 0;
  state.progress = { current: 0, total: 0 };
  state.stage = "ready";
  state.statusText = message;
  availabilityPage = 0;
  persistDraft();
  cancelRequestedJobId = "";
  completionProbeJobId = "";
  completedJobId = "";
  render();
}

async function chooseFiles() {
  if (state.busy) return;
  const owner = appRoot;
  const selected = await open({ multiple: true, title: rememberSources ? "选择课表并在本机记住原件位置" : "选择本次使用的课表", filters: [{ name: "PDF", extensions: ["pdf"] }] });
  const paths = Array.isArray(selected) ? selected : typeof selected === "string" ? [selected] : [];
  // 取消选择对话框时保留当前结果与任务引用，不能因为空选择把已生成的结果清掉。
  if (!paths.length) return;
  if (owner !== appRoot) return;
  const chosenNames = new Set(paths.map(path => path.split(/[\\/]/).pop()));
  state.files = state.files.filter(path => !needsOriginal(path) || !chosenNames.has(path));
  state.files = Array.from(new Set([...state.files, ...paths]));
  resetForNewInput(state.files.length ? `已选择 ${state.files.length} 份课表` : "请选择课表 PDF");
}

async function parseSchedules() {
  if (state.busy) return;
  if (!state.files.length) return setStatus("请先选择课表 PDF。");
  const batch = { ...state.batchDates };
  if (!batch.start_date || !batch.end_date || batch.start_date > batch.end_date) {
    document.querySelector<HTMLInputElement>("#batchEnd")?.reportValidity();
    return setStatus("请选择本次合表的开始和结束日期，结束日期须在开始日期当天或之后。");
  }
  if (state.files.some(needsOriginal)) return setStatus("有原件需要重新关联，请点「关联原件」后生成。");
  if (outsideSemester(batch, savedCalendarSettings)) return setStatus("合表日期超出当前校历，请调整日期，或先保存正确的学期设置。");
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
    await draftWrite;
    await saveSettings(false);
    const started = await client.request<{ ok: boolean; job_id: string }>("job.start", { paths: state.files, workspace: true, remember_sources: rememberSources, ...batch });
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
  state.result = checkedWorkerResult(job.output);
  state.failedFileCount = job.files.filter((file) => file.status === "failed").length;
  state.busy = false;
  state.stage = state.result?.can_export ? "completed" : "review";
  // 检测周数只用于本次展示；不回写学期设置。回写会让“导出周数”被检测值收缩，
  // 并改变解析签名，使上一轮已做的人工修正失效。
  state.activeTab = state.result?.can_export ? "availability" : "issues";
  if (state.result?.date_review) {
    const { start_date, end_date } = state.result.date_review;
    state.batchDates = { start_date, end_date };
    if (!state.result.can_export && state.result.date_review.members.length) state.activeTab = "review";
  }
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
    await refreshJobOutput();
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
      if (state.jobId !== jobId || completedJobId === jobId) return;
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
    if (state.jobId !== jobId || completedJobId === jobId) return;
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
  renderedOverviewReview = null;
  reviewFilter = "all";
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
    state.activeTab = result.date_review ? "review" : "issues";
    return setStatus("当前结果还不能导出，请到批次总览查看待核验课程和异常。");
  }
  const department = availabilityDepartment;
  const scoped = availabilityForDepartment(result, department);
  if (department && !scoped.memberCount) return setStatus("当前部门还没有可导出的成员，请核对部门归属。");
  const selection = await chooseExportOptions(department, scoped.memberCount, availabilityWeek, result.date_review || undefined);
  if (!selection || state.busy) return;
  if (state.result !== result || department !== availabilityDepartment) return setStatus("范围已更新，请重新选择导出内容。");
  let target: string | null;
  try {
    target = await save({
      defaultPath: workbookName(department, selection, result.date_review || undefined),
      filters: [{ name: "Excel", extensions: ["xlsx"] }]
    });
  } catch (error) {
    return setStatus(`无法打开保存窗口：${formatError(error)}`);
  }
  if (!target) return;
  // 保存对话框期间结果可能已被新任务替换，导出必须绑定用户刚才看到的那份结果。
  if (state.result !== result || availabilityDepartment !== department) return setStatus("结果或部门已更新，请重新确认后再导出。");
  state.busy = true;
  state.statusText = "正在生成 Excel 文件";
  render();
  try {
    const response = await client.request<{ ok: boolean; path: string }>("exports.excel", {
      result_ref: result.result_ref,
      target_path: target,
      mode: selection.mode,
      department,
      ...(selection.week ? { week: selection.week } : {}),
      // 用已保存的学期设置：输入框改动未点保存时，导出不能提前套用未生效的周数。
      ...(result.date_review ? {} : { export_week_count: savedCalendarSettings.teaching_weeks })
    });
    state.statusText = `空课表已导出：${response.path}`;
    state.stage = "exported";
    log(state.statusText);
  } catch (error) {
    state.statusText = `暂时无法导出：${formatError(error)}`;
  } finally {
    state.busy = false;
    render();
  }
}

async function repairResources() {
  if (state.startupDiagnostic) return setStatus(INSTALLATION_STARTUP_MESSAGE);
  if (state.busy) return;
  // 启动自检还未返回时，需要重新启动服务并读回学期设置。
  if (!state.resourceStatus) return boot();
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
  await client.request("data.clear");
  resetLocalView("本机记录和识别缓存已清除");
}

function resetLocalView(message: string) {
  const resources = state.resourceStatus;
  const calendar = { ...savedCalendarSettings };
  const remember = rememberSources;
  resetWorkbench();
  rememberSources = remember;
  state.resourceStatus = resources;
  state.calendarSettings = { ...calendar };
  savedCalendarSettings = calendar;
  state.batchDates = suggestedDates(calendar);
  state.stage = "ready";
  state.statusText = message;
  render();
}

function displayLocalHistory(initial?: LocalHistory) {
  showLocalHistory(client, {
    async openBatch(jobId) {
      const saved = await client.request<SavedWorkspace>("workspace.open", {job_id: jobId});
      resetLocalView("正在打开本机课表");
      await resumeWorkspace(saved);
      render();
    },
    async openDraft() {
      const saved = await client.request<SavedWorkspace>("workspace.get");
      resetLocalView("正在打开上次草稿");
      await resumeWorkspace(saved);
      render();
    },
    async newBatch() {
      await client.request("workspace.save", {files: [], remember: rememberSources});
      resetLocalView("请选择课表 PDF");
    },
    clearAll: clearLocalData
  }, initial);
}

async function openLocalHistory() {
  if (state.busy || reviewReloading) return;
  const owner = appRoot;
  await draftWrite.catch(() => undefined);
  if (owner !== appRoot || state.busy || reviewReloading || client.hasPendingRequests) {
    if (owner === appRoot) setStatus("本机操作还在进行，请稍后打开记录。");
    return;
  }
  displayLocalHistory();
}

async function saveSettings(showFeedback = true) {
  const next = { ...state.calendarSettings };
  const wouldChange = savedCalendarSettings.semester_start_date !== next.semester_start_date
    || savedCalendarSettings.teaching_weeks !== next.teaching_weeks;
  const hadResults = Boolean(state.result || state.review);
  if (wouldChange && showFeedback) {
    const warning = hadResults
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
      if (outsideSemester(state.batchDates, response.settings)) state.batchDates = suggestedDates(response.settings);
      state.result = null;
      state.review = null;
      state.jobId = "";
      state.failedFileCount = 0;
      state.activeTab = "availability";
      if (!state.busy) state.stage = "ready";
      completedJobId = "";
      if (showFeedback) persistDraft();
    }
    if (showFeedback) {
      setStatus(
        changed && hadResults
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

async function mutateReview(payload: Record<string, unknown>) {
  const jobId = state.jobId;
  if (!jobId) throw new Error("请先生成课表再修正。");
  const review = await client.request<ReviewPayload>("review.mutate", { ...payload, job_id: jobId });
  if (state.jobId !== jobId) return;
  state.review = review;
  try {
    await refreshJobOutput();
  } catch (error) {
    setStatus(`修正已保存，但结果刷新失败：${formatError(error)}`);
    throw new Error("修正已保存，请重新载入结果，不要重复补录。");
  }
}

async function requestAi(command: string, payload: Record<string, unknown>) {
  const result = await client.request<any>(command, payload, 240_000);
  if (command === "ai.run" && state.jobId === result.job_id) {
    state.review = await client.request<ReviewPayload>("review.get", { job_id: result.job_id });
    await refreshJobOutput();
  }
  if (command === "ai.adopt" && state.jobId === payload.job_id) {
    state.review = result as ReviewPayload;
    try { await refreshJobOutput(); }
    catch { throw new Error("补录已保存，但刷新失败。请重新载入结果，不要重复补录。"); }
  }
  return result;
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

async function verifyItem(itemId: string, revision: string): Promise<ReviewPayload> {
  const jobId = state.jobId;
  if (!jobId) throw new Error("请重新打开本批课表。");
  const reply = await client.request<ReviewPayload | {previous_revision: string; revision: string; item_id: string;
    verification: {total: number; confirmed: number; pending: number}}>("review.verify", {
    job_id: jobId, item_id: itemId, revision, compact: true
  });
  if (state.jobId !== jobId) throw new Error("已切换批次。");
  let review: ReviewPayload;
  if (!("previous_revision" in reply)) review = reply;
  else if (state.review?.revision === reply.previous_revision && state.review.verification &&
           reply.item_id === itemId && state.review.verification.items.some(item => item.item_id === itemId)) {
    const items = state.review.verification.items.map(item => item.item_id === itemId ? {...item, confirmed: true} : item);
    review = {...state.review, revision: reply.revision, verification: {...reply.verification, items}};
  } else {
    review = await client.request<ReviewPayload>("review.get", {job_id: jobId});
    if (state.jobId !== jobId) throw new Error("已切换批次。");
  }
  state.review = review;
  // A confirmation only changes the queue. Fetch the authoritative export decision at completion.
  if (state.result && review.verification?.pending) {
    const { total, confirmed, pending } = review.verification;
    state.result.verification = { total, confirmed, pending };
    state.statusText = resultActionText(state.result.quality_state);
    render();
    return review;
  }
  try {
    await refreshJobOutput();
  } catch {
    const message = "核验已保存，结果尚未刷新。请重新载入核验结果。";
    setStatus(message);
    throw new Error(message);
  }
  return review;
}

async function dateCommand(command: "review.slot" | "review.member" | "review.flag", payload: Record<string, unknown>): Promise<ReviewPayload> {
  const job = state.jobId;
  if (!job || !state.review?.date_review) throw new Error("请重新打开本次核对。");
  const updated = await client.request<ReviewPayload>(command, {...payload, job_id: job});
  if (state.jobId !== job) throw new Error("已切换合表批次。");
  state.review = updated;
  await refreshJobOutput();
  return updated;
}

function rangeComplete() {
  if (state.result?.can_export) {
    state.activeTab = "availability";
    setStatus("本批成员已确认，可以查看并导出空课表。");
  } else {
    state.activeTab = "issues";
    setStatus("成员核对已保存，请检查剩余的课表问题。");
  }
}

function checkedWorkerResult(result?: ParseResult) {
  if (!result || result.verification) return result || null;
  // An older worker cannot enforce the per-course export check.
  return { ...result, can_export: false, errors: ["当前识别组件未提供逐项核验，请使用配套的新版空谷。", ...result.errors] };
}

async function refreshJobOutput() {
  const jobId = state.jobId;
  if (!jobId) return;
  const response = await client.request<JobResponse>("job.get", { job_id: jobId }, 60_000);
  if (state.jobId !== jobId) return;
  state.result = checkedWorkerResult(response.job.output) || state.result;
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
        <div class="brand"><img src="${wordmarkUrl}" alt="空谷" /><span title="空谷是青禾计划孵化的课表协作产品">青禾计划孵化项目</span></div>
        <nav class="workspaceNav" aria-label="工作区">
          <button data-workspace="sources">课表资料<em id="navFileCount" hidden></em></button>
          <button data-workspace="review">课表核验<em id="navIssueCount" hidden></em></button>
          <button data-workspace="results">空课结果</button>
          <p id="actionGuide" class="actionGuide" hidden></p>
        </nav>
        <div class="railUtilities">
          <button type="button" id="historyEntry" class="cloudEntry"><strong>本机课表记录</strong><small>打开历史 · 管理缓存</small></button>
          <button type="button" id="cloudEntry" class="cloudEntry"><strong>登录 / 注册</strong><small>本机模式</small></button>
          <details id="semesterSettings" class="railDisclosure">
            <summary>学期设置</summary>
            <div class="railSection">
              <label>第一周开始日期<input id="semesterStart" type="date" /></label>
              <label>教学周数<input id="teachingWeeks" type="number" min="1" max="30" /></label>
              <button id="applySemester" hidden>保存并重新生成</button>
              <button id="saveSettings">保存设置</button>
            </div>
          </details>
          <details id="maintenanceSettings" class="railDisclosure">
            <summary>本地维护</summary>
            <div class="railSection resourceState">
              <small id="resourceDetail">正在检查识别资源</small>
              <button id="repairResources">修复识别功能</button>
            </div>
          </details>
        </div>
        <p class="railFootnote">本地识别<span id="resourceTitle" class="warning">正在检查</span></p>
      </aside>
      <section class="workbench">
        <header class="masthead">
          <div class="workspaceHeading">
            <h1 id="workspaceTitle">课表资料</h1>
            <p id="workspaceMeta" hidden></p>
            <p id="statusText" aria-live="polite"></p>
          </div>
          <div class="workspaceActions">
          <div id="sourceDepartmentPicker" hidden></div>
          <div id="qualitySeal" class="qualitySeal pending">
            <span id="qualityTitle">本地处理</span>
            <small id="qualityDetail" hidden></small>
          </div>
          <button id="linkOriginals" hidden>关联原件</button>
          <button id="chooseFiles">添加课表</button>
          <button id="clearFiles" class="quiet">清空</button>
          <button id="parseSchedules" class="primary">识别并核对</button>
          <button id="cancelJob" class="danger" hidden>停止处理</button>
          <button id="retryFailed" hidden>重新处理本批课表</button>
          <button id="exportExcel" class="primary">导出空课表</button>
          </div>
        </header>
        <section class="runStrip" aria-label="处理进度">
          <div class="runMeta"><b id="stageLabel"></b><span id="runHint"></span></div>
          <div class="workflowTrack">
            <div id="progressTrack" class="progressTrack" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i id="progressFill"></i></div>
          </div>
          <div class="runCounts"><strong id="fileCount">0</strong><span>文件</span><strong id="memberCount">0</strong><span>成员</span><strong id="issueCount">0</strong><span>待处理</span></div>
          <div id="runAguSlot"></div>
        </section>
        ${dateFields}
        <section class="sourceRibbon">
          <div class="sourceHeader">
            <div class="sourceHeaderCopy"><span id="sourceSummary">尚未选择文件</span></div>
            <div class="sourceHeaderActions"><button type="button" data-semester-setting="semesterStart">学期设置</button><small id="sourceViewHint" hidden></small><button id="toggleSources" hidden aria-controls="sourceChips" aria-expanded="true">收起文件</button></div>
          </div>
          <div id="draftRemember"></div>
          <div id="sourceChips" class="sourceChips" role="list" aria-label="已选择的课表文件"></div>
        </section>
        <section class="contentCard">
          <div id="resultNotice" class="resultNotice" hidden>
            <div><b id="resultNoticeTitle"></b><span id="resultNoticeDetail"></span></div>
            <button id="nextStepAction">查看待处理</button>
          </div>
          <div class="tabs" role="tablist" aria-label="结果视图">
            ${tabButton("availability", "共同空闲")}
            ${tabButton("members", "成员课表")}
            ${tabButton("files", "文件检查")}
            ${tabButton("issues", "批次总览")}
            ${tabButton("review", "原文核对")}
            ${tabButton("calendar", "课程周视图")}
            <i class="tabIndicator" aria-hidden="true"></i>
          </div>
          <div class="tabBody" id="tabBody">
            ${tabs.map((tab) => `<section id="panel-${tab}" class="tabPanel" data-panel="${tab}" role="tabpanel"></section>`).join("")}
          </div>
        </section>
        <details class="activityLog">
          <summary>操作记录</summary>
          <div id="activityItems"><span>等待操作。</span></div>
        </details>
      </section>
      <dialog id="slotDetail" class="slotDetail" aria-labelledby="slotDetailTitle"><div id="slotDetailBody"></div><form method="dialog"><button>关闭</button></form></dialog>
      ${exportDialog}
    </main>
  `;
  shellMounted = true;
  getElement<HTMLButtonElement>("#cloudEntry").onclick = () => void cloud.open();
  bindActions();
}

function updateShell() {
  cloud.updateEntry(getElement<HTMLButtonElement>("#cloudEntry"));
  const result = state.result;
  if (!result) state.activeTab = "availability";
  if (result?.date_review && !result.can_export && state.activeTab === "availability") state.activeTab = "review";
  const workspace = currentWorkspace();
  if (result) lastWorkspaceTab[workspace] = state.activeTab;
  getElement<HTMLElement>(".shell").dataset.workspace = workspace;
  getElement<HTMLElement>(".shell").dataset.stage = state.stage;
  setText("#workspaceTitle", workspaceTitles[workspace]);
  const scopedSource = state.activeTab === "members" || state.activeTab === "calendar";
  const picker = getElement<HTMLElement>("#sourceDepartmentPicker");
  picker.hidden = !result || !scopedSource;
  if (!picker.hidden && result) setHtmlIfChanged(picker, departmentPicker(memberDepartmentOptions(result), availabilityDepartment, "sourceDepartment"));
  const scopeLabel = result && (workspace === "results" || scopedSource) && availabilityDepartment
    ? `${availabilityDepartment} · ${membersForDepartment(result, availabilityDepartment).length} 位成员` : "";
  const range = result?.date_review;
  getElement<HTMLElement>(".shell").classList.toggle("is-date-review", Boolean(range && state.activeTab === "review"));
  const peoplePending = range?.members.filter(member => !member.confirmed).length;
  const batchLabel = range ? `${range.start_date} 至 ${range.end_date} · ` : "";
  setText("#workspaceMeta", batchLabel + (scopeLabel || (result ? `本批 ${result.summary.member_count || 0} 位成员 · ${result.summary.pdf_count || state.files.length} 份课表` : "")));
  getElement<HTMLElement>("#workspaceMeta").hidden = !result;
  document.querySelectorAll<HTMLButtonElement>("button[data-workspace]").forEach(button => {
    const active = button.dataset.workspace === workspace;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
    button.disabled = button.dataset.workspace !== "sources" && !result
      || button.dataset.workspace === "results" && Boolean(range && !result?.can_export);
  });
  setText("#navFileCount", String(state.files.length));
  getElement<HTMLElement>("#navFileCount").hidden = !state.files.length;
  const openReviewCount = peoplePending ?? (unresolvedIssueCount(result) + (result?.verification?.pending || 0));
  setText("#navIssueCount", String(openReviewCount));
  getElement<HTMLElement>("#navIssueCount").hidden = !openReviewCount;
  if (result && result.result_ref !== renderedResult?.result_ref) sourceListExpanded = false;
  else if (!result && renderedResult) sourceListExpanded = true;
  const quality = result?.quality_state || "pending";
  const invalidCount = state.files.filter((path) => !inferKind(path)).length;
  const desktopUnavailable = state.stage === "failed" && state.statusText.includes("桌面运行环境未连接");
  const installationUnavailable = Boolean(state.startupDiagnostic);
  const startupFailed = state.stage === "failed" && !state.resourceStatus;
  const nativeUnavailable = desktopUnavailable || installationUnavailable || startupFailed;
  if (nativeUnavailable) setDisabled("#cloudEntry", true);
  const hasInput = state.files.length > 0;
  getElement<HTMLElement>(".shell").classList.toggle("has-result", Boolean(result));
  getElement<HTMLElement>(".shell").classList.toggle("has-input", hasInput);
  getElement<HTMLElement>(".shell").classList.toggle("is-working", state.busy);
  getElement<HTMLElement>(".tabs").hidden = !result || workspace === "results";
  getElement<HTMLElement>(".sourceRibbon").hidden = !hasInput || Boolean(result);
  getElement<HTMLElement>("#batchDates").hidden = Boolean(result) || state.busy || nativeUnavailable;
  setInputValue("#batchStart", state.batchDates.start_date);
  setInputValue("#batchEnd", state.batchDates.end_date);
  getElement<HTMLInputElement>("#batchEnd").min = state.batchDates.start_date;
  const calendarHint = getElement<HTMLElement>("#batchCalendarHint");
  calendarHint.hidden = !outsideSemester(state.batchDates, savedCalendarSettings);
  if (!calendarHint.hidden) {
    const term = semesterDates(savedCalendarSettings);
    calendarHint.textContent = `当前校历为 ${term.start_date} 至 ${term.end_date}。请调整合表日期，或在学期设置中更新校历。`;
  }
  getElement<HTMLElement>(".contentCard").hidden = hasInput && !result && !state.busy && state.stage !== "failed";
  getElement<HTMLElement>(".runStrip").hidden = !state.busy || state.stage === "boot";
  getElement<HTMLElement>(".activityLog").hidden = !state.logs.length || Boolean(range && state.activeTab === "review");
  const canParse = state.files.length > 0 && invalidCount === 0 && !state.files.some(needsOriginal) && !state.busy;
  const progressPercent = state.progress.total
    ? Math.min(100, Math.round((state.progress.current / state.progress.total) * 100))
    : state.busy ? 8 : 0;

  const launch = getElement<HTMLElement>("#launchScreen");
  launch.classList.toggle("is-visible", state.stage === "boot");
  launch.setAttribute("aria-hidden", String(state.stage !== "boot"));
  setText("#launchStatus", state.stage === "boot" ? "正在启动…" : state.statusText);

  const detached = state.files.filter(needsOriginal).length;
  const reconnect = getElement<HTMLButtonElement>("#linkOriginals");
  reconnect.hidden = !detached;
  reconnect.disabled = state.busy || nativeUnavailable;
  reconnect.textContent = `关联原件（${detached}）`;
  setHtmlIfChanged(getElement("#draftRemember"), rememberedChoice(rememberSources));
  getElement<HTMLButtonElement>("#applySemester").hidden = !result || !hasInput;
  setDisabled("#applySemester", state.busy || Boolean(detached));
  setText("#saveSettings", result ? "只保存设置" : "保存设置");
  setDisabled("#chooseFiles", state.busy || nativeUnavailable);
  setDisabled("#parseSchedules", !canParse || nativeUnavailable);
  setText("#parseSchedules", result ? "新建合表" : "识别并核对");
  getElement<HTMLButtonElement>("#chooseFiles").hidden = workspace !== "sources" || (!hasInput && !result);
  getElement<HTMLButtonElement>("#clearFiles").hidden = workspace !== "sources" || !hasInput;
  getElement<HTMLButtonElement>("#parseSchedules").hidden = workspace !== "sources" || !hasInput || state.busy;
  getElement<HTMLButtonElement>("#exportExcel").hidden = workspace !== "results";
  const cancel = getElement<HTMLButtonElement>("#cancelJob");
  const retry = getElement<HTMLButtonElement>("#retryFailed");
  cancel.hidden = !state.busy || !state.jobId;
  const canRetry = !state.busy && !nativeUnavailable && Boolean(state.jobId) && (
    state.failedFileCount > 0 || ["failed", "interrupted", "cancelled"].includes(state.stage)
  );
  retry.hidden = !canRetry;
  retry.disabled = !state.jobId || nativeUnavailable;
  setDisabled("#exportExcel", !result?.can_export || state.busy || nativeUnavailable);
  getElement<HTMLButtonElement>("#chooseFiles").classList.toggle("primary", !state.files.length && !state.busy && !result && !nativeUnavailable);
  getElement<HTMLButtonElement>("#parseSchedules").classList.toggle("primary", canParse && !result && !nativeUnavailable);
  getElement<HTMLButtonElement>("#exportExcel").classList.toggle("primary", Boolean(result?.can_export) && !state.busy && !nativeUnavailable);
  setText("#actionGuide", invalidCount ? `${invalidCount} 份文件需注明中方或英方。` : "");
  getElement<HTMLElement>("#actionGuide").hidden = !invalidCount;
  setDisabled("#saveSettings", state.busy || nativeUnavailable);
  setDisabled("#semesterStart", state.busy || nativeUnavailable);
  setDisabled("#teachingWeeks", state.busy || nativeUnavailable);
  setDisabled("#repairResources", state.busy || desktopUnavailable || installationUnavailable);
  setText("#repairResources", startupFailed && !desktopUnavailable && !installationUnavailable ? "重新启动识别服务" : "修复识别功能");
  setDisabled("#historyEntry", state.busy || reviewReloading || nativeUnavailable);
  setDisabled("#clearFiles", state.busy || !state.files.length);
  setInputValue("#semesterStart", state.calendarSettings.semester_start_date);
  setInputValue("#teachingWeeks", String(state.calendarSettings.teaching_weeks));

  const resourceTitle = getElement<HTMLElement>("#resourceTitle");
  resourceTitle.className = state.resourceStatus?.ready && !nativeUnavailable ? "good" : "warning";
  resourceTitle.textContent = installationUnavailable ? "安装组件异常" : desktopUnavailable ? "仅桌面端可用" : startupFailed ? "服务未启动" : state.resourceStatus?.ready ? "已就绪" : !state.resourceStatus ? "正在启动" : "需要修复";
  setText("#resourceDetail", installationUnavailable ? "此问题无法通过识别资源修复解决，请重新安装或联系维护者。" : desktopUnavailable ? "普通浏览器无法运行本地识别，请打开空谷桌面端。" : startupFailed ? "服务启动后才能检查识别资源。" : resourceDetail(state.resourceStatus));
  setText("#statusText", state.statusText);
  getElement<HTMLElement>("#statusText").hidden = state.busy || (state.stage === "failed" && !result) || [
    "准备完成，可以导入课表", "空课表已生成，可以导出", "课表已处理，请先核对标记内容", "发现影响结果的问题，请先修正"
  ].includes(state.statusText);
  const maintenance = getElement<HTMLDetailsElement>("#maintenanceSettings");
  const needsRepair = startupFailed || Boolean(state.resourceStatus && !state.resourceStatus.ready);
  if (needsRepair && maintenance.dataset.needsRepair !== "true") maintenance.open = true;
  maintenance.dataset.needsRepair = String(needsRepair);

  const seal = getElement<HTMLElement>("#qualitySeal");
  seal.hidden = !result && !nativeUnavailable;
  const pendingVerification = peoplePending ?? (result?.verification?.pending || 0);
  const resultNeedsRefresh = Boolean(result && quality === "accepted" && !result.can_export && !pendingVerification);
  seal.className = `qualitySeal ${nativeUnavailable ? "blocked" : (quality === "accepted" && pendingVerification) || resultNeedsRefresh ? "needs_review" : quality}`;
  setText("#qualityTitle", installationUnavailable ? "安装组件未能启动" : desktopUnavailable ? "桌面环境未连接" : startupFailed ? "启动失败" : resultNeedsRefresh ? "需要重新生成" : quality === "accepted" && pendingVerification ? "待人工核验" : result ? resultStatusLabel(quality) : "本地处理");
  setText("#qualityDetail", installationUnavailable ? "请使用完整安装包重新安装" : desktopUnavailable ? "请在桌面端继续" : resultNeedsRefresh ? "请使用当前设置重新生成" : result ? resultStatusDetail(result) : "导入课表后查看");
  getElement<HTMLElement>("#qualityDetail").hidden = true;
  setText("#stageLabel", installationUnavailable ? "需要重新安装" : desktopUnavailable ? "请打开桌面端" : startupFailed ? "启动失败" : stageLabel(state.stage));
  setText("#runHint", state.busy ? state.statusText : runHint(result));
  setText("#fileCount", String(result?.summary.pdf_count || state.files.length));
  setText("#memberCount", String(result?.summary.member_count || 0));
  setText("#issueCount", String(result?.issues.filter((issue) => !issue.confirmed && issue.blocks_export).length || 0));
  const progress = getElement<HTMLElement>("#progressTrack");
  progress.classList.toggle("indeterminate", state.busy && !state.progress.total);
  progress.setAttribute("aria-valuenow", String(progressPercent));
  getElement<HTMLElement>("#progressFill").style.setProperty("--progress-scale", String(progressPercent / 100));
  progress.hidden = !state.busy;
  getElement<HTMLElement>(".runStrip").dataset.state = state.stage;
  const notice = getElement<HTMLElement>("#resultNotice");
  notice.hidden = !result || result.can_export || workspace !== "results";
  notice.dataset.quality = quality;
  const hasIssues = !resultNeedsRefresh && unresolvedIssueCount(result) > 0;
  setText("#resultNoticeTitle", quality === "blocked" ? "仅供预览 · 必须处理的问题尚未解决" : hasIssues || pendingVerification ? "结果待核对" : "结果需要重新生成");
  setText("#resultNoticeDetail", hasIssues ? `${unresolvedIssueCount(result)} 项待处理，核对或修正后才能导出。` : pendingVerification ? `还有 ${pendingVerification} 项课程需要对照原文。`
    : friendlyMessage(result?.errors[0] || result?.warnings[0] || "当前结果暂不可导出，请核对来源和学期设置后重新生成。"));
  setText("#nextStepAction", hasIssues ? "查看批次总览" : pendingVerification ? "继续核验" : state.files.length ? "重新生成" : "重新导入课表");
  setDisabled("#nextStepAction", state.busy || nativeUnavailable);
  getElement<HTMLButtonElement>("#nextStepAction").hidden = hasIssues && state.activeTab === "issues";

  updateRunAgu(result);
  updateSources(invalidCount);
  document.querySelectorAll<HTMLButtonElement>("[data-remove]").forEach((button) => { button.disabled = state.busy; });
  updateTabs(result);
  updateActivity();
  updatePanels(result);
  document.querySelectorAll<HTMLButtonElement>("[data-welcome-action]").forEach((button) => {
    button.disabled = state.busy || nativeUnavailable || (button.dataset.welcomeAction === "generate" && !canParse);
  });
  const semesterSummary = document.querySelector<HTMLElement>("#welcomeSemester");
  if (semesterSummary) semesterSummary.textContent = `${savedCalendarSettings.semester_start_date.replaceAll("-", "/")} 起 · ${savedCalendarSettings.teaching_weeks} 周`;
}

function bindActions() {
  document.querySelector("#linkOriginals")?.addEventListener("click", () => void reconnectOriginals());
  document.querySelector("#applySemester")?.addEventListener("click", async () => {
    if (state.busy || !state.result) return;
    try {
      await saveSettings();
      if (!state.result) await parseSchedules();
      else setStatus("请按校历修改第一周周一或教学周数。若设置正确，请核对提示中的原课。");
    } catch { /* saveSettings displays the reason. */ }
  });
  appRoot.addEventListener("change", event => {
    const input = event.target as HTMLInputElement;
    if (!input.matches("[data-remember-sources]") || state.busy) return;
    rememberSources = input.checked;
    persistDraft();
    document.querySelectorAll<HTMLInputElement>("[data-remember-sources]").forEach(box => box.checked = rememberSources);
  });
  document.querySelector("#chooseFiles")?.addEventListener("click", chooseFiles);
  document.querySelector("#parseSchedules")?.addEventListener("click", () => {
    if (state.result) resetForNewInput("可沿用已选文件，请设置本次合表日期。");
    else void parseSchedules();
  });
  const changeDates = () => {
    if (state.busy || state.result) return;
    state.batchDates = {start_date: getElement<HTMLInputElement>("#batchStart").value, end_date: getElement<HTMLInputElement>("#batchEnd").value};
    if (state.batchDates.start_date && state.batchDates.start_date <= state.batchDates.end_date) persistDraft();
    render();
  };
  document.querySelector("#batchStart")?.addEventListener("change", changeDates);
  document.querySelector("#batchEnd")?.addEventListener("change", changeDates);
  document.querySelector("#batchThisMonth")?.addEventListener("click", () => {
    if (!state.busy && !state.result) { state.batchDates = currentMonth(); persistDraft(); render(); }
  });
  document.querySelector("#cancelJob")?.addEventListener("click", cancelJob);
  document.querySelector("#retryFailed")?.addEventListener("click", retryFailed);
  document.querySelector("#exportExcel")?.addEventListener("click", exportExcel);
  document.querySelector("#nextStepAction")?.addEventListener("click", () => {
    if (state.busy || state.startupDiagnostic) return;
    if (state.result?.quality_state !== "accepted" && unresolvedIssueCount(state.result)) {
      state.activeTab = "issues";
      render();
      onNextFrame(() => document.querySelector<HTMLButtonElement>('[data-tab="issues"]')?.focus());
    } else if (state.result?.verification?.pending) {
      state.activeTab = "review";
      render();
    } else if (state.files.length) void parseSchedules();
    else void chooseFiles();
  });
  document.querySelector("#repairResources")?.addEventListener("click", repairResources);
  document.querySelector("#historyEntry")?.addEventListener("click", openLocalHistory);
  document.querySelector("#saveSettings")?.addEventListener("click", () => {
    if (state.busy) return;
    void saveSettings().catch(() => undefined);
  });
  document.querySelector("#clearFiles")?.addEventListener("click", () => {
    state.files = [];
    resetForNewInput("已清空文件列表，请重新导入课表");
  });
  document.querySelector("#toggleSources")?.addEventListener("click", () => {
    sourceListExpanded = !sourceListExpanded;
    render();
  });
  document.querySelector<HTMLInputElement>("#semesterStart")?.addEventListener("change", (event) => {
    state.calendarSettings.semester_start_date = (event.target as HTMLInputElement).value;
  });
  document.querySelector<HTMLInputElement>("#teachingWeeks")?.addEventListener("change", (event) => {
    state.calendarSettings.teaching_weeks = Number((event.target as HTMLInputElement).value);
  });
  appRoot.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    const workspaceButton = target.closest<HTMLButtonElement>("button[data-workspace]");
    if (workspaceButton && !workspaceButton.disabled) {
      state.activeTab = lastWorkspaceTab[workspaceButton.dataset.workspace as Workspace];
      render();
      return;
    }
    const viewButton = target.closest<HTMLButtonElement>("[data-availability-mode]");
    if (viewButton && state.result) {
      availabilityMode = viewButton.dataset.availabilityMode === "list" ? "list" : "week";
      availabilityPage = 0;
      setHtmlIfChanged(getPanel("availability"), renderPanel("availability", state.result));
      getPanel("availability").querySelector<HTMLButtonElement>(`[data-availability-mode="${availabilityMode}"]`)?.focus();
      return;
    }
    const weekStep = target.closest<HTMLButtonElement>("[data-week-step]");
    if (weekStep && state.result && !weekStep.disabled) {
      const weeks = availabilityWeeks(availabilityForDepartment(state.result, availabilityDepartment).rows);
      const nextWeek = weeks[weeks.indexOf(availabilityWeek) + Number(weekStep.dataset.weekStep)];
      if (nextWeek) {
        availabilityWeek = nextWeek;
        availabilityPage = 0;
        setHtmlIfChanged(getPanel("availability"), renderPanel("availability", state.result));
        const panel = getPanel("availability");
        (panel.querySelector<HTMLElement>(`[data-week-step="${weekStep.dataset.weekStep}"]:not(:disabled)`) || panel.querySelector<HTMLElement>("#availabilityWeek"))?.focus();
      }
      return;
    }
    const slotButton = target.closest<HTMLButtonElement>("[data-slot-index]");
    if (slotButton && state.result) {
      const row = availabilityForDepartment(state.result, availabilityDepartment).rows[Number(slotButton.dataset.slotIndex)];
      if (row) {
        getElement<HTMLElement>("#slotDetailBody").innerHTML = availabilitySlotDetail(row, availabilityDepartment);
        getElement<HTMLDialogElement>("#slotDetail").showModal();
      }
      return;
    }
    const filterButton = target.closest<HTMLButtonElement>("[data-review-filter]");
    if (filterButton && state.result) {
      reviewFilter = filterButton.dataset.reviewFilter as ReviewFilter;
      setHtmlIfChanged(getPanel("issues"), renderPanel("issues", state.result));
      getPanel("issues").querySelector<HTMLButtonElement>(`[data-review-filter="${reviewFilter}"]`)?.focus();
      return;
    }
    const openSource = target.closest<HTMLButtonElement>("[data-open-source]");
    if (openSource) {
      pendingReviewSource = openSource.dataset.openSource || "";
      pendingReviewBlock = openSource.dataset.openBlock || "";
      state.activeTab = "review";
      render();
      return;
    }
    const welcomeAction = target.closest<HTMLButtonElement>("[data-welcome-action]");
    if (welcomeAction && !welcomeAction.disabled) {
      if (welcomeAction.dataset.welcomeAction === "generate") void parseSchedules();
      else void chooseFiles();
      return;
    }
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
        if (chip && !chip.isConnected) return;
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
    const settingLink = target.closest<HTMLButtonElement>("[data-semester-setting]");
    if (settingLink) {
      getElement<HTMLDetailsElement>("#semesterSettings").open = true;
      const field = getElement<HTMLInputElement>(settingLink.dataset.semesterSetting === "semesterStart" ? "#semesterStart" : "#teachingWeeks");
      field.scrollIntoView({ block: "center" });
      field.focus({ preventScroll: true });
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
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('.tabs [data-tab]');
    if (!button || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const visibleTabs = workspaceTabs[currentWorkspace()];
    const current = visibleTabs.indexOf(button.dataset.tab as Tab);
    const next = event.key === "Home" ? 0
      : event.key === "End" ? visibleTabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + visibleTabs.length) % visibleTabs.length;
    state.activeTab = visibleTabs[next];
    render();
    onNextFrame(() => document.querySelector<HTMLButtonElement>(`[data-tab="${state.activeTab}"]`)?.focus());
  });
  appRoot.addEventListener("change", event => {
    const input = event.target as HTMLSelectElement;
    if (!state.result) return;
    if (input.id === "availabilityDepartment" || input.id === "sourceDepartment") {
      availabilityDepartment = input.value;
      availabilityPage = 0;
      setHtmlIfChanged(getPanel("availability"), renderPanel("availability", state.result));
      setHtmlIfChanged(getPanel("members"), renderPanel("members", state.result));
      render();
      const focusId = input.id;
      onNextFrame(() => document.getElementById(focusId)?.focus());
      return;
    }
    if (input.id !== "availabilityWeek") return;
    availabilityWeek = Number(input.value);
    availabilityPage = 0;
    setHtmlIfChanged(getPanel("availability"), renderPanel("availability", state.result));
    getPanel("availability").querySelector<HTMLSelectElement>("#availabilityWeek")?.focus();
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
  getElement<HTMLElement>(".sourceRibbon").classList.toggle("is-empty", !state.files.length);
  getElement<HTMLElement>("#sourceChips").hidden = Boolean(state.result) && !sourceListExpanded;
  setText("#toggleSources", sourceListExpanded ? "收起文件" : "查看文件");
  getElement<HTMLButtonElement>("#toggleSources").setAttribute("aria-expanded", String(sourceListExpanded));
  setText("#sourceSummary", state.files.length
    ? `${state.files.length} 份 PDF${invalidCount ? ` · ${invalidCount} 份需注明课表类型` : ""}`
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
  document.querySelectorAll<HTMLButtonElement>('.tabs [data-tab]').forEach((button) => {
    const tab = button.dataset.tab as Tab;
    const active = tab === state.activeTab;
    button.hidden = !workspaceTabs[currentWorkspace()].includes(tab);
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
    const detail = getElement<HTMLDialogElement>("#slotDetail");
    if (detail.open) detail.close();
    const refreshingSameResult = Boolean(result && previousResultRef === result.result_ref);
    // 同一结果内的修正刷新要保留各表格的滚动位置，只有换任务结果才回到顶部第一页。
    const scrollPositions = refreshingSameResult ? captureTableScroll() : null;
    renderedResult = result;
    if (!result || result.result_ref !== previousResultRef) {
      availabilityPage = 0;
      availabilityDepartment = "";
    } else if (availabilityDepartment && !memberDepartmentOptions(result).includes(availabilityDepartment)) {
      availabilityDepartment = "";
    }
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

  if (!result) {
    const nextEmptyKey = `${state.stage === "failed" ? state.statusText : ""}:${state.busy}:${state.files.length}`;
    if (nextEmptyKey !== emptyPanelKey) {
      emptyPanelKey = nextEmptyKey;
      setHtmlIfChanged(getPanel("availability"), renderWelcome(state.stage !== "failed"));
    }
  } else emptyPanelKey = "";

  if (state.activeTab === "issues" && result && state.review && renderedOverviewReview !== state.review) {
    renderedOverviewReview = state.review;
    setHtmlIfChanged(getPanel("issues"), renderPanel("issues", result));
  }
  if (reviewController && state.review && reviewPayload !== state.review) {
    reviewPayload = state.review;
    void reviewController.update(state.review);
  }
  if (state.activeTab === "review" && state.review) {
    ensureReview();
    if (reviewController && pendingReviewSource) {
      const path = pendingReviewSource;
      pendingReviewSource = "";
      const block = pendingReviewBlock;
      pendingReviewBlock = "";
      void reviewController.selectSource(path, block).catch(error => setStatus(formatError(error)));
    }
  }
  if (state.activeTab === "calendar" && result) ensureCalendar(result);
}

function ensureReview() {
  if (reviewController || reviewMount || !state.review) return;
  const panel = getPanel("review");
  const generation = richViewGeneration;
  panel.innerHTML = `<div class="loadingPanel"><span class="loadingDot" aria-hidden="true"></span>正在载入课表原文…</div>`;
  const initialPayload = state.review;
  const currentMount = mountReview(panel, initialPayload, { reconnect: reconnectOriginals, readOriginal: (source) => readLinkedPdf(client, initialPayload.job_id, source.content_hash, source.file_name), apply: applyReview, confirm: confirmIssue, mutate: mutateReview, verify: verifyItem, dateCommand, rangeComplete, result: () => state.result, ai: requestAi })
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
      if (pendingReviewSource) {
        const path = pendingReviewSource;
        pendingReviewSource = "";
        const block = pendingReviewBlock;
        pendingReviewBlock = "";
        await controller.selectSource(path, block).catch(error => setStatus(formatError(error)));
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
  const members = new Set(membersForDepartment(result, availabilityDepartment).map(member => member.member_key));
  const courses = availabilityDepartment ? result.courses.filter(course => members.has(course.member_key)) : result.courses;
  calendar = mountCalendar(host, courses, state.calendarSettings, async (block, patch) => {
    for (const [field, value] of Object.entries(patch)) {
      await applyReview(block.block_id, field, value, "周课表拖拽修正");
    }
  }, initialDate, (message) => setStatus(message), showCourseSlot, calendarScope);
  calendarKey = key;
  onNextFrame(() => calendar?.updateSize());
}

function showCourseSlot(slot: CourseSlot) {
  const distinct = new Map(slot.courses.map(course => [
    JSON.stringify([course.member_key || course.name, course.course, course.source_file]), course
  ]));
  const body = getElement<HTMLElement>("#slotDetailBody");
  body.innerHTML = `<h2 id="slotDetailTitle">${escapeHtml(slot.day)} · 第 ${slot.period} 节</h2>
    <p>${slot.memberCount} 人有课</p><ul class="courseRoster">${[...distinct.values()].map(course =>
      `<li><strong>${escapeHtml(course.name)}</strong><span>${escapeHtml(course.course || "课程")}</span>
        <small>${escapeHtml([course.department, course.role].filter(Boolean).join(" · "))}</small></li>`).join("")}</ul>`;
  getElement<HTMLDialogElement>("#slotDetail").showModal();
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
  pendingReviewSource = "";
  pendingReviewBlock = "";
  richViewGeneration += 1;
  reviewController?.destroy();
  reviewController = null;
  reviewMount = null;
  reviewPayload = null;
  calendar?.destroy();
  calendar = null;
  calendarKey = "";
  calendarViewDate = null;
  calendarScope.member = "";
}

function calendarSignature(result: ParseResult) {
  const courseKey = result.courses.map((course) => [
    course.block_id,
    course.week,
    course.weekday,
    course.periods.join(","),
    course.course || ""
  ].join(":")).join("|");
  return `${result.result_ref}|${availabilityDepartment}|${courseKey}|${state.calendarSettings.semester_start_date}|${state.calendarSettings.teaching_weeks}`;
}

function getPanel(tab: Tab) {
  return getElement<HTMLElement>(`#panel-${tab}`);
}

function renderWelcome(showAgu = true) {
  if (!showAgu) return `<div class="emptyPanel taskEmpty taskEmpty--failed"><span class="taskMarker" aria-hidden="true">!</span>
    <b>${state.resourceStatus ? "暂时无法完成处理" : "本地识别服务未能启动"}</b><span>${escapeHtml(state.statusText)}</span>
    <p>${state.startupDiagnostic ? "请使用完整安装包重新安装；仍失败时，将下方技术详情提供给维护者。" : state.statusText.includes("桌面运行环境") ? "请打开空谷桌面端导入课表。" : !state.resourceStatus ? "可在左侧“本地维护”中重新启动识别服务。" : "请根据上述原因处理后重新生成。"}</p>
    ${state.startupDiagnostic ? `<details><summary>查看脱敏技术详情</summary><pre style="max-width:560px;white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;font-size:11px">${escapeHtml(state.startupDiagnostic)}</pre></details>` : ""}</div>`;
  if (state.busy) return `<div class="emptyPanel taskEmpty"><span class="loadingDot" aria-hidden="true"></span>
    <b>正在处理课表</b><span>处理期间请保留原文件。</span></div>`;
  return `<div class="emptyPanel welcomePanel">
    <div class="welcomeCopy">
      <h2>${state.files.length ? "课表已选好" : "导入课表"}</h2>
      <p>${state.files.length ? `已选择 ${state.files.length} 份课表，确认学期设置后开始生成。` : "每位成员需提供中方、英方两份 PDF 课表。"}</p>
      <button type="button" class="primary welcomeAction" data-welcome-action="${state.files.length ? "generate" : "choose"}">${state.files.length ? "生成空课表" : "选择课表文件"}</button>
      ${!state.files.length ? rememberedChoice(rememberSources) : ""}
      <div class="welcomeSemester"><button type="button" data-semester-setting="semesterStart">学期设置</button><span id="welcomeSemester"></span></div>
    </div>
    ${showAgu ? renderAguMotion("welcome") : ""}
  </div>`;
}

function renderPanel(tab: Tab, result: ParseResult) {
  if (tab === "members") {
    const members = membersForDepartment(result, availabilityDepartment);
    return table(members, [
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
    const overview = renderReviewOverview(result, state.review, reviewFilter);
    if (!result.issues.length) return overview;
    return `${overview}<details class="batchIssueDetails"><summary>全部检查记录（${result.issues.length}）</summary><div class="issueTable">${result.issues.map((issue) => `<article class="${escapeHtml(issue.confirmed ? "confirmed" : issue.severity)}">
      <div class="issueHeading"><b>${issueStateLabel(issue)}</b><span>${escapeHtml((issue.source_file || "本批课表").split(/[\\/]/).pop() || "本批课表")}</span></div>
      <h3>${escapeHtml(friendlyMessage(issue.message))}</h3><p>${escapeHtml(friendlyMessage(issue.suggestion || "请在“原文核对”中对照课表。"))}</p>
      ${issue.source_file ? `<button data-open-source="${escapeHtml(issue.source_file)}">查看原文</button>` : ""}
      ${issueAction(issue)}
    </article>`).join("")}</div></details>`;
  }
  if (tab === "review") return `<div class="loadingPanel">正在载入课表原文…</div>`;
  if (tab === "calendar") return `<div class="calendarHost"></div>`;
  const scoped = availabilityForDepartment(result, availabilityDepartment);
  const weeks = availabilityWeeks(scoped.rows);
  if (!weeks.includes(availabilityWeek)) availabilityWeek = weeks[0] || 0;
  const departments = departmentOptions(result);
  if (availabilityDepartment && !departments.includes(availabilityDepartment)) departments.push(availabilityDepartment);
  const toolbar = availabilityToolbar(scoped.rows, availabilityWeek, availabilityMode, departments, availabilityDepartment);
  const rows = scoped.rows.filter(row => Number(row["周次"]) === availabilityWeek);
  const pageSize = 800;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(availabilityPage, pageCount - 1);
  const pageRows = rows.slice(page * pageSize, (page + 1) * pageSize);
  const columns = ["周次", "日期", "星期", "节次", "时间", "空闲人数", "空闲人员", "占用人数", "有课人员"];
  const pager = availabilityPager(rows.length, page, pageCount);
  const coverage = result.issues.find((issue) => issue.code === "TIMETABLE_COVERAGE_LIMIT" || issue.code === "TIMETABLE_COVERAGE_EMPTY");
  const coverageNote = coverage ? `<p class="inputTip" role="note">${escapeHtml(coverage.message)} ${escapeHtml(coverage.suggestion || "")}</p>` : "";
  const content = availabilityMode === "week" ? availabilityWeekGrid(scoped.rows, availabilityWeek, scoped.memberCount, Boolean(result.date_review)) : `${pager}${table(pageRows, columns)}${pager}`;
  return `${coverageNote}${toolbar}<div class="availabilityBody">${content}</div>`;
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
  const cellClass = (key: string) => ["周次", "空闲人数", "占用人数", "page_count", "course_block_count"].includes(key)
    ? "numberCell" : ["日期", "星期", "节次", "时间", "source_type"].includes(key) ? "nowrapCell" : "";
  return `<div class="tableWrap"><table><thead><tr>${normalized.map((column) => `<th scope="col" class="${cellClass(column.key)}">${escapeHtml(column.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${normalized.map((column) => `<td class="${cellClass(column.key)}">${column.key === "status" ? `<span class="tableStatus" data-status="${escapeHtml(String(row[column.key] || ""))}">${escapeHtml(tableCell(row[column.key]))}</span>` : escapeHtml(tableCell(row[column.key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
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
  return `<span class="sourceChip ${kind ? "" : "invalid"}" role="listitem"><i>${kind || "待检查"}</i><b title="${escapeHtml(name)}">${escapeHtml(name)}</b><button type="button" aria-label="移除课表 ${escapeHtml(name)}" title="移除这份课表" data-remove="${escapeHtml(path)}">移除</button></span>`;
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
  const pending = state.result?.date_review?.members.filter(member => !member.confirmed).length;
  if (pending) return `识别结果待确认，请逐人对照原件。还有 ${pending} 位成员待核对。`;
  if (state.result && !state.result.verification) return "当前识别组件未提供逐项核验，请使用配套的新版空谷。";
  if (stateValue === "needs_review") return "课表已处理，请先核对标记内容";
  if (stateValue === "blocked") return "发现影响结果的问题，请先修正";
  if (state.result?.verification?.pending) return `还有 ${state.result.verification.pending} 项课程待人工核验`;
  if (state.result && !state.result.can_export) return "当前结果暂不可导出，请重新生成";
  return "空课表已生成，可以导出";
}

function unresolvedIssueCount(result: ParseResult | null) {
  return result?.issues.filter((issue) => !issue.confirmed && issue.blocks_export).length || 0;
}

function resultStatusDetail(result: ParseResult) {
  const count = unresolvedIssueCount(result);
  if (result.can_export) return "所有问题均已处理";
  if (result.quality_state === "blocked") return `${count} 项必须先修正`;
  if (result.verification?.pending) return `${result.verification.pending} 项课程待核验`;
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
  if (issue.confirmed || issue.blocks_export === false) return "";
  if (issue.code === "WEEK_OUT_OF_SEMESTER" || issue.code === "PRE_TERM_DATE") {
    const field = issue.code === "PRE_TERM_DATE" ? "semesterStart" : "teachingWeeks";
    return `<button type="button" data-semester-setting="${field}">前往学期设置</button>`;
  }
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

function isInstallationStartupError(message: string, startup: boolean) {
  if (/not configured[\s\S]*tauri\.conf\.json[\s\S]*externalBin/i.test(message)) return true;
  const component = /sidecar|konggu-worker|binaries[\\/]konggu-worker/i.test(message);
  const missing = /not found|no such file|cannot find|failed to spawn|os error (?:2|3|193)\b|系统找不到|无法找到|未找到/i.test(message);
  const bareMissing = /^(?:Error:\s*)?(?:No such file or directory|program not found|系统找不到指定的文件)[。.]?\s*(?:\(os error [23]\))?\s*$/i.test(message);
  return (component && missing) || (startup && bareMissing);
}

function redactStartupDiagnostic(message: string) {
  const codes = message.match(/(?:os error\s*\d+|WinError\s*\d+|ENOENT)/gi) || [];
  let detail = message.replace(/(?:[A-Za-z]:[\\/]|\\\\)[^\r\n"'<>]+/g, "<本地路径>")
    .replace(/Bearer\s+[^\s]+|(?:token|password|secret)\s*[=:]\s*[^\s,;]+/gi, "<已隐藏>");
  for (const code of codes) if (!detail.includes(code)) detail += ` (${code})`;
  return detail.slice(0, 900);
}

function formatError(error: unknown, startup = false) {
  const message = error instanceof Error ? error.message : String(error);
  if (isInstallationStartupError(message, startup)) {
    state.startupDiagnostic = redactStartupDiagnostic(message);
    return INSTALLATION_STARTUP_MESSAGE;
  }
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
  if (!state.busy || state.stage === "boot") return;
  event.preventDefault();
  event.returnValue = "课表仍在处理中，关闭窗口会中断任务。";
});

// 页面刷新（Ctrl+R/F5）会重新执行脚本：先结束旧 worker，避免两个进程同时写同一份状态。
window.addEventListener("pagehide", () => {
  void client.shutdown();
});
