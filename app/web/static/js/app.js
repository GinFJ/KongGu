let currentTaskId = null;
let stagedPdfFiles = [];

const state = {
  files: [],
  status: "IDLE",
  progress: 0,
  pollTimer: null,
  exporting: false,
  hasWarnings: false,
  results: {
    availability: [],
    members: [],
    details: [],
    logs: [],
    warnings: [],
    errors: [],
  },
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const statusLabels = {
  IDLE: "空闲",
  UPLOADED: "已上传",
  RUNNING: "正在解析",
  COMPLETED: "已完成",
  FAILED: "失败",
  EXPORTED: "已导出",
};

const taskHints = {
  IDLE: "请上传课表 PDF。",
  UPLOADED: "已上传文件，请点击“生成空课表”。",
  RUNNING: "正在解析课表 PDF，请等待。",
  COMPLETED: "空课表生成完成，可以查看结果或导出 Excel。",
  FAILED: "任务失败，请查看处理日志。",
  EXPORTED: "Excel 已导出，可以继续检查结果或清空任务。",
};

function setStatus(status) {
  state.status = status || "IDLE";
  renderTaskStatus();
}

function renderTaskStatus() {
  const warningSuffix = state.hasWarnings && (state.status === "COMPLETED" || state.status === "EXPORTED")
    ? "，有警告"
    : "";
  const label = `${statusLabels[state.status] || state.status}${warningSuffix}`;
  const badge = $("#statusBadge");
  badge.textContent = label;
  badge.className = `badge badge-${state.status.toLowerCase()}${state.hasWarnings ? " badge-warning-state" : ""}`;
  $("#taskStatusText").textContent = label;
  $("#taskStatusText").className = `console-status badge-${state.status.toLowerCase()}${state.hasWarnings ? " badge-warning-state" : ""}`;
  $("#taskHint").textContent = state.hasWarnings && state.status === "COMPLETED"
    ? "结果已生成，但存在警告，请先检查成员检查和识别明细。"
    : taskHints[state.status] || "等待任务状态更新。";
  updateButtons();
  renderDebugInfo();
}

function updateButtons() {
  const running = state.status === "RUNNING";
  $("#pickFilesBtn").disabled = running;
  $("#pickFilesBtn").textContent = canAppendToCurrentTask() ? "继续追加 PDF" : "上传 PDF";
  $("#startBtn").disabled = !currentTaskId || running || state.files.length === 0;
  $("#exportBtn").disabled = state.exporting || !(state.status === "COMPLETED" || state.status === "EXPORTED");
  $("#clearBtn").disabled = running || (!currentTaskId && state.files.length === 0);
  $("#exportHint").textContent = $("#exportBtn").disabled
    ? "生成完成后可导出 Excel。"
    : "结果已生成，可以导出 Excel。";
}

function canAppendToCurrentTask() {
  return Boolean(currentTaskId && !["RUNNING", "COMPLETED", "EXPORTED"].includes(state.status));
}

function setProgress(value) {
  state.progress = Math.max(0, Math.min(1, Number(value) || 0));
  $("#progressFill").style.width = `${Math.round(state.progress * 100)}%`;
  $("#progressText").textContent = `${Math.round(state.progress * 100)}%`;
  renderDebugInfo();
}

function renderDebugInfo() {
  $("#debugInfo").textContent = `task_id: ${currentTaskId || "-"} | status: ${state.status} | progress: ${Math.round(state.progress * 100)}%`;
}

function renderSummary(summary = {}) {
  $("#acceptUploaded").textContent = summary.pdf_count ?? state.files.length;
  $("#acceptMembers").textContent = summary.member_count ?? 0;
  $("#acceptCompleteMembers").textContent = summary.complete_member_count ?? 0;
  $("#acceptWarnings").textContent = summary.warning_count ?? 0;
  state.hasWarnings = Number(summary.warning_count || 0) > 0 || state.results.warnings.length > 0 || state.results.errors.length > 0;
  renderTaskStatus();
}

function renderAcceptanceSummary(summary = {}) {
  $("#acceptUploaded").textContent = summary.uploaded_pdf_count ?? state.files.length;
  $("#acceptMembers").textContent = summary.member_count ?? 0;
  $("#acceptCompleteMembers").textContent = summary.complete_member_count ?? 0;
  $("#acceptMissingChinese").textContent = summary.missing_chinese_count ?? 0;
  $("#acceptMissingEnglish").textContent = summary.missing_english_count ?? 0;
  $("#acceptWarnings").textContent = summary.warning_count ?? 0;
}

function renderFiles(files) {
  state.files = files || [];
  $("#fileCount").textContent = `共 ${state.files.length} 个 PDF`;
  $("#fileEmpty").hidden = state.files.length > 0;
  const tbody = $("#fileTable tbody");
  tbody.innerHTML = "";
  for (const file of state.files) {
    const row = document.createElement("tr");
    const sourceType = file.source_type || "unknown";
    const typeLabel = file.source_type_label || sourceTypeLabel(sourceType);
    const member = file.member_name || file.inferred_member || "无法推断成员名";
    row.innerHTML = `
      <td title="${escapeHtml(file.filename || "")}">${escapeHtml(file.filename || "")}</td>
      <td>${badge(typeLabel, sourceTypeBadgeClass(sourceType))}</td>
      <td>${escapeHtml(member)}</td>
      <td>${formatBytes(file.size || 0)}</td>
      <td>${badge(file.status || "uploaded", "badge-info")}</td>
      <td class="${file.warning ? "cell-warning" : ""}">${escapeHtml(file.warning || file.note || "等待解析")}</td>
    `;
    tbody.appendChild(row);
  }
  updateButtons();
}

function renderResults() {
  renderAvailability();
  renderMembers();
  renderDetails();
  renderLogs(state.results.logs);
  $("#resultCount").textContent = state.results.availability.length
    ? `${state.results.availability.length} 条空课表记录`
    : "尚未生成";
}

function renderAvailability() {
  const warningBanner = state.hasWarnings
    ? `<div class="notice notice-warning result-warning">当前结果存在警告，建议检查后再使用导出的 Excel。</div>`
    : "";
  $("#availabilityPanel").innerHTML = warningBanner + renderTable(state.results.availability, [
    "teaching_week",
    "weekday",
    "period",
    "free_count",
    "free_members",
    "busy_count",
    "busy_members",
  ], {
    emptyTitle: "暂无空课表结果",
    emptyMessage: "请先上传课表并点击“生成空课表”。",
  });
}

function renderMembers() {
  $("#membersPanel").innerHTML = renderTable(state.results.members, [
    "member",
    "chinese_schedule",
    "english_schedule",
    "course_block_count",
    "status",
    "risk",
  ], {
    emptyTitle: "暂无成员检查结果",
    emptyMessage: "生成后将在这里显示每位成员的课表完整性。",
  });
}

function renderDetails() {
  $("#detailsPanel").innerHTML = renderTable(state.results.details, [
    "filename",
    "member",
    "source_type_label",
    "parser",
    "cache",
    "course_block_count",
    "status",
    "warning",
    "error",
  ], {
    emptyTitle: "暂无识别明细",
    emptyMessage: "生成后将在这里显示每个 PDF 的识别情况。",
  });
}

function renderLogs(logs) {
  if (!logs || logs.length === 0) {
    $("#logsPanel").innerHTML = emptyBlock("暂无处理日志", "程序运行信息将在这里显示。");
    return;
  }
  const lines = [...logs].map((log) => {
    const level = escapeHtml(log.level || "INFO");
    const time = escapeHtml(log.time || "");
    const message = escapeHtml(log.message || String(log));
    return `<div class="log-line log-${level.toLowerCase()}"><strong>${level}</strong><em>${message}</em><span>${time}</span></div>`;
  }).join("");
  $("#logsPanel").innerHTML = `<div class="log-box">${lines}</div>`;
}

function renderTable(rows, columns, options = {}) {
  if (!rows || rows.length === 0) {
    return emptyBlock(options.emptyTitle || "暂无内容", options.emptyMessage || "生成完成后将在这里显示。");
  }
  const head = columns.map((column) => `<th>${escapeHtml(labelFor(column))}</th>`).join("");
  const body = rows.map((row) => {
    const cells = columns.map((column) => {
      const raw = row?.[column];
      return `<td class="${cellClass(column, row)}" title="${escapeHtml(formatCell(raw))}">${renderCell(column, raw)}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
  return `<div class="table-wrap result-table"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderApiMessages(errors = [], warnings = []) {
  state.results.errors = errors || [];
  state.results.warnings = warnings || [];
  const visible = [
    ...state.results.errors.map((message) => ({ level: "ERROR", message })),
    ...state.results.warnings.map((message) => ({ level: "WARNING", message })),
  ];
  $("#messagePanel").innerHTML = visible.length
    ? visible.map((item) => `<div class="notice notice-${item.level.toLowerCase()}">${escapeHtml(item.message)}</div>`).join("")
    : "";
}

async function uploadFiles(fileList) {
  const incoming = Array.from(fileList || []);
  const pdfs = incoming.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  if (!incoming.length) {
    showToast("请选择 PDF 文件。", "warning");
    return;
  }
  if (pdfs.length !== incoming.length) {
    renderApiMessages(["仅支持 PDF 文件。"], []);
    showToast("仅支持 PDF 文件。", "error");
    return;
  }
  const continuingTask = canAppendToCurrentTask();
  const previousTaskId = currentTaskId;
  const previousStatus = state.status;
  const previousStagedPdfFiles = stagedPdfFiles;
  const previousStagedCount = continuingTask ? stagedPdfFiles.length : 0;
  stagedPdfFiles = continuingTask ? mergePdfFiles(stagedPdfFiles, pdfs) : [...pdfs];
  const addedCount = stagedPdfFiles.length - previousStagedCount;
  stopPolling();
  const body = new FormData();
  for (const file of stagedPdfFiles) body.append("files", file);
  setStatus("RUNNING");
  setProgress(0.05);
  showLog("正在上传 PDF 文件。");
  renderApiMessages();
  try {
    const data = await apiFetch("/api/files/upload", { method: "POST", body });
    const isNewTask = previousTaskId !== data.task_id;
    currentTaskId = data.task_id;
    if (previousTaskId && isNewTask) {
      clearRemoteTask(previousTaskId);
    }
    if (isNewTask) {
      state.results = { availability: [], members: [], details: [], logs: [], warnings: [], errors: [] };
      state.hasWarnings = false;
      renderResults();
    }
    $("#taskIdLabel").textContent = data.task_id;
    renderFiles(data.files);
    renderSummary({ pdf_count: data.files.length, warning_count: (data.warnings || []).length + (data.errors || []).length });
    renderAcceptanceSummary({ uploaded_pdf_count: data.files.length, warning_count: (data.warnings || []).length + (data.errors || []).length });
    $("#currentFile").textContent = data.files[0]?.filename || "-";
    $("#uploadHint").textContent = `已上传 ${data.files.length} 个 PDF，可继续分批追加。`;
    showLog(`本次新增 ${addedCount} 个 PDF，当前共 ${data.files.length} 个。`);
    renderApiMessages(data.errors, data.warnings);
    setStatus("UPLOADED");
    setProgress(0);
    $("#fileInput").value = "";
    showToast(continuingTask ? "追加上传成功。" : "上传成功。", "success");
  } catch (error) {
    stagedPdfFiles = previousStagedPdfFiles;
    if (continuingTask) {
      currentTaskId = previousTaskId;
      $("#taskIdLabel").textContent = previousTaskId || "尚无任务";
      setStatus(previousStatus || "UPLOADED");
    } else {
      setStatus("FAILED");
    }
    renderApiMessages([error.message], []);
    showLog(error.message);
    showToast("上传失败。", "error");
  }
}

function mergePdfFiles(existingFiles, newFiles) {
  const merged = [...existingFiles];
  const seen = new Set(existingFiles.map(fileKey));
  for (const file of newFiles) {
    const key = fileKey(file);
    if (!seen.has(key)) {
      merged.push(file);
      seen.add(key);
    }
  }
  return merged;
}

function fileKey(file) {
  return `${file.name}::${file.size}::${file.lastModified}`;
}

function clearRemoteTask(taskId) {
  apiFetch("/api/files/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  }).catch(() => {});
}

async function startProcess() {
  if (!currentTaskId || state.status === "RUNNING") return;
  try {
    await apiFetch("/api/process/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: currentTaskId }),
    });
    setStatus("RUNNING");
    setProgress(0.18);
    showLog("处理已开始。");
    renderApiMessages();
    showToast("生成已开始。", "success");
    startPolling();
  } catch (error) {
    showLog(error.message);
    renderApiMessages([error.message], []);
    setStatus("FAILED");
    showToast("生成失败。", "error");
  }
}

function startPolling() {
  stopPolling();
  const tick = async () => {
    if (!currentTaskId) {
      stopPolling();
      return;
    }
    try {
      const data = await apiFetch(`/api/process/status/${currentTaskId}`);
      state.hasWarnings = Boolean((data.warnings || []).length || (data.errors || []).length || Number(data.summary?.warning_count || 0));
      setStatus(data.status);
      setProgress(data.status === "FAILED" ? 0 : data.progress);
      renderSummary(data.summary);
      renderAcceptanceSummary(data.acceptance_summary || {});
      renderApiMessages(data.errors, data.warnings);
      $("#currentFile").textContent = data.current_stage || data.current_file || "-";
      const latest = (data.logs || []).at(-1);
      showLog(latest ? `${latest.level}: ${latest.message}` : "暂无日志");
      state.results.logs = data.logs || [];
      renderResults();
      if (data.status === "COMPLETED" || data.status === "EXPORTED" || data.status === "FAILED") {
        stopPolling();
        await loadResults();
        showToast(data.status === "FAILED" ? "任务失败，请查看日志。" : "生成完成。", data.status === "FAILED" ? "error" : "success");
      }
    } catch (error) {
      stopPolling();
      showLog(error.message);
      renderApiMessages([error.message], []);
      setStatus("FAILED");
      showToast("状态更新失败。", "error");
    }
  };
  tick();
  state.pollTimer = window.setInterval(tick, 1000);
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function loadResults() {
  if (!currentTaskId) return;
  const data = await apiFetch(`/api/results/${currentTaskId}`);
  state.results.availability = data.availability || [];
  state.results.members = data.members || [];
  state.results.details = data.details || [];
  state.results.logs = data.logs || [];
  state.results.warnings = data.warnings || [];
  state.results.errors = data.errors || [];
  state.hasWarnings = Boolean(state.results.warnings.length || state.results.errors.length || Number(data.summary?.warning_count || 0));
  renderSummary(data.summary);
  renderAcceptanceSummary(data.acceptance_summary || {});
  renderApiMessages(data.errors, data.warnings);
  renderResults();
}

async function exportExcel() {
  if (!currentTaskId || state.exporting) return;
  if (state.status !== "COMPLETED" && state.status !== "EXPORTED") {
    renderApiMessages(["请先生成空课表。"], []);
    showToast("请先生成空课表。", "warning");
    return;
  }
  if (state.hasWarnings) {
    renderApiMessages([], ["当前结果存在警告，建议检查后再使用。"]);
  }
  state.exporting = true;
  updateButtons();
  try {
    const data = await apiFetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: currentTaskId }),
    });
    setStatus("EXPORTED");
    showLog("Excel 已生成，正在下载。");
    window.location.href = data.download_url;
    await loadResults();
    showToast("导出成功。", "success");
  } catch (error) {
    showLog(error.message);
    renderApiMessages([error.message], []);
    showToast("导出失败。", "error");
  } finally {
    state.exporting = false;
    updateButtons();
  }
}

async function clearTask() {
  const taskId = currentTaskId;
  stopPolling();
  if (taskId) {
    try {
      await apiFetch("/api/files/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId }),
      });
    } catch (error) {
      showLog(error.message);
    }
  }
  currentTaskId = null;
  stagedPdfFiles = [];
  state.files = [];
  state.hasWarnings = false;
  state.results = { availability: [], members: [], details: [], logs: [], warnings: [], errors: [] };
  $("#taskIdLabel").textContent = "尚无任务";
  $("#currentFile").textContent = "-";
  $("#fileInput").value = "";
  $("#uploadHint").textContent = "等待选择课表文件。";
  renderFiles([]);
  renderSummary({});
  renderAcceptanceSummary({});
  renderApiMessages();
  renderResults();
  setProgress(0);
  setStatus("IDLE");
  showLog("暂无日志");
  showToast("任务已清空。", "success");
}

async function apiFetch(url, options) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" ? data.detail : data;
    throw new Error(detail || `请求失败：${response.status}`);
  }
  return data;
}

