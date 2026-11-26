import type { CourseRow, ReviewPayload } from "./types";

type Context = { review: ReviewPayload; sourceHash: string; block: CourseRow | null; page: number };
type Mutation = (payload: Record<string, unknown>) => Promise<void>;
const labels: Record<string, string> = {
  add: "补录漏课", delete: "删除误识别记录", restore: "恢复已删除记录",
  restore_region: "恢复误排除区域", exclude_region: "排除非课程区域", undo: "撤销课程操作"
};
const escape = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[c]!);
const courseLabel = (b: CourseRow) => `${b.course || "课程"} · 第${b.week}周${b.weekday} · 第${b.periods?.join("、") || b.period}节`;
function changeSummary(correction: Record<string, unknown>): string {
  if (correction.field !== "course_operation") {
    return `${JSON.stringify(correction.original_value)} → ${JSON.stringify(correction.new_value)}`;
  }
  const operation = correction.new_value as { changes: Array<{ before: CourseRow | null; after: CourseRow | null }> };
  return operation.changes.map(change => `${change.before ? courseLabel(change.before) : "无此记录"} → ${change.after ? courseLabel(change.after) : "已移出占用"}`).join("；");
}

export function mountCourseActions(root: HTMLElement, context: () => Context, mutate: Mutation) {
  let busy = false;
  let renderedKey = "";
  function render() {
    const { review, sourceHash, block, page } = context();
    const key = `${review.revision}|${sourceHash}|${block?.block_id}`;
    if (key === renderedKey || busy) return;
    renderedKey = key;
    const removed = (review.removed_blocks || []).filter(b => b.source_hash === sourceHash);
    const regions = (review.regions || []).filter(r => r.source_hash === sourceHash);
    const history = review.corrections.filter(c => c.source_hash === sourceHash);
    const operations = (review.applied_operations || []).filter(c => c.source_hash === sourceHash);
    const latest = operations[operations.length - 1]?.new_value as Record<string, unknown> | undefined;
    const appliedIds = new Set(operations.map(c => (c.new_value as Record<string, unknown>).operation_id));
    root.innerHTML = `
      <section class="manualReviewActions">
        <h3>补录与删除</h3>
        <p class="reviewActionHint">当前原件中的漏课可补录。删除只移出占用计算，原件和历史保留，可恢复。</p>
        <label>操作原因<textarea id="courseOperationReason" rows="2" placeholder="说明原件内容及修正原因"></textarea></label>
        ${block ? `<p class="currentCourseSummary">${escape(courseLabel(block))}</p><button type="button" data-course-action="delete" data-block="${escape(block.block_id)}">删除当前误识别记录</button>` : ""}
        <details class="manualCourseForm">
          <summary>补录漏课</summary>
          <label>课程名称<input id="manualCourseName" maxlength="500" /></label>
          <div class="fieldPair">
            <label>原件页码<input id="manualCoursePage" type="number" min="1" value="${page}" /></label>
            <label>周次<input id="manualCourseWeek" type="number" min="1" max="30" value="${block?.week || 1}" /></label>
          </div>
          <label>星期<select id="manualCourseDay">${["周一","周二","周三","周四","周五","周六","周日"].map(day => `<option ${day === block?.weekday ? "selected" : ""}>${day}</option>`).join("")}</select></label>
          <label>节次<input id="manualCoursePeriods" placeholder="例如 3,4" /></label>
          <p class="reviewActionHint">午间第一、二节填写 12、13，对应 12:40–13:25、13:30–14:15。一次补录对应一周一天。</p>
          <button type="button" data-course-action="add">保存补录并重算</button>
        </details>
        ${regions.length ? `<details class="manualCourseForm"><summary>自动排除与疑似非课程区域</summary>
          <label>原文区域<select id="manualRegion">${regions.map(r => `<option value="${escape(r.region_id)}">第${r.page + 1}页 · 第${r.week}周${escape(r.weekday)} · ${r.periods.join("、")}节 · ${r.action === "excluded_from_preview_occupancy" ? "已自动排除" : "仍计入占用"}</option>`).join("")}</select></label>
          <label>恢复为课程时填写课名<input id="restoredRegionCourse" maxlength="500" /></label>
          <button type="button" data-course-action="restore_region">恢复为课程并重算</button>
          <button type="button" data-course-action="exclude_region">确为非课程，排除并重算</button>
        </details>` : ""}
        ${removed.length ? `<details class="manualCourseForm"><summary>已删除记录（${removed.length}）</summary>
          <label>选择要恢复的记录<select id="removedCourse">${removed.map(b => `<option value="${escape(b.block_id)}">${escape(courseLabel(b))}</option>`).join("")}</select></label>
          <button type="button" data-course-action="restore">恢复记录并重算</button>
        </details>` : ""}
        <details class="manualCourseForm"><summary>本原件修正记录（${history.length}）</summary>
          ${history.length ? `<ol class="reviewHistory">${history.map(c => {
            const op = c.new_value as Record<string, unknown>;
            const name = c.field === "course_operation" ? labels[String(op.action)] : "修改课程字段";
            const state = c.stale ? "（已过期，未应用）" : c.field === "course_operation" && !appliedIds.has(op.operation_id) ? "（未应用到本次结果）" : "";
            return `<li><strong>${escape(name)}${state}</strong><span>${escape(changeSummary(c))}</span><span>原因：${escape(c.reason)}</span><small>${escape(c.operator_id)} · ${escape(c.created_at)}</small></li>`;
          }).join("")}</ol>` : '<p class="reviewActionHint">尚无人工修正。</p>'}
          ${latest ? `<button type="button" data-course-action="undo" data-undo="${escape(latest.operation_id)}">撤销本原件最近一次课程操作</button>` : ""}
        </details>
        <p id="courseOperationStatus" role="status" aria-live="polite"></p>
      </section>`;
  }
  const value = (selector: string) => root.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(selector)?.value || "";
  async function click(event: Event) {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-course-action]");
    if (!button || busy) return;
    const { review, sourceHash } = context();
    const action = button.dataset.courseAction!;
    const status = root.querySelector<HTMLElement>("#courseOperationStatus")!;
    const reason = value("#courseOperationReason").trim();
    if (!reason) { status.textContent = "请先填写操作原因。"; return; }
    const payload: Record<string, unknown> = { action, reason, source_hash: sourceHash,
      revision: review.revision, operation_id: crypto.randomUUID(), operator_id: "本机用户" };
    if (action === "add") payload.course = { course: value("#manualCourseName"), page: Number(value("#manualCoursePage")) - 1,
      week: Number(value("#manualCourseWeek")), weekday: value("#manualCourseDay"),
      periods: value("#manualCoursePeriods").split(/[,，\s]+/).filter(Boolean).map(Number) };
    if (action === "delete") payload.block_id = button.dataset.block;
    if (action === "restore") payload.block_id = value("#removedCourse");
    if (action === "undo") payload.undo_id = button.dataset.undo;
    if (action.endsWith("_region")) {
      payload.region_id = value("#manualRegion");
      payload.course = { course: value("#restoredRegionCourse") };
    }
    busy = true;
    root.querySelectorAll<HTMLButtonElement>("button").forEach(b => b.disabled = true);
    status.textContent = "正在保存并重算…";
    try {
      await mutate(payload);
      busy = false;
      renderedKey = "";
      render();
      root.querySelector<HTMLElement>("#courseOperationStatus")!.textContent = "已保存并重算，请查看剩余待处理问题和空课结果。";
    } catch (error) {
      status.textContent = String(error instanceof Error ? error.message : error);
    } finally {
      busy = false;
      root.querySelectorAll<HTMLButtonElement>("button").forEach(b => b.disabled = false);
    }
  }
  root.addEventListener("click", click);
  return { render, destroy: () => root.removeEventListener("click", click) };
}
