import { readFile } from "@tauri-apps/plugin-fs";
import * as pdfjs from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { CourseRow, ParseIssue, ReviewPayload } from "./types";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type ReviewActions = {
  apply: (blockId: string, field: string, newValue: unknown, reason: string) => Promise<void>;
  confirm: (issueId: string) => Promise<void>;
};

export async function mountReview(
  root: HTMLElement,
  review: ReviewPayload,
  actions: ReviewActions
) {
  const activeSource = review.sources[0];
  let activeBlock = review.blocks[0] || null;
  let currentPage = Math.max(1, Number(activeBlock?.page ?? 0) + 1);
  let scale = 1.15;
  let pdf: Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]> | null = null;

  root.innerHTML = `
    <div class="reviewSplit">
      <section class="pdfDesk">
        <div class="reviewToolbar">
          <select id="reviewSource">
            ${review.sources.map((source) => `<option value="${escapeHtml(source.source_path)}">${escapeHtml(source.file_name)}</option>`).join("")}
          </select>
          <button id="prevPage">上一页</button>
          <span id="pageLabel">第 ${currentPage} 页</span>
          <button id="nextPage">下一页</button>
          <button id="zoomOut">−</button>
          <button id="zoomIn">＋</button>
        </div>
        <div class="pdfViewport" id="pdfViewport">
          <div class="pdfStage" id="pdfStage">
            <canvas id="pdfCanvas"></canvas>
            <div class="pdfOverlay" id="pdfOverlay"></div>
          </div>
        </div>
      </section>
      <aside class="reviewInspector">
        <div class="qualityBanner ${review.quality_state}">
          <span>${qualityLabel(review.quality_state)}</span>
          <small>${review.issues.filter((issue) => !issue.confirmed).length} 项待处理</small>
        </div>
        <section class="issueStack">
          <h3>问题清单</h3>
          ${review.issues.length ? review.issues.map((issue) => issueCard(issue)).join("") : `<p class="emptyNote">没有待复核问题。</p>`}
        </section>
        <section class="blockEditor">
          <h3>课程块修正</h3>
          <label>课程块
            <select id="blockSelect">
              ${review.blocks.map((block) => `<option value="${escapeHtml(block.block_id)}">${escapeHtml(blockLabel(block))}</option>`).join("")}
            </select>
          </label>
          <div id="blockFields"></div>
        </section>
      </aside>
    </div>
  `;

  const sourceSelect = root.querySelector<HTMLSelectElement>("#reviewSource");
  const blockSelect = root.querySelector<HTMLSelectElement>("#blockSelect");

  async function loadSource(path: string) {
    const bytes = await readFile(path);
    pdf = await pdfjs.getDocument({ data: bytes }).promise;
    currentPage = Math.min(currentPage, pdf.numPages);
    await renderPage();
  }

  async function renderPage() {
    if (!pdf) return;
    const page = await pdf.getPage(currentPage);
    const viewport = page.getViewport({ scale });
    const pixelRatio = window.devicePixelRatio || 1;
    const canvas = root.querySelector<HTMLCanvasElement>("#pdfCanvas")!;
    const context = canvas.getContext("2d")!;
    canvas.width = Math.floor(viewport.width * pixelRatio);
    canvas.height = Math.floor(viewport.height * pixelRatio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    await page.render({
      canvasContext: context,
      canvas,
      viewport,
      transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0]
    }).promise;
    const stage = root.querySelector<HTMLElement>("#pdfStage")!;
    stage.style.width = `${viewport.width}px`;
    stage.style.height = `${viewport.height}px`;
    root.querySelector("#pageLabel")!.textContent = `第 ${currentPage} / ${pdf.numPages} 页`;
    renderOverlay(viewport.width, viewport.height, viewport.rotation);
  }

  function renderOverlay(width: number, height: number, rotation: number) {
    const overlay = root.querySelector<HTMLElement>("#pdfOverlay")!;
    const blocks = review.blocks.filter((block) => Number(block.page ?? 0) + 1 === currentPage && block.bbox);
    overlay.innerHTML = blocks.map((block) => {
      const rect = transformRect(block, width, height, rotation);
      if (!rect) return "";
      const selected = activeBlock?.block_id === block.block_id ? "selected" : "";
      return `<button class="pdfBox ${selected}" data-block-id="${escapeHtml(block.block_id)}"
        style="left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px"
        title="${escapeHtml(blockLabel(block))}"><span>${escapeHtml(block.course || "课程")}</span></button>`;
    }).join("");
    overlay.querySelectorAll<HTMLButtonElement>("[data-block-id]").forEach((button) => {
      button.addEventListener("click", () => selectBlock(button.dataset.blockId || ""));
    });
  }

  function selectBlock(blockId: string) {
    activeBlock = review.blocks.find((block) => block.block_id === blockId) || null;
    if (!activeBlock) return;
    if (blockSelect) blockSelect.value = activeBlock.block_id;
    currentPage = Number(activeBlock.page ?? 0) + 1;
    renderFields();
    void renderPage();
  }

  function renderFields() {
    const fields = root.querySelector<HTMLElement>("#blockFields")!;
    if (!activeBlock) {
      fields.innerHTML = `<p class="emptyNote">没有可编辑课程块。</p>`;
      return;
    }
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
    fields.querySelector("#applyCorrection")?.addEventListener("click", async () => {
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
    });
  }

  function valueOf(selector: string) {
    return (fieldsElement(selector)?.value || "").trim();
  }

  function fieldsElement(selector: string) {
    return root.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector);
  }

  root.querySelectorAll<HTMLButtonElement>("[data-confirm-issue]").forEach((button) => {
    button.addEventListener("click", () => void actions.confirm(button.dataset.confirmIssue || ""));
  });
  sourceSelect?.addEventListener("change", () => void loadSource(sourceSelect.value));
  blockSelect?.addEventListener("change", () => selectBlock(blockSelect.value));
  root.querySelector("#prevPage")?.addEventListener("click", () => {
    if (currentPage > 1) { currentPage -= 1; void renderPage(); }
  });
  root.querySelector("#nextPage")?.addEventListener("click", () => {
    if (pdf && currentPage < pdf.numPages) { currentPage += 1; void renderPage(); }
  });
  root.querySelector("#zoomOut")?.addEventListener("click", () => {
    scale = Math.max(0.6, scale - 0.15); void renderPage();
  });
  root.querySelector("#zoomIn")?.addEventListener("click", () => {
    scale = Math.min(2.5, scale + 0.15); void renderPage();
  });

  renderFields();
  if (activeSource) await loadSource(activeSource.source_path);
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
    <div><span>${escapeHtml(issue.code)}</span><b>${issue.confirmed ? "已确认" : issue.severity === "error" ? "阻断" : "待确认"}</b></div>
    <p>${escapeHtml(issue.message)}</p>
    ${issue.suggestion ? `<small>${escapeHtml(issue.suggestion)}</small>` : ""}
    ${issue.confirmed ? "" : `<button data-confirm-issue="${escapeHtml(issue.issue_id)}">确认已核对</button>`}
  </article>`;
}

function blockLabel(block: CourseRow) {
  return `第${block.week}周 ${block.weekday} ${block.periods?.join(",") || "-"}节 · ${block.course || "未命名课程"}`;
}

function qualityLabel(state: string) {
  if (state === "accepted") return "质量门禁已通过";
  if (state === "needs_review") return "需要人工复核";
  return "存在阻断错误";
}

function escapeHtml(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