function showLog(message) {
  $("#latestLog").textContent = message || "暂无日志";
}

function showToast(message, type = "info") {
  const host = $("#toastHost");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  host.appendChild(toast);
  window.setTimeout(() => toast.remove(), type === "error" ? 5200 : 3000);
}

function badge(text, className) {
  return `<span class="badge-cell ${className}">${escapeHtml(text)}</span>`;
}

function labelFor(key) {
  return {
    teaching_week: "教学周",
    weekday: "星期",
    period: "节次",
    free_count: "空闲人数",
    free_members: "空闲成员",
    busy_count: "有课人数",
    busy_members: "有课成员",
    member: "成员",
    chinese_schedule: "中方课表",
    english_schedule: "英方课表",
    course_block_count: "课程块数",
    status: "状态",
    risk: "风险提示",
    filename: "文件名",
    source_type_label: "类型",
    parser: "解析器",
    cache: "缓存",
    warning: "警告",
    error: "错误",
  }[key] || key;
}

function renderCell(column, value) {
  if (column === "status") return badge(formatCell(value), statusBadgeClass(value));
  if (column === "chinese_schedule" || column === "english_schedule") return badge(formatCell(value), scheduleBadgeClass(value));
  if (column === "free_count") return `<strong class="free-count">${escapeHtml(formatCell(value))}</strong>`;
  if (column === "course_block_count" && Number(value) === 0) return `<strong class="zero-count">0</strong>`;
  if (column === "warning" && value) return `<span class="inline-warning">${escapeHtml(formatCell(value))}</span>`;
  if (column === "error" && value) return `<span class="inline-error">${escapeHtml(formatCell(value))}</span>`;
  return escapeHtml(formatCell(value));
}

