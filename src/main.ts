import { open, save } from "@tauri-apps/plugin-dialog";
import { Command } from "@tauri-apps/plugin-shell";
import wordmarkUrl from "../assets/konggu-wordmark.png";
import "./styles.css";

type ResourceStatus = {
  ready: boolean;
  app_data_root: string;
  resources_root: string;
  ocr_models_root: string;
  missing: Array<{ target: string; kind: string; issue: string }>;
  invalid: Array<{ target: string; kind: string; issue: string }>;
  ocr: { ready: boolean; models: Array<{ name: string; path: string; exists: boolean; valid: boolean }> };
};

type ParseResult = {
  ok: boolean;
  result_ref: string;
  summary: Record<string, number>;
  detected_weeks: number[];
  detected_week_count: number;
  detected_max_week: number;
  availability_preview: Array<Record<string, unknown>>;
  members: Array<Record<string, unknown>>;
  details: Array<Record<string, unknown>>;
  warnings: string[];
  errors: string[];
};

type CalendarSettings = {
  semester_start_date: string;
  teaching_weeks: number;
};

type AppState = {
  files: string[];
  resourceStatus: ResourceStatus | null;
  calendarSettings: CalendarSettings;
  result: ParseResult | null;
  activeTab: "availability" | "members" | "details";
  busy: boolean;
  statusText: string;
  logs: string[];
};

type FileView = {
  file: string;
  kind: string;
  member: string;
  missingPair: string;
};

const state: AppState = {
  files: [],
  resourceStatus: null,
  calendarSettings: {
    semester_start_date: "2026-03-02",
    teaching_weeks: 18
  },
  result: null,
  activeTab: "availability",
  busy: false,
  statusText: "正在检查离线材料",
  logs: []
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("App root not found.");
}

function log(message: string) {
  state.logs = [`${new Date().toLocaleTimeString()} ${message}`, ...state.logs].slice(0, 80);
  render();
}

async function callSidecar<T>(command: string, payload: Record<string, unknown> = {}): Promise<T> {
  const request = JSON.stringify({ command, ...payload });
  const output = await Command.sidecar("binaries/konggu-worker", ["--request-json", request]).execute();
  const raw = output.stdout.trim() || output.stderr.trim();
  if (!raw) {
    throw new Error("sidecar 没有返回数据。");
  }
  const parsed = JSON.parse(raw);
  if (!parsed.ok) {
    throw new Error(parsed.error || "sidecar 执行失败。");
  }
  return parsed as T;
}

async function refreshResources() {
  state.busy = true;
  state.statusText = "正在检查离线材料";
  render();
  try {
    state.resourceStatus = await callSidecar<ResourceStatus>("resources.status");
    state.statusText = state.resourceStatus.ready ? "离线材料已就绪" : "离线材料缺失，可点击修复";
    log(state.statusText);
  } catch (error) {
    state.statusText = `材料检查暂不可用：${formatSidecarError(error)}`;
    log(state.statusText);
  } finally {
    state.busy = false;
    render();
  }
}

async function repairResources() {
  state.busy = true;
  state.statusText = "正在从安装包修复离线材料";
  render();
  try {
    const repaired = await callSidecar<{ status: ResourceStatus; copied: string[] }>("resources.repair");
    state.resourceStatus = repaired.status;
    state.statusText = state.resourceStatus.ready ? "离线材料已修复" : "仍有离线材料缺失";
    log(`修复完成，复制 ${repaired.copied.length} 个文件。`);
  } catch (error) {
    state.statusText = `修复失败：${formatSidecarError(error)}`;
    log(state.statusText);
  } finally {
    state.busy = false;
    render();
  }
}

async function loadCalendarSettings() {
  try {
    const response = await callSidecar<{ settings: CalendarSettings }>("settings.calendar.get");
    state.calendarSettings = response.settings;
    render();
  } catch (error) {
    log(`学期设置读取失败：${formatSidecarError(error)}`);
  }
}

