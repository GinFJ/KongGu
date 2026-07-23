import { open, save } from "@tauri-apps/plugin-dialog";
import type { Calendar } from "@fullcalendar/core";
import wordmarkUrl from "../assets/konggu-wordmark.png";
import { mountCalendar } from "./calendarView";
import { mountReview } from "./reviewView";
import { SidecarClient } from "./sidecarClient";
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
  statusText: "正在连接离线解析引擎",
  stage: "boot",
  progress: { current: 0, total: 0 },
  logs: []
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("App root not found.");
let calendar: Calendar | null = null;

client.onEvent((event) => {
  if (event.event === "sidecar.fatal") {
    state.statusText = `解析引擎启动失败：${event.message}`;
    state.busy = false;
    render();
    return;
  }
  if (state.jobId && event.job_id !== state.jobId) return;
  state.stage = event.stage;
  state.progress = { current: event.current, total: event.total };
  state.statusText = event.message;
  log(event.message, false);
  render();
});

async function boot() {
  render();
  try {
    await client.start();
    const [resources, settings] = await Promise.all([
      client.request<ResourceStatus>("resources.status"),
      client.request<{ ok: boolean; settings: CalendarSettings }>("settings.calendar.get")
    ]);
    state.resourceStatus = resources;
    state.calendarSettings = settings.settings;
    state.statusText = resources.ready ? "离线材料已就绪，可以导入课表" : "离线材料不完整，请先修复";
    state.stage = "ready";
  } catch (error) {
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
  state.stage = "queued";
  state.statusText = "正在建立持久任务";
  render();
  try {
    await saveSettings(false);
    const started = await client.request<{ ok: boolean; job_id: string }>("job.start", { paths: state.files });
    state.jobId = started.job_id;
    log(`任务 ${state.jobId.slice(0, 8)} 已建立`);
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
      state.result = job.output || null;
      state.busy = false;
      if (state.result?.detected_max_week) {
        state.calendarSettings.teaching_weeks = state.result.detected_max_week;
      }
      state.review = await client.request<ReviewPayload>("review.get", { job_id: jobId });
      state.activeTab = state.result?.can_export ? "availability" : "issues";
      state.statusText = state.result?.can_export
        ? "解析完成，质量门禁已通过"
        : `解析完成：${qualityLabel(state.result?.quality_state || "blocked")}`;
      log(state.statusText);
      render();
      return;
    }
    if (["failed", "cancelled", "interrupted"].includes(job.status)) {
      state.busy = false;
      state.statusText = job.error || `任务状态：${job.status}`;
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
  state.statusText = "正在等待当前 OCR 页结束";
  state.stage = "cancelling";
  render();
  await client.request("job.cancel", { job_id: jobId }).catch(() => undefined);
  window.setTimeout(async () => {
    if (state.busy && state.jobId === jobId && state.stage === "cancelling") {
      state.statusText = "软取消超时，正在重启解析引擎";
      render();
      await client.forceRestart();
      state.busy = false;
      state.stage = "interrupted";
      state.statusText = "任务已中断，可点击“重试失败文件”。";
      render();
    }
  }, 10_000);
}

async function retryFailed() {
  if (!state.jobId || state.busy) return;
  state.busy = true;
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
  if (!state.result?.result_ref) return setStatus("请先完成解析。");
  if (!state.result.can_export) {
    state.activeTab = "issues";
    return setStatus("质量门禁未通过；请先确认或修正所有阻塞项。");
  }
  const target = await save({
    defaultPath: "Konggu_availability.xlsx",
    filters: [{ name: "Excel", extensions: ["xlsx"] }]
  });
  if (!target) return;
  state.busy = true;
  state.statusText = "正在执行导出前质量复核";
  render();
  try {
    const response = await client.request<{ ok: boolean; path: string }>("exports.excel", {
      result_ref: state.result.result_ref,
      target_path: target,
      mode: "classic",
      export_week_count: state.calendarSettings.teaching_weeks
    });
    state.statusText = `可信 Excel 已导出：${response.path}`;
    log(state.statusText);
  } catch (error) {
    state.statusText = `导出被阻止：${formatError(error)}`;
    state.activeTab = "issues";
  } finally {
    state.busy = false;
    render();
  }
}

async function repairResources() {
  state.busy = true;
  setStatus("正在从安装包校验并修复离线材料");
  try {
    const response = await client.request<{ ok: boolean; status: ResourceStatus; copied: string[] }>("resources.repair");
    state.resourceStatus = response.status;
    state.statusText = response.status.ready ? `修复完成，复制 ${response.copied.length} 项` : "仍有资源未通过校验";
  } catch (error) {
    state.statusText = formatError(error);
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
    state.statusText = qualityLabel(state.result.quality_state);
  }
  render();
}

function render() {
  calendar?.destroy();
  calendar = null;
  const result = state.result;
  const quality = result?.quality_state || "blocked";
  const invalidCount = state.files.filter((path) => !inferKind(path)).length;
  const canParse = state.files.length > 0 && invalidCount === 0 && !state.busy;
  const progressPercent = state.progress.total
    ? Math.min(100, Math.round((state.progress.current / state.progress.total) * 100))
    : state.busy ? 8 : 0;

  app.innerHTML = `
    <main class="shell">
      <aside class="rail">
        <div class="brand"><img src="${wordmarkUrl}" alt="空谷" /><span>排班复核工作台</span></div>
        <nav class="primaryActions">
          <button id="chooseFiles" class="primary" ${state.busy ? "disabled" : ""}>＋ 导入课表 PDF</button>
          <button id="parseSchedules" ${canParse ? "" : "disabled"}>开始解析与质检</button>
          ${state.busy ? `<button id="cancelJob" class="danger">取消当前任务</button>` : `<button id="retryFailed" ${state.jobId ? "" : "disabled"}>重试失败文件</button>`}
          <button id="exportExcel" ${!result?.can_export || state.busy ? "disabled" : ""}>导出可信 Excel</button>
        </nav>
        <section class="railSection">
          <div class="sectionLabel">学期边界</div>
          <label>第 1 周周一<input id="semesterStart" type="date" value="${state.calendarSettings.semester_start_date}" /></label>
          <label>教学周数<input id="teachingWeeks" type="number" min="1" max="30" value="${state.calendarSettings.teaching_weeks}" /></label>
          <button id="saveSettings" ${state.busy ? "disabled" : ""}>保存设置</button>
        </section>
        <section class="railSection resourceState">
          <div class="sectionLabel">离线资源</div>
          <strong class="${state.resourceStatus?.ready ? "good" : "warning"}">${state.resourceStatus?.ready ? "完整并通过校验" : "需要修复"}</strong>
          <small>${state.resourceStatus?.ocr.ready ? "PP-OCRv4 CPU 模型可用" : "OCR 模型未就绪"}</small>
          <button id="repairResources" ${state.busy ? "disabled" : ""}>修复离线材料</button>
        </section>
        <div class="privacyMark">原始 PDF 仅在本机读取<br/>数据库不复制源文件</div>
      </aside>
      <section class="workbench">
        <header class="masthead">
          <div>
            <span class="eyebrow">QINGHE · KONGGU / 空谷</span>
            <h1>从“识别出来”到“确认可信”</h1>
            <p>${escapeHtml(state.statusText)}</p>
          </div>
          <div class="qualitySeal ${quality}">
            <span>${result ? qualityLabel(quality) : "尚未质检"}</span>
            <small>${result?.parser_signature ? `签名 ${result.parser_signature.slice(0, 10)}` : "等待解析结果"}</small>
          </div>
        </header>
        <section class="runStrip">
          <div class="runMeta"><b>${stageLabel(state.stage)}</b><span>${state.jobId ? `任务 ${state.jobId.slice(0, 8)}` : "未建立任务"}</span></div>
          <div class="progressTrack"><i style="width:${progressPercent}%"></i></div>
          <div class="runCounts"><strong>${result?.summary.pdf_count || state.files.length}</strong><span>文件</span><strong>${result?.summary.member_count || 0}</strong><span>成员</span><strong>${result?.issues.filter((issue) => !issue.confirmed).length || 0}</strong><span>待复核</span></div>
        </section>
        <section class="sourceRibbon">
          <div class="sourceHeader"><b>本次来源</b><span>${state.files.length ? `${state.files.length} 份 PDF · ${invalidCount ? `${invalidCount} 份类型不明` : "命名检查通过"}` : "尚未选择文件"}</span><button id="clearFiles" ${state.busy || !state.files.length ? "disabled" : ""}>清空</button></div>
          <div class="sourceChips">${state.files.length ? state.files.map(fileChip).join("") : `<span class="emptySource">导入中方与英方课表后，空谷会顺序解析并建立可复核记录。</span>`}</div>
        </section>
        <section class="contentCard">
          <div class="tabs">
            ${tabButton("availability", "多人空闲", result?.availability_preview.length)}
            ${tabButton("members", "成员身份", result?.members.length)}
            ${tabButton("files", "文件处理", result?.details.length)}
            ${tabButton("issues", "问题门禁", result?.issues.length)}
            ${tabButton("review", "PDF 复核", state.review?.blocks.length)}
            ${tabButton("calendar", "交互周课表", result?.courses.length)}
          </div>
          <div class="tabBody" id="tabBody">${renderTabShell()}</div>
        </section>
        <footer class="activityLog">
          <b>审计轨迹</b>
          <div>${state.logs.slice(0, 3).map((item) => `<span>${escapeHtml(item)}</span>`).join("") || "<span>等待操作。</span>"}</div>
        </footer>
      </section>
    </main>
  `;

  bindActions();
  void mountActiveTab();
}

function bindActions() {
  document.querySelector("#chooseFiles")?.addEventListener("click", chooseFiles);
  document.querySelector("#parseSchedules")?.addEventListener("click", parseSchedules);
  document.querySelector("#cancelJob")?.addEventListener("click", cancelJob);
  document.querySelector("#retryFailed")?.addEventListener("click", retryFailed);
  document.querySelector("#exportExcel")?.addEventListener("click", exportExcel);
  document.querySelector("#repairResources")?.addEventListener("click", repairResources);
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
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab as Tab;
      render();
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      state.files = state.files.filter((path) => path !== button.dataset.remove);
      render();
    });
  });
}

