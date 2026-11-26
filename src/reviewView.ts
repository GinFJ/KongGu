import { readFile } from "@tauri-apps/plugin-fs";
import * as pdfjs from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { CourseRow, ParseIssue, ParseResult, PdfInspection, ReviewPayload, VerificationItem } from "./types";
import { batchReviewRisks, issuesForSource } from "./reviewRisks";
import { mountCourseActions } from "./reviewCourseActions";
import { openAiReview } from "./aiReview";
import { mountRangeReview } from "./rangeReviewView";
import type { OriginalFrame, PageLabel } from "./rangeReviewGeometry";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export type ReviewActions = {
  dateCommand?: (command: "review.slot" | "review.member" | "review.flag", request: Record<string, unknown>) => Promise<ReviewPayload>;
  rangeComplete?: () => void;
  readOriginal?: (source: ReviewPayload["sources"][number]) => Promise<Uint8Array<ArrayBuffer>>;
  reconnect?: () => Promise<void>;
  apply: (blockId: string, field: string, newValue: unknown, reason: string) => Promise<void>;
  confirm: (issueId: string) => Promise<void>;
  result: () => ParseResult | null;
  verify: (itemId: string, revision: string) => Promise<ReviewPayload>;
  mutate: (payload: Record<string, unknown>) => Promise<void>;
  ai?: (command: string, payload: Record<string, unknown>) => Promise<any>;
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
  selectSource: (sourcePath: string, blockId?: string, pageIndex?: number) => Promise<void>;
  destroy: () => void;
  getViewState: () => ReviewViewState;
};

export async function mountReview(
  root: HTMLElement,
  initialReview: ReviewPayload,
  actions: ReviewActions
): Promise<ReviewController> {
  if (initialReview.date_review && actions.dateCommand) {
    return mountRangeReview(root, initialReview, actions, mountOriginalReview);
  }
  return mountOriginalReview(root, initialReview, actions);
}

export type OriginalHooks = {
  overlay: (element: HTMLElement, frame: OriginalFrame | null) => void;
  initialSource?: string;
  failure: (message: string) => void;
};