async function saveCalendarSettings() {
  state.busy = true;
  state.statusText = "正在保存学期设置";
  render();
  try {
    const response = await callSidecar<{ settings: CalendarSettings }>("settings.calendar.save", {
      settings: state.calendarSettings
    });
    state.calendarSettings = response.settings;
    state.statusText = `学期设置已保存：第 1 周周一 ${state.calendarSettings.semester_start_date}，${state.calendarSettings.teaching_weeks} 周。`;
    log(state.statusText);
  } catch (error) {
    state.statusText = `设置保存失败：${formatSidecarError(error)}`;
    log(state.statusText);
  } finally {
    state.busy = false;
    render();
  }
}

async function chooseFiles() {
  const selected = await open({
    multiple: true,
    filters: [{ name: "PDF", extensions: ["pdf"] }]
  });
  if (Array.isArray(selected)) {
    state.files = Array.from(new Set([...state.files, ...selected]));
  } else if (typeof selected === "string") {
    state.files = Array.from(new Set([...state.files, selected]));
  }
  state.statusText = state.files.length ? `已选择 ${state.files.length} 个 PDF` : state.statusText;
  render();
}

async function parseSchedules() {
  if (!state.files.length) {
    state.statusText = "请先选择 PDF。";
    render();
    return;
  }
  const invalidFiles = state.files.filter((file) => !inferFileKind(file));
  if (invalidFiles.length) {
    state.statusText = `有 ${invalidFiles.length} 个文件无法识别中方/英方，请先按“姓名-中方课表.pdf”或“姓名-英方课表.pdf”改名。`;
    render();
    return;
  }
  state.busy = true;
  state.statusText = "正在解析课表 PDF";
  render();
  try {
    const savedSettings = await callSidecar<{ settings: CalendarSettings }>("settings.calendar.save", {
      settings: state.calendarSettings
    });
    state.calendarSettings = savedSettings.settings;
    state.result = await callSidecar<ParseResult>("schedules.parse", { paths: state.files });
    if (state.result.detected_max_week > 0) {
      state.calendarSettings.teaching_weeks = state.result.detected_max_week;
    }
    state.activeTab = "availability";
    state.statusText = `处理完成：${state.result.summary.member_count || 0} 名成员，识别到 ${formatDetectedWeeks(state.result.detected_weeks)}，可按需调整导出周数。`;
    log(state.statusText);
  } catch (error) {
    state.statusText = `解析失败：${formatSidecarError(error)}`;
    log(state.statusText);
  } finally {
    state.busy = false;
    render();
  }
}

async function exportExcel() {
  if (!state.result?.result_ref) {
    state.statusText = "请先生成空课表。";
    render();
    return;
  }
  const target = await save({
    defaultPath: "Konggu_availability.xlsx",
    filters: [{ name: "Excel", extensions: ["xlsx"] }]
  });
  if (!target) {
    return;
  }
  state.busy = true;
  state.statusText = "正在导出 Excel";
  render();
  try {
    const savedSettings = await callSidecar<{ settings: CalendarSettings }>("settings.calendar.save", {
      settings: state.calendarSettings
    });
    state.calendarSettings = savedSettings.settings;
    const exported = await callSidecar<{ path: string }>("exports.excel", {
      result_ref: state.result.result_ref,
      target_path: target,
      mode: "classic",
      export_week_count: state.calendarSettings.teaching_weeks
    });
    state.statusText = `Excel 已导出：${exported.path}`;
    log(state.statusText);
  } catch (error) {
    state.statusText = `导出失败：${formatSidecarError(error)}`;
    log(state.statusText);
  } finally {
    state.busy = false;
    render();
  }
}

function removeFile(path: string) {
  state.files = state.files.filter((item) => item !== path);
  render();
}

function clearFiles() {
  state.files = [];
  state.statusText = "已清空待处理 PDF";
  render();
}

function clearLogs() {
  state.logs = [];
  render();
}