async function mountActiveTab() {
  const body = document.querySelector<HTMLElement>("#tabBody");
  if (!body) return;
  if (state.activeTab === "review" && state.review) {
    try {
      await mountReview(body, state.review, { apply: applyReview, confirm: confirmIssue });
    } catch (error) {
      body.innerHTML = `<div class="emptyPanel">PDF 加载失败：${escapeHtml(formatError(error))}</div>`;
    }
  }
  if (state.activeTab === "calendar" && state.result) {
    calendar = mountCalendar(body, state.result.courses, state.calendarSettings, async (block, patch) => {
      for (const [field, value] of Object.entries(patch)) {
        await applyReview(block.block_id, field, value, "周课表拖拽修正");
      }
    });
  }
}

function renderTabShell() {
  const result = state.result;
  if (!result) return `<div class="emptyPanel"><b>尚无结构化结果</b><span>完成解析后可在这里查看空闲槽、质量问题、PDF 坐标和交互式周课表。</span></div>`;
  if (state.activeTab === "members") {
    return table(result.members, ["member", "department", "role", "member_key", "chinese_schedule", "english_schedule", "course_block_count", "status"]);
  }
  if (state.activeTab === "files") {
    return table(result.details, ["filename", "member", "source_type", "layout_profile", "course_block_count", "quality_state", "status", "warning"]);
  }
  if (state.activeTab === "issues") {
    if (!result.issues.length) return `<div class="emptyPanel"><b>没有质量问题</b><span>当前结果可以进入正式导出。</span></div>`;
    return `<div class="issueTable">${result.issues.map((issue) => `<article class="${issue.confirmed ? "confirmed" : issue.severity}">
      <div><code>${escapeHtml(issue.code)}</code><b>${issue.confirmed ? "已确认" : issue.severity === "error" ? "阻断" : "待确认"}</b></div>
      <h3>${escapeHtml(issue.message)}</h3><p>${escapeHtml(issue.suggestion || "请在 PDF 复核页核对来源。")}</p>
      ${issue.confirmed ? "" : `<button data-confirm-inline="${issue.issue_id}">确认已核对</button>`}
    </article>`).join("")}</div>`;
  }
  if (state.activeTab === "review") return `<div class="loadingPanel">正在载入本地 PDF 复核画布…</div>`;
  if (state.activeTab === "calendar") return `<div class="calendarHost"></div>`;
  return table(result.availability_preview.slice(0, 800), ["周次", "日期", "星期", "节次", "时间", "空闲人数", "空闲人员", "占用人数", "有课人员"]);
}