async function mountOriginalReview(
  root: HTMLElement,
  initialReview: ReviewPayload,
  actions: ReviewActions,
  hooks?: OriginalHooks
): Promise<ReviewController> {
  let review = initialReview;
  const items = () => review.verification?.items || [];
  const flaggedIds = () => new Set(review.issues.filter(issue => !issue.confirmed && issue.severity !== "info" && issue.block_id)
    .map(issue => issue.block_id!));
  const firstPending = items().find(item => !item.confirmed);
  let activeSource = review.sources.find(s=>s.content_hash===hooks?.initialSource) ||
    (firstPending && resolveItemSource(firstPending)) || review.sources[0];
  const blocksForActiveSource = () => review.blocks.filter(block => {
    if (!activeSource || (block.source_hash && activeSource.content_hash && block.source_hash !== activeSource.content_hash)) return false;
    return block.source_file === activeSource.file_name || block.source_file === activeSource.source_path ||
      (Boolean(block.source_hash) && block.source_hash === activeSource.content_hash && review.sources.filter(s => s.content_hash === block.source_hash).length === 1);
  });
  const firstBlock = () => {
    const pending = items().find(item => !item.confirmed && resolveItemSource(item) === activeSource);
    const blocks = blocksForActiveSource();
    return blocks.find(block => pending?.block_ids.includes(block.block_id)) || blocks[0] || null;
  };
  let activeBlock: CourseRow | null = firstBlock();
  function resolveItemSource(item: VerificationItem) {
    const candidates = review.sources.filter(s => s.content_hash === item.source_hash);
    return candidates.find(s => s.file_name === item.source_file || s.source_path === item.source_file) ||
      (candidates.length === 1 ? candidates[0] : undefined);
  }
  let savingVerification = false;
  let savingCorrection = false;
  let sourceFailure = "";
  let verificationFailed = false;
  let correctionDirty = false;
  let renderedPage = 0;
  let evidenceHash = "";
  let feedback = "";
  let focusBox = true;
  let currentPage = Math.max(1, Number(activeBlock?.page ?? 0) + 1);
  let scale = 1.0;
  let pageUnitSize = { width: 0, height: 0 };
  let pageLabels: PageLabel[] = [];
  let fitPage = Boolean(hooks);
  let pdf: Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]> | null = null;
  let loadedPath = "";
  let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
  let renderVersion = 0;
  let loadVersion = 0;
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
          <label class="markToggle"><input type="checkbox" checked id="showMarks" />显示识别框</label>
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
        <section class="verifyCard" aria-labelledby="verifyTitle">
          <div class="verifyHeading"><b>逐项核验</b><span id="verifyCount"></span></div>
          <progress id="verifyProgress" aria-label="本批课程核验进度" max="1" value="0"></progress>
          <div class="verifyDetail"><span id="verifyPosition"></span><h2 id="verifyTitle"></h2><p id="verifyWhen"></p><small id="verifyEvidence"></small></div>
          <button type="button" id="verifyNext" class="primary" disabled>确认并核验下一项</button>
          <p id="verifyFeedback" role="status"></p>
          <button type="button" data-reload-review id="reloadVerification" hidden>重新载入核验结果</button>
          <button type="button" id="retrySource" hidden>重新读取原件</button>
          <button type="button" id="relinkSource" hidden>重新关联原件</button>
          <button type="button" id="returnToBatch" data-tab="issues" hidden>返回批次总览</button>
        </section>
        <div id="reviewQuality" class="qualityBanner">
          <span id="reviewQualityTitle"></span>
          <small id="reviewQualityCount"></small>
        </div>
        <details class="pdfInspection">
          <summary>文件识别情况</summary>
          <div id="inspectionSummary"></div>
        </details>
        <section class="issueStack">
          <h3>这份课表的检查记录</h3>
          <div id="reviewIssues"></div>
        </section>
        <details class="blockEditor">
          <summary>修正课程时间</summary>
          <p id="verifyEditScope" class="emptyNote"></p>
          <label>课程记录
            <select id="blockSelect"></select>
          </label>
          <div id="blockFields"></div>
        </details>
        <details class="reviewMore"><summary>补录、删除与历史记录</summary><div id="courseActions"></div></details>
        ${actions.ai ? '<section class="aiEntry"><button type="button" id="openAiReview">AI 核对当前页</button><p>先预览并遮盖个人信息，再联网读取此页。AI 建议经原文核对后可补录并重算。</p></section>' : ''}
      </aside>
    </div>
  `;

  const sourceSelect = element<HTMLSelectElement>("#reviewSource");
  const blockSelect = element<HTMLSelectElement>("#blockSelect");
  root.querySelector("#openAiReview")?.addEventListener("click", () => {
    if (actions.ai) void openAiReview(() => ({ review, sourceHash: activeSource?.content_hash || "", page: currentPage }), actions.ai);
  });
  const courseActions = mountCourseActions(element<HTMLElement>("#courseActions"),
    () => ({ review, sourceHash: activeSource?.content_hash || "", block: activeBlock, page: currentPage }), actions.mutate);

  function element<T extends HTMLElement>(selector: string) {
    const target = root.querySelector<T>(selector);
    if (!target) throw new Error(`Missing review element: ${selector}`);
    return target;
  }

  function reportLoadFailure() {
    if (destroyed) return;
    sourceFailure ||= "原文暂时无法载入，请确认文件仍在这台电脑上";
    renderedPage = 0;
    evidenceHash = "";
    element<HTMLElement>("#pdfOverlay").replaceChildren();
    hooks?.overlay(element("#pdfOverlay"), null);
    hooks?.failure(sourceFailure);
    renderVerification();
    const meta = root.querySelector<HTMLElement>("#reviewSourceMeta");
    if (meta) meta.textContent = sourceFailure;
    // 旧页图像不能继续留在画布上：否则下拉显示新文件、画布还是上一个来源。
    const canvas = root.querySelector<HTMLCanvasElement>("#pdfCanvas");
    if (canvas) {
      const context = canvas.getContext("2d");
      context?.clearRect(0, 0, canvas.width, canvas.height);
      canvas.width = 0;
      canvas.height = 0;
      canvas.style.width = "0px";
      canvas.style.height = "0px";
    }
    viewportMetrics = { width: 0, height: 0, rotation: 0 };
    const label = root.querySelector<HTMLElement>("#pageLabel");
    if (label) label.textContent = "原文不可用";
  }

  function pageLabelFailure() {
    renderedPage = 0;
    renderVerification();
    const label = root.querySelector<HTMLElement>("#pageLabel");
    if (label) label.textContent = `第 ${currentPage} 页暂时无法显示`;
    hooks?.failure(`第 ${currentPage} 页暂时无法显示`);
  }

  function renderQuality() {
    const banner = element<HTMLElement>("#reviewQuality");
    banner.hidden = !review.issues.some(issue => !issue.confirmed && issue.blocks_export);
    banner.className = `qualityBanner ${review.quality_state}`;
    element<HTMLElement>("#reviewQualityTitle").textContent = qualityLabel(review.quality_state);
    element<HTMLElement>("#reviewQualityCount").textContent = `${review.issues.filter((issue) => !issue.confirmed && issue.blocks_export).length} 项待处理`;
  }

  function renderSources() {
    activeSource = review.sources.find(source => source.content_hash === activeSource?.content_hash && source.file_name === activeSource?.file_name) || review.sources[0];
    const nextKey = review.sources.map((source) => `${source.source_path}|${source.file_name}|${source.kind}`).join("\u0000");
    if (sourceOptionsKey !== nextKey) {
      sourceOptionsKey = nextKey;
      sourceSelect.innerHTML = review.sources
        .map((source, index) => `<option value="${index}">${escapeHtml(source.file_name)} · ${escapeHtml(source.kind)}课表</option>`)
        .join("");
    }
    if (activeSource) {
      sourceSelect.value = String(review.sources.indexOf(activeSource));
      const index = review.sources.indexOf(activeSource);
      element<HTMLElement>("#reviewSourceMeta").textContent = sourceFailure || (activeSource.source_path
        ? `文件 ${index + 1}/${review.sources.length} · ${activeSource.kind}课表`
        : `文件 ${index + 1}/${review.sources.length} · 请点「关联原件」继续核验`);
    }
  }

  function renderIssues() {
    const scopedIssues = activeSource ? issuesForSource(review, activeSource) : [];
    const result = actions.result();
    const current = result && batchReviewRisks(result, review).files.find(file => file.source === activeSource);
    const summary = current && ["blocked", "attention"].includes(current.state) && current.reason
      ? `<p class="sourceRisk"><b>${escapeHtml(current.reason)}</b> · 请在批次总览查看处理要求。</p>` : "";
    const nextKey = summary + JSON.stringify(scopedIssues);
    if (issuesKey === nextKey) return;
    issuesKey = nextKey;
    element<HTMLElement>("#reviewIssues").innerHTML = summary + (scopedIssues.length
      ? scopedIssues.map((issue) => issueCard(issue)).join("")
      : summary ? "" : `<p class="emptyNote">这份课表暂无异常记录。</p>`);
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
    const inspection = review.inspections.find(item => item.source_hash && item.source_hash === activeSource.content_hash) ||
      review.inspections.find(item => !item.source_hash && item.source_file === activeSource.file_name);
    const html = inspectionCard(inspection);
    if (target.dataset.renderedHtml !== html) {
      target.dataset.renderedHtml = html;
      target.innerHTML = html;
    }
  }

  function renderFields(force = false) {
    courseActions.render();
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
    correctionDirty = false;
    fields.innerHTML = `
      <div class="identityLine">${escapeHtml(activeBlock.member_key || activeBlock.name)}</div>
      <label>课程<input id="editCourse" value="${escapeHtml(activeBlock.course || "")}" /></label>
      <div class="fieldPair">
        <label>周次<input id="editWeek" type="number" min="1" max="30" value="${escapeHtml(String(activeBlock.week ?? ""))}" /></label>
        <label>星期<select id="editWeekday">${["周一","周二","周三","周四","周五","周六","周日"].map((day) => `<option ${day === activeBlock?.weekday ? "selected" : ""}>${day}</option>`).join("")}</select></label>
      </div>
      <label>节次<input id="editPeriods" value="${escapeHtml((activeBlock.periods || []).join(","))}" placeholder="例如 1,2" /></label>
      <label>修正原因<textarea id="editReason" rows="2" placeholder="说明为何需要修正"></textarea></label>
      <button id="applyCorrection" class="primary">保存修正</button>
    `;
  }

  async function loadSource(path: string, resetPosition = false) {
    if (destroyed) return;
    const version = ++loadVersion;
    renderVersion += 1;
    sourceFailure = "";
    renderedPage = 0;
    hooks?.overlay(element("#pdfOverlay"), null);
    viewportMetrics = { width: 0, height: 0, rotation: 0 };
    element<HTMLElement>("#pdfOverlay").replaceChildren();
    renderVerification();
    if (!path) {
      await destroyPdf();
      loadedPath = "";
      reportLoadFailure();
      return;
    }
    if (loadedPath !== path) {
      renderTask?.cancel();
      await destroyPdf();
      const source = activeSource;
      const bytes = await readFile(path).catch(() => actions.readOriginal?.(source).catch(() => null));
      if (destroyed || version !== loadVersion) return;
      if (!bytes) { reportLoadFailure(); return; }
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
      if (destroyed || version !== loadVersion) return;
      evidenceHash = Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
      if (activeSource.content_hash && evidenceHash !== activeSource.content_hash) {
        sourceFailure = "原件内容已变化，请重新导入并识别";
        reportLoadFailure();
        return;
      }
      const loadedPdf = await pdfjs.getDocument({
        data: bytes,
        cMapUrl: new URL("/pdfjs/cmaps/", window.location.href).href,
        cMapPacked: true,
        standardFontDataUrl: new URL("/pdfjs/standard_fonts/", window.location.href).href
      }).promise.catch(() => null);
      if (!loadedPdf) {
        if (!destroyed && version === loadVersion) reportLoadFailure();
        return;
      }
      if (destroyed || version !== loadVersion) {
        // 载入期间视图已销毁或切到别的文件：新文档必须自己释放，不能留在内存里。
        const destroyDocument = (loadedPdf as unknown as { destroy?: () => Promise<void> | void }).destroy;
        if (destroyDocument) void destroyDocument.call(loadedPdf);
        else loadedPdf.cleanup();
        return;
      }
      pdf = loadedPdf;
      loadedPath = path;
    }
    if (destroyed || version !== loadVersion) return;
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
    renderedPage = 0;
    hooks?.overlay(element("#pdfOverlay"), null);
    element<HTMLElement>("#pdfOverlay").replaceChildren();
    renderVerification();
    const viewportElement = element<HTMLElement>("#pdfViewport");
    const scrollLeft = viewportElement.scrollLeft;
    const scrollTop = viewportElement.scrollTop;
    renderTask?.cancel();
    // 视图销毁或文档被替换后取页会失败，这属于正常取消，不能变成未处理的 Promise rejection。
    const page = await pdf.getPage(currentPage).catch(() => null);
    if (!page) {
      if (!destroyed && version === renderVersion) pageLabelFailure();
      return;
    }
    if (destroyed || version !== renderVersion) return;
    if (fitPage && viewportElement.clientWidth > 0) {
      const style = getComputedStyle(viewportElement);
      const available = viewportElement.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      scale = Math.max(.15, Math.min(2.5, available / page.getViewport({scale:1}).width));
    }
    const viewport = page.getViewport({ scale });
    pageUnitSize = { width: page.view[2] - page.view[0], height: page.view[3] - page.view[1] };
    viewportMetrics = { width: viewport.width, height: viewport.height, rotation: viewport.rotation };
    const pixelRatio = window.devicePixelRatio || 1;
    const canvas = element<HTMLCanvasElement>("#pdfCanvas");
    const context = canvas.getContext("2d");
    if (!context) {
      pageLabelFailure();
      return;
    }
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
      if (destroyed || version !== renderVersion) return;
      if ((error as { name?: string }).name === "RenderingCancelledException") return;
      pageLabelFailure();
      return;
    }
    if (destroyed || version !== renderVersion) return;
    const stage = element<HTMLElement>("#pdfStage");
    stage.style.width = `${viewport.width}px`;
    stage.style.height = `${viewport.height}px`;
    element<HTMLElement>("#pageLabel").textContent = `第 ${currentPage} / ${pdf.numPages} 页`;
    renderedPage = currentPage;
    pageLabels = [];
    renderVerification();
    renderOverlay();
    if (hooks && viewport.rotation === 0) {
      void page.getTextContent().then(content=>{
        if (destroyed || version !== renderVersion) return;
        pageLabels = content.items.flatMap(item=>{
          if (!("str" in item)) return [];
          const [x,y]=viewport.convertToViewportPoint(item.transform[4]+item.width/2,item.transform[5]+item.height*.35);
          return [{text:item.str.replace(/\s+/g,""),x,y}];
        });
        renderOverlay();
      }).catch(()=>{ /* Scanned originals still support explicit slot selection. */ });
    }
    viewportElement.scrollLeft = scrollLeft;
    viewportElement.scrollTop = scrollTop;
    if (focusBox) revealBlock();
    focusBox = false;
  }

  function handlePdfWheel(event: WheelEvent) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const nextScale = Math.max(hooks ? .15 : .6, Math.min(2.5, scale + (event.deltaY < 0 ? 0.1 : -0.1)));
    if (nextScale === scale) return;
    fitPage = false;
    scale = Number(nextScale.toFixed(2));
    void renderPage();
  }

  function activeItem() {
    return activeBlock ? items().find(item => item.block_ids.includes(activeBlock!.block_id)) : undefined;
  }

  function renderVerification() {
    const summary = review.verification;
    const item = activeItem();
    const block = activeBlock;
    const ready = Boolean(activeSource?.source_path && evidenceHash === activeSource.content_hash &&
      loadedPath === activeSource.source_path && renderedPage > 0 && renderedPage === currentPage &&
      (block?.page == null || renderedPage === block.page + 1));
    element("#verifyCount").textContent = `已核验 ${summary?.confirmed || 0} / ${summary?.total || 0}`;
    const progress = element<HTMLProgressElement>("#verifyProgress");
    progress.max = summary?.total || 1;
    progress.value = summary?.confirmed || 0;
    element("#verifyPosition").textContent = item
      ? `第 ${items().indexOf(item) + 1} 项 · ${item.confirmed ? "已核验" : "待核验"}` : "当前课表";
    element("#verifyTitle").textContent = block?.course || (block ? "未命名课程" : "没有可核验的课程");
    element("#verifyWhen").textContent = block
      ? [block.name, `第 ${(item?.weeks || [block.week]).join("、")} 周`, block.weekday, `第 ${(item?.periods || block.periods)?.join("、") || block.period || "?"} 节`].filter(Boolean).join(" · ") : "";
    element("#verifyEvidence").textContent = !block ? "请对照原件检查是否漏课。" : block.bbox && block.page != null
      ? `原件第 ${block.page + 1} 页 · 识别框定位` : "缺少定位框，请在原文中按课程和时间核对。";
    const button = element<HTMLButtonElement>("#verifyNext");
    const pending = summary?.pending || 0;
    button.disabled = savingVerification || savingCorrection || correctionDirty || !item || !ready || !pending;
    button.textContent = savingVerification ? "正在保存…" : item?.confirmed
      ? (pending ? "前往下一项待核验课程" : "本批课程已核验") : pending === 1 ? "确认最后一项" : "确认并核验下一项";
    element("#verifyFeedback").textContent = sourceFailure || feedback || (correctionDirty ? "请先保存修正，再核验当前记录。" :
      block && !ready ? "请先查看当前课程对应的原件页。" : "");
    element("#reloadVerification").hidden = !verificationFailed;
    element("#retrySource").hidden = !sourceFailure || !activeSource?.source_path;
    element("#relinkSource").hidden = !sourceFailure || !actions.reconnect;
    element("#verifyEditScope").textContent = (item?.block_ids.length || 0) > 1
      ? "此框包含多条课程记录。请选择要修改的具体周次和节次，保存后再核验。" : "";
    const finish = element<HTMLButtonElement>("#returnToBatch");
    finish.hidden = !!item && !!pending;
    const canOpenResult = !pending && actions.result()?.can_export;
    finish.dataset.tab = canOpenResult ? "availability" : "issues";
    finish.textContent = canOpenResult ? "查看空课结果" : "返回批次总览";
    element<HTMLButtonElement>("#prevPage").disabled = !pdf || currentPage <= 1;
    element<HTMLButtonElement>("#nextPage").disabled = !pdf || currentPage >= pdf.numPages;
  }

  async function verifyNext() {
    const original = activeItem();
    if (!original || element<HTMLButtonElement>("#verifyNext").disabled) return;
    savingVerification = true;
    verificationFailed = false;
    feedback = "";
    renderVerification();
    try {
      const updated = original.confirmed ? review : await actions.verify(original.item_id, review.revision);
      if (destroyed) return;
      if (!updated.verification?.items.some(item => item.item_id === original.item_id && item.confirmed)) {
        throw new Error("未收到当前课程的核验回执，请重新载入。");
      }
      await update(updated);
      if (destroyed || activeItem()?.item_id !== original.item_id) return;
      const queue = items();
      const position = queue.findIndex(item => item.item_id === original.item_id);
      const next = queue.slice(position + 1).find(item => !item.confirmed) || queue.find(item => !item.confirmed);
      if (next) {
        const source = resolveItemSource(next);
        if (!source) throw new Error("下一项的原件未找到，请返回批次总览。");
        if (source !== activeSource) await switchSource(source, next.block_ids[0]);
        else selectBlock(next.block_ids[0]);
      } else {
        feedback = review.issues.some(issue => issue.blocks_export && !issue.confirmed)
          ? "课程核验完成。请返回批次总览处理剩余异常。" : "课程核验完成，可到空课结果查看导出状态。";
      }
    } catch (error) {
      verificationFailed = true;
      feedback = error instanceof Error ? error.message : String(error);
    } finally {
      savingVerification = false;
      if (!destroyed) renderVerification();
    }
  }

  function renderOverlay() {
    const overlay = element<HTMLElement>("#pdfOverlay");
    const { width, height, rotation } = viewportMetrics;
    if (hooks) {
      hooks.overlay(overlay, width && height && renderedPage && renderedPage === currentPage && evidenceHash === activeSource?.content_hash
        ? { page: currentPage - 1, width, height, rotation, pageSize: pageUnitSize, canvas:element<HTMLCanvasElement>("#pdfCanvas"),
          labels:pageLabels, sourceHash:evidenceHash, marksVisible:element<HTMLInputElement>("#showMarks").checked,
          rectFor: row=>transformRect(row,width,height,rotation,pageUnitSize) } : null);
      return;
    }
    if (!width || !height || !renderedPage || !element<HTMLInputElement>("#showMarks").checked) {
      overlay.replaceChildren();
      return;
    }
    const byBlock = new Map(items().flatMap(item => item.block_ids.map(id => [id, item] as const)));
    const flagged = flaggedIds();
    const shown = new Set<string>();
    const selectedItem = activeItem();
    overlay.innerHTML = blocksForActiveSource().filter(block => block.page != null && block.page + 1 === currentPage && block.bbox).map(block => {
      const item = byBlock.get(block.block_id);
      const id = item?.item_id || block.block_id;
      if (shown.has(id)) return "";
      shown.add(id);
      const rect = transformRect(block, width, height, rotation, pageUnitSize);
      if (!rect) return "";
      const selected = item ? selectedItem?.item_id === item.item_id : activeBlock?.block_id === block.block_id;
      const hasIssue = (item?.block_ids || [block.block_id]).some(id => flagged.has(id));
      const status = [item?.confirmed ? "已核验" : "待核验", hasIssue ? "有疑点" : ""].filter(Boolean).join(" · ");
      return `<button type="button" class="pdfBox ${selected ? "selected" : ""} ${item?.confirmed ? "checked" : ""} ${hasIssue ? "flagged" : ""}" data-block-id="${escapeHtml(block.block_id)}"
        style="left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px"
        title="${escapeHtml(blockLabel(block))} · ${status}" aria-pressed="${selected}"><span>${status} · ${escapeHtml(block.course || "课程")}</span></button>`;
    }).join("");
  }

  function revealBlock() {
    root.querySelector<HTMLElement>(".pdfBox.selected")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function selectBlock(blockId: string) {
    activeBlock = blocksForActiveSource().find((block) => block.block_id === blockId) || null;
    if (!activeBlock) return;
    feedback = "";
    correctionDirty = false;
    blockSelect.value = activeBlock.block_id;
    currentPage = Number(activeBlock.page ?? 0) + 1;
    renderFields(true);
    renderVerification();
    if (renderedPage === currentPage) {
      renderOverlay();
      revealBlock();
    } else {
      focusBox = true;
      void renderPage();
    }
  }

  function valueOf(selector: string) {
    return (root.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector)?.value || "").trim();
  }

  const handleRootClick = (event: Event) => {
    const target = event.target as HTMLElement;
    if (target.closest("#verifyNext")) {
      if (!(event instanceof MouseEvent) || event.detail < 2) void verifyNext();
      return;
    }
    if (target.closest("#relinkSource")) { void actions.reconnect?.(); return; }
    if (target.closest("#retrySource") && activeSource) {
      void loadSource(activeSource.source_path).catch(reportLoadFailure);
      return;
    }
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
      fitPage = false;
      scale = Math.max(hooks ? .15 : 0.6, scale - 0.15);
      void renderPage();
      return;
    }
    if (target.closest("#zoomIn")) {
      fitPage = false;
      scale = Math.min(2.5, scale + 0.15);
      void renderPage();
      return;
    }
    if (target.closest("#applyCorrection")) void applyCorrection();
  };
  root.addEventListener("click", handleRootClick);

  async function selectSource(sourcePath: string, blockId?: string, pageIndex?: number) {
    const exact = review.sources.find(source => source.source_path === sourcePath);
    const candidates = review.sources.filter(source => source.file_name === sourcePath);
    const nextSource = exact || (candidates.length === 1 ? candidates[0] : undefined);
    if (!nextSource) throw new Error("无法唯一定位这份课表，请在原文核对中选择文件。");
    await switchSource(nextSource, blockId, pageIndex);
  }

  async function switchSource(nextSource: ReviewPayload["sources"][number], blockId?: string, pageIndex?:number) {
    activeSource = nextSource;
    activeBlock = blockId ? blocksForActiveSource().find(block => block.block_id === blockId) || firstBlock() : firstBlock();
    focusBox = true;
    feedback = "";
    correctionDirty = false;
    currentPage = Math.max(1, Number(pageIndex ?? activeBlock?.page ?? 0) + 1);
    blockOptionsKey = "";
    fieldsKey = "";
    renderSources();
    renderIssues();
    renderInspection();
    renderBlockOptions();
    renderFields();
    await loadSource(activeSource.source_path, true).catch(reportLoadFailure);
  }
  sourceSelect.addEventListener("change", () => {
    void switchSource(review.sources[Number(sourceSelect.value)]).catch(reportLoadFailure);
  });
  element("#verifyNext").addEventListener("keydown", event => {
    if (event.repeat && ["Enter", " "].includes(event.key)) event.preventDefault();
  });
  element("#showMarks").addEventListener("change", renderOverlay);
  element("#blockFields").addEventListener("input", () => { correctionDirty = true; renderVerification(); });
  const pdfViewport = element<HTMLElement>("#pdfViewport");
  let resizeFrame=0, lastViewportWidth=pdfViewport.clientWidth;
  const viewportObserver=hooks ? new ResizeObserver(()=>{
    const width=pdfViewport.clientWidth;
    if(!width || width===lastViewportWidth)return;
    lastViewportWidth=width;
    if(!pdf || !fitPage || destroyed)return;
    cancelAnimationFrame(resizeFrame);
    resizeFrame=requestAnimationFrame(()=>{if(!destroyed && fitPage)void renderPage();});
  }) : null;
  viewportObserver?.observe(pdfViewport);
  pdfViewport.addEventListener("wheel", handlePdfWheel, { passive: false });
  blockSelect.addEventListener("change", () => selectBlock(blockSelect.value));

  async function applyCorrection() {
    // 保存期间必须锁定目标块：首个字段落库会触发上层重渲染，若用户在 await
    // 期间切换课程记录或来源，剩余字段会写到另一个块甚至另一个成员。
    const targetBlock = activeBlock;
    if (!targetBlock) return;
    const blockId = targetBlock.block_id;
    const button = root.querySelector<HTMLButtonElement>("#applyCorrection");
    // 逐字段保存期间禁用按钮，避免重复点击写出多条相同的人工修正记录。
    if (button?.disabled) return;
    if (button) button.disabled = true;
    savingCorrection = true;
    renderVerification();
    const lockedSelects = [sourceSelect, blockSelect];
    lockedSelects.forEach((select) => {
      select.disabled = true;
    });
    const reason = valueOf("#editReason");
    const updates: Array<[string, unknown]> = [
      ["course", valueOf("#editCourse")],
      ["week", Number(valueOf("#editWeek"))],
      ["weekday", valueOf("#editWeekday")],
      ["periods", valueOf("#editPeriods").split(/[,，\s]+/).filter(Boolean).map(Number)]
    ];
    const original: Record<string, unknown> = {
      course: targetBlock.course || "",
      week: targetBlock.week,
      weekday: targetBlock.weekday,
      periods: targetBlock.periods || []
    };
    try {
      for (const [field, value] of updates) {
        if (JSON.stringify(original[field]) !== JSON.stringify(value)) {
          await actions.apply(blockId, field, value, reason);
        }
      }
      correctionDirty = false;
      renderVerification();
    } catch (error) {
      feedback = error instanceof Error ? error.message : String(error);
      // 业务错误已由上层状态栏提示；内部类型错误必须暴露，不能把剩余字段静默丢掉。
      if (error instanceof TypeError) throw error;
    } finally {
      savingCorrection = false;
      renderVerification();
      const current = root.querySelector<HTMLButtonElement>("#applyCorrection");
      if (current) current.disabled = false;
      lockedSelects.forEach((select) => {
        select.disabled = false;
      });
    }
  }

  async function update(nextReview: ReviewPayload) {
    if (destroyed) return;
    const previousPath = activeSource?.source_path || "";
    if (verificationFailed) feedback = "";
    verificationFailed = false;
    review = nextReview;
    renderQuality();
    renderSources();
    renderIssues();
    renderBlockOptions();
    renderFields();
    renderInspection();
    renderVerification();
    renderOverlay();
    if (activeSource && (previousPath !== activeSource.source_path || (sourceFailure && activeSource.source_path))) {
      try {
        await loadSource(activeSource.source_path, true);
      } catch {
        reportLoadFailure();
      }
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
    loadVersion += 1;
    renderTask?.cancel();
    viewportObserver?.disconnect();
    cancelAnimationFrame(resizeFrame);
    root.removeEventListener("click", handleRootClick);
    courseActions.destroy();
    pdfViewport.removeEventListener("wheel", handlePdfWheel);
    void destroyPdf();
  }

  async function destroyPdf() {
    // 必须同时清掉 loadedPath：否则加载新文件失败后仍指向旧路径，
    // 再切回旧文件会被误判为“已加载”，翻页与缩放会永久失效。
    loadedPath = "";
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
  renderVerification();
  if (activeSource) {
    try {
      await loadSource(activeSource.source_path);
    } catch {
      reportLoadFailure();
    }
  }

  return { update, selectSource, destroy, getViewState };
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

function transformRect(block: CourseRow, width: number, height: number, rotation: number, pageSize: { width: number; height: number }) {
  if (!block.bbox) return null;
  const sourceWidth = Number(block.page_width || pageSize.width);
  const sourceHeight = Number(block.page_height || pageSize.height);
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
  return `<article class="issueCard ${escapeHtml(state)}">
    <div><b>${issue.confirmed ? "已核对" : issue.severity === "info" ? "范围说明" : issue.severity === "error" ? "必须处理" : "请核对"}</b></div>
    <p>${escapeHtml(friendlyMessage(issue.message))}</p>
    ${issue.suggestion ? `<small>${escapeHtml(friendlyMessage(issue.suggestion))}</small>` : ""}
    ${issueAction(issue)}
  </article>`;
}

function issueAction(issue: ParseIssue) {
  if (issue.confirmed || issue.blocks_export === false) return "";
  if (issue.severity === "error") return `<small class="issueActionHint">请先补充或修正课表，再重新生成空课表。</small>`;
  return `<button data-confirm-issue="${escapeHtml(issue.issue_id)}">确认已核对</button>`;
}

function blockLabel(block: CourseRow) {
  return `第${block.week}周 ${block.weekday} ${block.periods?.join(",") || "-"}节 · ${block.course || "未命名课程"}`;
}

function qualityLabel(state: string) {
  if (state === "accepted") return "已完成识别检查";
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