function render() {
  const summary = state.result?.summary || {};
  const fileViews = buildFileViews(state.files);
  const invalidFiles = fileViews.filter((item) => !item.kind);
  const visibleLogs = state.logs.slice(0, 4);
  const canParse = state.files.length > 0 && invalidFiles.length === 0 && !state.busy;
  app.innerHTML = `
    <main class="shell">
      <aside class="sidebar">
        <div class="brand">
          <img src="${wordmarkUrl}" alt="Konggu" />
          <span>离线桌面版</span>
        </div>
        <button id="chooseFiles" class="primary" ${state.busy ? "disabled" : ""}>选择课表 PDF</button>
        <button id="parseSchedules" ${canParse ? "" : "disabled"}>开始解析</button>
        <button id="exportExcel" ${!state.result || state.busy ? "disabled" : ""}>导出 Excel</button>
        <button id="repairResources" ${state.busy ? "disabled" : ""}>修复离线材料</button>
        <button id="refreshResources" class="ghost" ${state.busy ? "disabled" : ""}>重新检查材料</button>
        <section class="settingsBox">
          <h2>学期设置</h2>
          <label>
            <span>第 1 周周一</span>
            <input id="semesterStartDate" type="date" value="${escapeHtml(state.calendarSettings.semester_start_date)}" ${state.busy ? "disabled" : ""} />
          </label>
          <label>
            <span>导出周数</span>
            <input id="teachingWeeks" type="number" min="1" max="30" step="1" value="${state.calendarSettings.teaching_weeks}" ${state.busy ? "disabled" : ""} />
          </label>
          ${state.result?.detected_max_week ? `<small>已识别：${formatDetectedWeeks(state.result.detected_weeks)}，已自动填到第 ${state.result.detected_max_week} 周。</small>` : `<small>解析后会自动填入识别到的最大周数。</small>`}
          <button id="saveCalendarSettings" ${state.busy ? "disabled" : ""}>保存设置</button>
        </section>
        <section class="resourceBox">
          <h2>离线材料</h2>
          <p class="${state.resourceStatus?.ready ? "ok" : "warn"}">${state.resourceStatus?.ready ? "已就绪" : "需检查"}</p>
          <small>${state.resourceStatus?.ocr.ready ? "OCR 模型可用" : "OCR 模型缺失或未校验"}</small>
          ${renderResourceIssues()}
        </section>
      </aside>
      <section class="workspace">
        <header class="topbar">
          <div>
            <h1>空课生成工作台</h1>
            <p>${state.statusText}</p>
          </div>
          <div class="statusChip ${state.busy ? "busy" : ""}">
            <span></span>
            ${state.busy ? "处理中" : deriveReadyText(fileViews)}
          </div>
          <div class="stats">
            <div><strong>${summary.pdf_count || state.files.length}</strong><span>PDF</span></div>
            <div><strong>${summary.member_count || 0}</strong><span>成员</span></div>
            <div><strong>${summary.warning_count || 0}</strong><span>警告</span></div>
          </div>
        </header>
        ${renderWorkflow(fileViews)}
        <section class="filePanel">
          <div class="panelHeader">
            <div>
              <h2>已选文件</h2>
              <p>${renderFileSummary(fileViews)}</p>
            </div>
            <button id="clearFiles" ${!state.files.length || state.busy ? "disabled" : ""}>清空</button>
          </div>
          <div class="fileList">
            ${fileViews.length ? fileViews.map((item) => `
              <div class="fileRow ${item.kind ? "" : "invalid"}">
                <div class="fileInfo">
                  <span>${escapeHtml(item.file)}</span>
                  <small>${renderFileMeta(item)}</small>
                </div>
                <button data-remove="${escapeHtml(item.file)}" ${state.busy ? "disabled" : ""}>移除</button>
              </div>
            `).join("") : `<div class="empty">选择中方/英方课表 PDF 后开始解析。</div>`}
          </div>
          ${renderFileGuidance(fileViews)}
        </section>
        <section class="resultPanel">
          <div class="tabs">
            ${tabButton("availability", `空课预览${state.result ? ` · ${state.result.availability_preview.length}` : ""}`)}
            ${tabButton("members", `成员检查${state.result ? ` · ${state.result.members.length}` : ""}`)}
            ${tabButton("details", `识别明细${state.result ? ` · ${state.result.details.length}` : ""}`)}
          </div>
          ${renderActiveTable()}
        </section>
        <section class="logPanel">
          <div class="panelHeader compact">
            <h2>处理日志${state.logs.length ? ` · ${state.logs.length}` : ""}</h2>
            <button id="clearLogs" ${!state.logs.length ? "disabled" : ""}>清空</button>
          </div>
          ${visibleLogs.length ? visibleLogs.map((item) => `<p>${escapeHtml(item)}</p>`).join("") : `<p>等待操作。</p>`}
          ${state.logs.length > visibleLogs.length ? `<small class="logMeta">仅显示最近 ${visibleLogs.length} 条，完整日志已保留在本次运行记录中。</small>` : ""}
        </section>
      </section>
    </main>
  `;

  document.querySelector("#chooseFiles")?.addEventListener("click", chooseFiles);
  document.querySelector("#parseSchedules")?.addEventListener("click", parseSchedules);
  document.querySelector("#exportExcel")?.addEventListener("click", exportExcel);
  document.querySelector("#repairResources")?.addEventListener("click", repairResources);
  document.querySelector("#refreshResources")?.addEventListener("click", refreshResources);
  document.querySelector("#saveCalendarSettings")?.addEventListener("click", saveCalendarSettings);
  document.querySelector("#clearFiles")?.addEventListener("click", clearFiles);
  document.querySelector("#clearLogs")?.addEventListener("click", clearLogs);
  document.querySelector<HTMLInputElement>("#semesterStartDate")?.addEventListener("input", (event) => {
    state.calendarSettings.semester_start_date = (event.target as HTMLInputElement).value;
  });
  document.querySelector<HTMLInputElement>("#teachingWeeks")?.addEventListener("input", (event) => {
    state.calendarSettings.teaching_weeks = Number((event.target as HTMLInputElement).value || 0);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-remove]").forEach((button) => {
    button.addEventListener("click", () => removeFile(button.dataset.remove || ""));
  });
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab as AppState["activeTab"];
      render();
    });
  });
}