function table(rows: Array<Record<string, unknown>>, columns: string[]) {
  if (!rows.length) return `<div class="emptyPanel">暂无数据。</div>`;
  return `<div class="tableWrap"><table><thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(String(row[column] ?? ""))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function tabButton(tab: Tab, label: string, count?: number) {
  return `<button data-tab="${tab}" class="${state.activeTab === tab ? "active" : ""}">${label}${typeof count === "number" ? `<em>${count}</em>` : ""}</button>`;
}

function fileChip(path: string) {
  const name = path.split(/[\\/]/).pop() || path;
  const kind = inferKind(path);
  return `<span class="sourceChip ${kind ? "" : "invalid"}"><i>${kind || "?"}</i><b>${escapeHtml(name)}</b><button data-remove="${escapeHtml(path)}">×</button></span>`;
}

function inferKind(path: string) {
  const text = path.toLowerCase();
  if (path.includes("英方") || text.includes("english") || text.includes("uk")) return "英方";
  if (path.includes("中方") || text.includes("chinese")) return "中方";
  return "";
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    boot: "启动", ready: "就绪", queued: "排队", waiting: "等待", text_layer: "读取文本层",
    profile: "识别版式", ocr: "离线 OCR", course_parse: "课程解析", review: "人工复核",
    completed: "完成", failed: "失败", cancelling: "正在取消", cancelled: "已取消", interrupted: "已中断"
  };
  return labels[stage] || stage;
}

function qualityLabel(stateValue: string) {
  if (stateValue === "accepted") return "绿色 · 可导出";
  if (stateValue === "needs_review") return "黄色 · 待人工确认";
  return "红色 · 阻止导出";
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
  if (message.includes("__TAURI") || message.includes("invoke") || message.includes("Command")) {
    return "桌面运行环境未连接，请在 Konggu 桌面端中使用该功能。";
  }
  return message.replace(/^Error:\s*/, "");
}

function escapeHtml(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

document.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-confirm-inline]");
  if (button) void confirmIssue(button.dataset.confirmInline || "");
});

void boot();
