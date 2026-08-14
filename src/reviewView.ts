import { readFile } from "@tauri-apps/plugin-fs";
import * as pdfjs from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { CourseRow, ParseIssue, PdfInspection, ReviewPayload } from "./types";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type ReviewActions = {
  apply: (blockId: string, field: string, newValue: unknown, reason: string) => Promise<void>;
  confirm: (issueId: string) => Promise<void>;
};

export type ReviewViewState = {
  sourcePath: string;
  blockId: string;
  page: number;
  scale: number;
  scrollLeft: number;
  scrollTop: number;
};

export type ReviewController = {
  update: (review: ReviewPayload) => Promise<void>;
  destroy: () => void;
  getViewState: () => ReviewViewState;
};

export async function mountReview(
  root: HTMLElement,
  initialReview: ReviewPayload,
  actions: ReviewActions
): Promise<ReviewController> {
  let review = initialReview;
  let activeSource = review.sources[0];
  const blocksForActiveSource = () => review.blocks.filter((block) =>
    !activeSource || block.source_file === activeSource.file_name || block.source_file === activeSource.source_path
  );
  let activeBlock: CourseRow | null = blocksForActiveSource()[0] || review.blocks[0] || null;
  let currentPage = Math.max(1, Number(activeBlock?.page ?? 0) + 1);
  let scale = 1.15;
  let pdf: Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]> | null = null;
  let loadedPath = "";
  let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
  let renderVersion = 0;
  let destroyed = false;
  let sourceOptionsKey = "";
  let blockOptionsKey = "";
  let fieldsKey = "";
  let issuesKey = "";
  let viewportMetrics = { width: 0, height: 0, rotation: 0 };

  root.innerHTML = `
    <div class="reviewSplit">
      <section class="pdfDesk">
        <div class="reviewToolbar">
          <div class="reviewFileControl">
            <span class="reviewControlLabel">当前文件</span>
            <select id="reviewSource" aria-label="选择课表原文件"></select>
            <small id="reviewSourceMeta" class="reviewSourceMeta"></small>
          </div>
          <div class="reviewPageControls" role="group" aria-label="翻页和缩放课表原文">
            <button id="prevPage" aria-label="上一页">上一页</button>
            <span id="pageLabel" aria-live="polite">第 ${currentPage} 页</span>
            <button id="nextPage" aria-label="下一页">下一页</button>
            <div class="zoomControls" role="group" aria-label="缩放课表原文">
              <button id="zoomOut" aria-label="缩小">−</button>
              <button id="zoomIn" aria-label="放大">＋</button>
            </div>
          </div>
          <span class="pdfScrollHint">滚轮浏览 · Ctrl+滚轮缩放</span>
        </div>
        <div class="pdfViewport" id="pdfViewport">
          <div class="pdfStage" id="pdfStage">
            <canvas id="pdfCanvas"></canvas>
            <div class="pdfOverlay" id="pdfOverlay"></div>
          </div>
        </div>
      </section>
      <aside class="reviewInspector">
        <div id="reviewQuality" class="qualityBanner">
          <span id="reviewQualityTitle"></span>
          <small id="reviewQualityCount"></small>
        </div>
        <section class="pdfInspection">
          <h3>文件识别情况</h3>
          <div id="inspectionSummary"></div>
        </section>
        <section class="issueStack">
          <h3>待处理内容</h3>
          <div id="reviewIssues"></div>
        </section>
        <section class="blockEditor">
          <h3>修正课程时间</h3>
          <label>课程记录
            <select id="blockSelect"></select>
          </label>
          <div id="blockFields"></div>
        </section>
      </aside>
    </div>
  `;

  const sourceSelect = element<HTMLSelectElement>("#reviewSource");
  const blockSelect = element<HTMLSelectElement>("#blockSelect");

  function element<T extends HTMLElement>(selector: string) {
    const target = root.querySelector<T>(selector);
    if (!target) throw new Error(`Missing review element: ${selector}`);
    return target;
  }

  function renderQuality() {
    const banner = element<HTMLElement>("#reviewQuality");
    banner.className = `qualityBanner ${review.quality_state}`;
    element<HTMLElement>("#reviewQualityTitle").textContent = qualityLabel(review.quality_state);
    element<HTMLElement>("#reviewQualityCount").textContent = `${review.issues.filter((issue) => !issue.confirmed).length} 项待处理`;
  }

  function renderSources() {
    const currentPath = activeSource?.source_path || "";
    activeSource = review.sources.find((source) => source.source_path === currentPath) || review.sources[0];
    const nextKey = review.sources.map((source) => `${source.source_path}|${source.file_name}|${source.kind}`).join("\u0000");
    if (sourceOptionsKey !== nextKey) {
      sourceOptionsKey = nextKey;
      sourceSelect.innerHTML = review.sources
        .map((source) => `<option value="${escapeHtml(source.source_path)}">${escapeHtml(source.file_name)} · ${escapeHtml(source.kind)}课表</option>`)
        .join("");
    }
    if (activeSource) {
      sourceSelect.value = activeSource.source_path;
      const index = review.sources.findIndex((source) => source.source_path === activeSource?.source_path);
      element<HTMLElement>("#reviewSourceMeta").textContent = activeSource.source_path
        ? `文件 ${index + 1}/${review.sources.length} · ${activeSource.kind}课表`
        : `文件 ${index + 1}/${review.sources.length} · 原文需重新导入`;
    }
  }

  function renderIssues() {
    const nextKey = JSON.stringify(review.issues.map((issue) => [issue.issue_id, issue.confirmed, issue.message, issue.suggestion]));
    if (issuesKey === nextKey) return;
    issuesKey = nextKey;
    element<HTMLElement>("#reviewIssues").innerHTML = review.issues.length
      ? review.issues.map((issue) => issueCard(issue)).join("")
      : `<p class="emptyNote">没有待复核问题。</p>`;
  }

  function renderBlockOptions() {
    const activeId = activeBlock?.block_id || "";
    const blocks = blocksForActiveSource();
    activeBlock = blocks.find((block) => block.block_id === activeId) || blocks[0] || null;
    const nextKey = blocks.map((block) => `${block.block_id}|${blockLabel(block)}`).join("\u0000");
    if (blockOptionsKey !== nextKey) {
      blockOptionsKey = nextKey;
      blockSelect.innerHTML = blocks
        .map((block) => `<option value="${escapeHtml(block.block_id)}">${escapeHtml(blockLabel(block))}</option>`)
        .join("");
    }
    if (activeBlock) blockSelect.value = activeBlock.block_id;
  }

  function renderInspection() {
    const target = element<HTMLElement>("#inspectionSummary");
    if (!activeSource) {
      target.innerHTML = `<p class="emptyNote">没有可用的文件识别记录。</p>`;
      return;
    }
    const inspection = review.inspections.find((item) =>
      item.source_hash === activeSource.content_hash || item.source_file === activeSource.file_name
    );
    const html = inspectionCard(inspection);
    if (target.dataset.renderedHtml !== html) {
      target.dataset.renderedHtml = html;
      target.innerHTML = html;
    }
  }

  function renderFields(force = false) {
    const fields = element<HTMLElement>("#blockFields");
    if (!activeBlock) {
      fieldsKey = "empty";
      fields.innerHTML = `<p class="emptyNote">没有可编辑的课程记录。</p>`;
      return;
    }
    const nextKey = JSON.stringify([
      activeBlock.block_id,
      activeBlock.member_key,
      activeBlock.course,
      activeBlock.week,
      activeBlock.weekday,
      activeBlock.periods
    ]);
    if (!force && fieldsKey === nextKey) return;
    fieldsKey = nextKey;
    fields.innerHTML = `
      <div class="identityLine">${escapeHtml(activeBlock.member_key || activeBlock.name)}</div>
      <label>课程<input id="editCourse" value="${escapeHtml(activeBlock.course || "")}" /></label>
      <div class="fieldPair">
        <label>周次<input id="editWeek" type="number" min="1" max="30" value="${activeBlock.week}" /></label>
        <label>星期<select id="editWeekday">${["周一","周二","周三","周四","周五","周六","周日"].map((day) => `<option ${day === activeBlock?.weekday ? "selected" : ""}>${day}</option>`).join("")}</select></label>
      </div>
      <label>节次<input id="editPeriods" value="${(activeBlock.periods || []).join(",")}" placeholder="例如 1,2" /></label>
      <label>修正原因<textarea id="editReason" rows="2" placeholder="说明为何需要修正"></textarea></label>
      <button id="applyCorrection" class="primary">保存修正</button>
    `;
  }

  async function loadSource(path: string, resetPosition = false) {
    if (destroyed) return;
    if (!path) {
      await destroyPdf();
      loadedPath = "";
      return;
    }
    if (loadedPath !== path) {
      renderTask?.cancel();
      await destroyPdf();
      const bytes = await readFile(path);
      if (destroyed) return;
      pdf = await pdfjs.getDocument({ data: bytes }).promise;
      loadedPath = path;
    }
    if (resetPosition) {
      const viewport = element<HTMLElement>("#pdfViewport");
      viewport.scrollLeft = 0;
      viewport.scrollTop = 0;
    }
    currentPage = Math.min(currentPage, pdf?.numPages || currentPage);
    renderInspection();
    await renderPage();
  }

  async function renderPage() {
    if (!pdf || destroyed) return;
    const version = ++renderVersion;
    const viewportElement = element<HTMLElement>("#pdfViewport");
    const scrollLeft = viewportElement.scrollLeft;
    const scrollTop = viewportElement.scrollTop;
    renderTask?.cancel();
    const page = await pdf.getPage(currentPage);
    if (destroyed || version !== renderVersion) return;
    const viewport = page.getViewport({ scale });
    viewportMetrics = { width: viewport.width, height: viewport.height, rotation: viewport.rotation };
    const pixelRatio = window.devicePixelRatio || 1;
    const canvas = element<HTMLCanvasElement>("#pdfCanvas");
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法创建课表预览画布。");
    canvas.width = Math.floor(viewport.width * pixelRatio);
    canvas.height = Math.floor(viewport.height * pixelRatio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const task = page.render({
      canvasContext: context,
      canvas,
      viewport,
      transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0]
    });
    renderTask = task;
    try {
      await task.promise;
    } catch (error) {
      if ((error as { name?: string }).name !== "RenderingCancelledException") throw error;
      return;
    }
    if (destroyed || version !== renderVersion) return;
    const stage = element<HTMLElement>("#pdfStage");
    stage.style.width = `${viewport.width}px`;
    stage.style.height = `${viewport.height}px`;
    element<HTMLElement>("#pageLabel").textContent = `第 ${currentPage} / ${pdf.numPages} 页`;
    renderOverlay();
    viewportElement.scrollLeft = scrollLeft;
    viewportElement.scrollTop = scrollTop;
  }

  function handlePdfWheel(event: WheelEvent) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const nextScale = Math.max(0.6, Math.min(2.5, scale + (event.deltaY < 0 ? 0.1 : -0.1)));
    if (nextScale === scale) return;
    scale = Number(nextScale.toFixed(2));
    void renderPage();
  }

  function renderOverlay() {
    const overlay = element<HTMLElement>("#pdfOverlay");
    const { width, height, rotation } = viewportMetrics;
    if (!width || !height) return;
    const blocks = blocksForActiveSource().filter((block) => Number(block.page ?? 0) + 1 === currentPage && block.bbox);
    overlay.innerHTML = blocks.map((block) => {
      const rect = transformRect(block, width, height, rotation);
      if (!rect) return "";
      const selected = activeBlock?.block_id === block.block_id ? "selected" : "";
      return `<button class="pdfBox ${selected}" data-block-id="${escapeHtml(block.block_id)}"
        style="left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px"
        title="${escapeHtml(blockLabel(block))}"><span>${escapeHtml(block.course || "课程")}</span></button>`;
    }).join("");
  }

  function selectBlock(blockId: string) {
    activeBlock = blocksForActiveSource().find((block) => block.block_id === blockId) || null;
    if (!activeBlock) return;
    blockSelect.value = activeBlock.block_id;
    currentPage = Number(activeBlock.page ?? 0) + 1;
    renderFields(true);
    void renderPage();
  }

  function valueOf(selector: string) {
    return (root.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector)?.value || "").trim();
  }

  root.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    const block = target.closest<HTMLButtonElement>("[data-block-id]");
    if (block) {
      selectBlock(block.dataset.blockId || "");
      return;
    }
    const issue = target.closest<HTMLButtonElement>("[data-confirm-issue]");
    if (issue) {
      void actions.confirm(issue.dataset.confirmIssue || "");
      return;
    }
    if (target.closest("#prevPage") && currentPage > 1) {
      currentPage -= 1;
      void renderPage();
      return;
    }
    if (target.closest("#nextPage") && pdf && currentPage < pdf.numPages) {
      currentPage += 1;
      void renderPage();
      return;
    }
    if (target.closest("#zoomOut")) {
      scale = Math.max(0.6, scale - 0.15);
      void renderPage();
      return;
    }
    if (target.closest("#zoomIn")) {
      scale = Math.min(2.5, scale + 0.15);
      void renderPage();
      return;
    }
    if (target.closest("#applyCorrection")) void applyCorrection();
  });

  sourceSelect.addEventListener("change", () => {
    activeSource = review.sources.find((source) => source.source_path === sourceSelect.value) || review.sources[0];
    activeBlock = blocksForActiveSource()[0] || null;
    currentPage = Math.max(1, Number(activeBlock?.page ?? 0) + 1);
    blockOptionsKey = "";
    fieldsKey = "";
    renderBlockOptions();
    renderFields();
    void loadSource(sourceSelect.value, true);
  });
  const pdfViewport = element<HTMLElement>("#pdfViewport");
  pdfViewport.addEventListener("wheel", handlePdfWheel, { passive: false });
  blockSelect.addEventListener("change", () => selectBlock(blockSelect.value));

  async function applyCorrection() {
    if (!activeBlock) return;
    const reason = valueOf("#editReason");
    const updates: Array<[string, unknown]> = [
      ["course", valueOf("#editCourse")],
      ["week", Number(valueOf("#editWeek"))],
      ["weekday", valueOf("#editWeekday")],
      ["periods", valueOf("#editPeriods").split(/[,，\s]+/).filter(Boolean).map(Number)]
    ];
    const original: Record<string, unknown> = {
      course: activeBlock.course || "",
      week: activeBlock.week,
      weekday: activeBlock.weekday,
      periods: activeBlock.periods || []
    };
    for (const [field, value] of updates) {
      if (JSON.stringify(original[field]) !== JSON.stringify(value)) {
        await actions.apply(activeBlock.block_id, field, value, reason);
      }
    }
  }

  async function update(nextReview: ReviewPayload) {
    if (destroyed) return;
    const previousPath = activeSource?.source_path || "";
    review = nextReview;
    renderQuality();
    renderSources();
    renderIssues();
    renderBlockOptions();
    renderFields();
    renderInspection();
    renderOverlay();
    if (activeSource && previousPath !== activeSource.source_path) {
      await loadSource(activeSource.source_path, true);
    }
  }

  function getViewState(): ReviewViewState {
    const viewport = element<HTMLElement>("#pdfViewport");
    return {
      sourcePath: activeSource?.source_path || "",
      blockId: activeBlock?.block_id || "",
      page: currentPage,
      scale,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop
    };
  }

  function destroy() {
    destroyed = true;
    renderVersion += 1;
    renderTask?.cancel();
    pdfViewport.removeEventListener("wheel", handlePdfWheel);
    void destroyPdf();
  }

  async function destroyPdf() {
    const current = pdf;
    pdf = null;
    if (!current) return;
    const destroyDocument = (current as unknown as { destroy?: () => Promise<void> | void }).destroy;
    if (destroyDocument) await destroyDocument.call(current);
    else current.cleanup();
  }

  renderQuality();
  renderSources();
  renderIssues();
  renderBlockOptions();
  renderFields();
  renderInspection();
  if (activeSource) await loadSource(activeSource.source_path);

  return { update, destroy, getViewState };
}