function sourceTypeBadgeClass(value) {
  if (value === "english") return "badge-purple";
  if (value === "chinese") return "badge-info";
  return "badge-warn";
}

function sourceTypeLabel(value) {
  if (value === "english") return "英方";
  if (value === "chinese") return "中方";
  return "未知";
}

function statusBadgeClass(value) {
  const text = String(value || "");
  if (text === "正常") return "badge-ok";
  if (text === "需检查") return "badge-warn";
  if (text === "疑似解析失败" || text === "异常" || text.includes("失败")) return "badge-risk";
  return "badge-muted";
}

function scheduleBadgeClass(value) {
  const text = String(value || "");
  if (text.includes("已") || text.includes("导入")) return "badge-ok";
  if (text.includes("缺")) return "badge-warn";
  return "badge-muted";
}

function cellClass(column, row) {
  if (column === "warning" && row?.[column]) return "cell-warning";
  if (column === "error" && row?.[column]) return "cell-error";
  if (column === "course_block_count" && Number(row?.[column]) === 0) return "cell-error";
  return "";
}

function emptyBlock(title, message = "生成完成后将在这里显示。") {
  return `<div class="empty-state result-empty"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div>`;
}

function formatCell(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.join("、");
  return String(value);
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function bindEvents() {
  $("#pickFilesBtn").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", (event) => uploadFiles(event.target.files));
  $("#startBtn").addEventListener("click", startProcess);
  $("#exportBtn").addEventListener("click", exportExcel);
  $("#clearBtn").addEventListener("click", clearTask);

  const dropZone = $("#dropZone");
  const dropSurface = $(".drop-surface");
  dropSurface.addEventListener("click", () => {
    if (!$("#pickFilesBtn").disabled) $("#fileInput").click();
  });
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragging");
    });
  }
  dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));

  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".tab").forEach((item) => item.classList.toggle("active", item === button));
      $$(".tab-panel").forEach((panel) => panel.classList.remove("active"));
      $(`#${button.dataset.tab}Panel`).classList.add("active");
    });
  });
}

bindEvents();
renderFiles([]);
renderSummary({});
renderAcceptanceSummary({});
renderResults();
setProgress(0);
setStatus("IDLE");