function tabButton(tab: AppState["activeTab"], label: string) {
  return `<button data-tab="${tab}" class="${state.activeTab === tab ? "active" : ""}">${label}</button>`;
}

function buildFileViews(files: string[]): FileView[] {
  const views = files.map((file) => ({
    file,
    kind: inferFileKind(file),
    member: inferMemberName(file),
    missingPair: ""
  }));
  const byMember = new Map<string, Set<string>>();
  views.forEach((view) => {
    if (!view.member || !view.kind) {
      return;
    }
    byMember.set(view.member, byMember.get(view.member) || new Set());
    byMember.get(view.member)?.add(view.kind);
  });
  return views.map((view) => {
    const kinds = view.member ? byMember.get(view.member) : undefined;
    if (view.kind === "中方" && !kinds?.has("英方")) {
      return { ...view, missingPair: "缺英方" };
    }
    if (view.kind === "英方" && !kinds?.has("中方")) {
      return { ...view, missingPair: "缺中方" };
    }
    return view;
  });
}

function deriveReadyText(fileViews: FileView[]) {
  if (state.result) {
    return "可导出";
  }
  if (!fileViews.length) {
    return "待选择";
  }
  if (fileViews.some((item) => !item.kind)) {
    return "需改名";
  }
  return "可解析";
}

function renderWorkflow(fileViews: FileView[]) {
  const hasFiles = fileViews.length > 0;
  const hasInvalid = fileViews.some((item) => !item.kind);
  const steps = [
    { label: "离线材料", done: Boolean(state.resourceStatus?.ready), active: state.busy && state.statusText.includes("材料") },
    { label: "选择 PDF", done: hasFiles, active: !hasFiles },
    { label: "文件校验", done: hasFiles && !hasInvalid, active: hasFiles && hasInvalid },
    { label: "生成空课", done: Boolean(state.result), active: state.busy && state.statusText.includes("解析") },
    { label: "导出 Excel", done: false, active: Boolean(state.result) && !state.busy }
  ];
  return `
    <section class="workflow" aria-label="处理进度">
      ${steps.map((step, index) => `
        <div class="step ${step.done ? "done" : ""} ${step.active ? "active" : ""}">
          <span>${step.done ? "✓" : index + 1}</span>
          <strong>${step.label}</strong>
        </div>
      `).join("")}
    </section>
  `;
}

function renderResourceIssues() {
  if (!state.resourceStatus || state.resourceStatus.ready) {
    return "";
  }
  const issues = [...state.resourceStatus.missing, ...state.resourceStatus.invalid].slice(0, 3);
  if (!issues.length) {
    return "";
  }
  return `<ul class="resourceIssues">${issues.map((item) => `<li>${escapeHtml(item.target)}：${escapeHtml(item.issue)}</li>`).join("")}</ul>`;
}