function inspectionCard(inspection?: PdfInspection) {
  if (!inspection) return `<p class="emptyNote">没有可用的文件识别记录。</p>`;
  if (inspection.status !== "ready") {
    return `<article class="inspectionCard unavailable"><b>文件检查未完成</b><p>${escapeHtml(inspection.error || "暂时无法读取文件信息")}</p></article>`;
  }
  const ocrPages = inspection.pages_needing_ocr.length ? inspection.pages_needing_ocr.join("、") : "无";
  const reasons = inspection.ocr_reasons_by_page.map((item) =>
    `第 ${item.page} 页：${item.reasons.map(reasonLabel).join("、")}`
  );
  return `<article class="inspectionCard ${escapeHtml(inspection.pdf_type)}">
    <div class="inspectionGrid">
      <span>文件形式<b>${escapeHtml(pdfTypeLabel(inspection.pdf_type))}</b></span>
      <span>页数<b>${inspection.page_count}</b></span>
      <span>图片识别页<b>${escapeHtml(ocrPages)}</b></span>
      <span>文字读取<b>${inspection.has_encoding_issues ? "建议核对" : "正常"}</b></span>
    </div>
    ${reasons.length ? `<ul>${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : `<p>所有页面都可直接读取，无需图片文字识别。</p>`}
    ${inspection.warning ? `<p class="inspectionWarning">${escapeHtml(inspection.warning)}</p>` : ""}
  </article>`;
}

function pdfTypeLabel(value: string) {
  const labels: Record<string, string> = {
    text_based: "文字版 PDF",
    scanned: "扫描件",
    image_based: "图片版 PDF",
    mixed: "混合内容 PDF",
    unknown: "未确定"
  };
  return labels[value] || value;
}

function reasonLabel(value: string) {
  const labels: Record<string, string> = {
    scanned: "这一页是扫描件",
    no_text: "没有可直接读取的文字",
    vector_text: "文字以图形方式保存",
    suspected_garbled_text: "直接读取的文字可能乱码",
    sparse_text: "可直接读取的文字较少",
    full_page_image: "整页内容为图片"
  };
  return labels[value] || value;
}

function transformRect(block: CourseRow, width: number, height: number, rotation: number) {
  if (!block.bbox) return null;
  const sourceWidth = Number(block.page_width || width);
  const sourceHeight = Number(block.page_height || height);
  const [x0, y0, x1, y1] = block.bbox;
  const corners = [[x0 / sourceWidth, y0 / sourceHeight], [x1 / sourceWidth, y1 / sourceHeight]];
  const mapped = corners.map(([x, y]) => {
    if (rotation === 90) return [1 - y, x];
    if (rotation === 180) return [1 - x, 1 - y];
    if (rotation === 270) return [y, 1 - x];
    return [x, y];
  });
  const xs = mapped.map((point) => point[0] * width);
  const ys = mapped.map((point) => point[1] * height);
  return {
    left: Math.min(...xs),
    top: Math.min(...ys),
    width: Math.abs(xs[1] - xs[0]),
    height: Math.abs(ys[1] - ys[0])
  };
}

function issueCard(issue: ParseIssue) {
  const state = issue.confirmed ? "confirmed" : issue.severity;
  return `<article class="issueCard ${state}">
    <div><b>${issue.confirmed ? "已核对" : issue.severity === "error" ? "必须处理" : "请核对"}</b></div>
    <p>${escapeHtml(friendlyMessage(issue.message))}</p>
    ${issue.suggestion ? `<small>${escapeHtml(friendlyMessage(issue.suggestion))}</small>` : ""}
    ${issueAction(issue)}
  </article>`;
}

function issueAction(issue: ParseIssue) {
  if (issue.confirmed) return "";
  if (issue.severity === "error") return `<small class="issueActionHint">请先补充或修正课表，再重新生成空课表。</small>`;
  return `<button data-confirm-issue="${escapeHtml(issue.issue_id)}">确认已核对</button>`;
}

function blockLabel(block: CourseRow) {
  return `第${block.week}周 ${block.weekday} ${block.periods?.join(",") || "-"}节 · ${block.course || "未命名课程"}`;
}

function qualityLabel(state: string) {
  if (state === "accepted") return "已检查，可以导出";
  if (state === "needs_review") return "还有内容需要核对";
  return "发现影响结果的问题";
}

function friendlyMessage(message: string) {
  return message
    .replaceAll("课程占用槽", "课程时间")
    .replaceAll("时间块", "有课时段")
    .replaceAll("阻断错误", "必须处理的问题")
    .replaceAll("组合标识", "成员信息")
    .replaceAll("角色", "职务")
    .replaceAll("PDF 复核页", "“原文核对”")
    .replaceAll("文本层", "可读取文字")
    .replaceAll("OCR 框", "识别标记")
    .replaceAll("OCR", "图片文字识别");
}

function escapeHtml(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