function renderFileSummary(fileViews: FileView[]) {
  if (!fileViews.length) {
    return "等待导入中方和英方课表。";
  }
  const members = new Set(fileViews.map((item) => item.member).filter(Boolean));
  const invalidCount = fileViews.filter((item) => !item.kind).length;
  const missingPairCount = fileViews.filter((item) => item.missingPair).length;
  if (invalidCount) {
    return `${fileViews.length} 个 PDF，${invalidCount} 个需要改名。`;
  }
  if (missingPairCount) {
    return `${fileViews.length} 个 PDF，${members.size || 0} 名成员，${missingPairCount} 个缺少配对课表。`;
  }
  return `${fileViews.length} 个 PDF，${members.size || 0} 名成员，文件类型已就绪。`;
}

function renderFileMeta(item: FileView) {
  if (!item.kind) {
    return "需要改名：文件名包含“中方”或“英方”";
  }
  const parts = [`已识别：${escapeHtml(item.kind)}`];
  if (item.member) {
    parts.push(escapeHtml(item.member));
  }
  if (item.missingPair) {
    parts.push(`<b>${escapeHtml(item.missingPair)}</b>`);
  }
  return parts.join(" · ");
}

function renderFileGuidance(fileViews: FileView[]) {
  const invalidCount = fileViews.filter((item) => !item.kind).length;
  if (invalidCount) {
    return `<p class="fileWarning">有 ${invalidCount} 个文件无法判断课表类型，已暂停解析。</p>`;
  }
  const missingPairCount = fileViews.filter((item) => item.missingPair).length;
  if (missingPairCount) {
    return `<p class="fileHint">已允许继续解析，但建议补齐缺少的中方/英方课表，结果更可信。</p>`;
  }
  if (fileViews.length) {
    return `<p class="fileHint">文件校验通过，可以开始解析。</p>`;
  }
  return "";
}

function formatDetectedWeeks(weeks: number[]) {
  if (!weeks.length) {
    return "无";
  }
  if (weeks.length <= 8) {
    return weeks.map((week) => `第${week}周`).join("、");
  }
  return `第${weeks[0]}周-第${weeks[weeks.length - 1]}周，共 ${weeks.length} 周`;
}

function inferFileKind(path: string) {
  const normalized = path.toLowerCase();
  if (path.includes("英方") || normalized.includes("english") || normalized.includes("uk")) {
    return "英方";
  }
  if (path.includes("中方") || normalized.includes("chinese")) {
    return "中方";
  }
  return "";
}

function inferMemberName(path: string) {
  const fileName = path.split(/[\\/]/).pop() || path;
  const stem = fileName.replace(/\.[^.]+$/, "");
  const cleaned = stem
    .replace(/中方课表|英方课表|中方|英方|课表|办公室|外联部|宣传部|活动部|部长|副部长|干事|负责人|成员/g, " ")
    .replace(/[-_\s]+/g, " ");
  const match = cleaned.match(/[\u4e00-\u9fff]{2,4}/);
  return match?.[0] || "";
}

function renderActiveTable() {
  const result = state.result;
  if (!result) {
    return `<div class="empty large">解析完成后会显示空课预览、成员检查和识别明细。</div>`;
  }
  if (state.activeTab === "members") {
    return table(result.members, ["member", "chinese_schedule", "english_schedule", "course_block_count", "status", "risk"]);
  }
  if (state.activeTab === "details") {
    return table(result.details, ["filename", "member", "source_type", "course_block_count", "status", "warning"]);
  }
  return table(result.availability_preview.slice(0, 300), ["周次", "日期", "星期", "节次", "时间", "空闲人数", "空闲人员"]);
}

function table(rows: Array<Record<string, unknown>>, columns: string[]) {
  if (!rows.length) {
    return `<div class="empty large">暂无数据。</div>`;
  }
  return `
    <div class="tableWrap">
      <table>
        <thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(String(row[column] ?? ""))}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatSidecarError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  if (
    message.includes("__TAURI") ||
    message.includes("invoke") ||
    message.includes("Cannot read properties of undefined")
  ) {
    return "桌面运行环境未连接，请在 Konggu 桌面端中使用该功能。";
  }
  return message.replace(/^Error:\s*/, "");
}

render();
loadCalendarSettings();
refreshResources();
