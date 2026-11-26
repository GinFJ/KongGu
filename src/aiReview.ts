import type { ReviewPayload } from "./types";
import "./aiReview.css";

type Request = (command: string, payload: Record<string, unknown>) => Promise<any>;
type Candidate = { id: string; course: string; location: string; evidence: string; uncertainty: string;
  week: number; weekday: string; periods: number[]; missing_periods: number[]; state: string; date: string };
const escape = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[c]!);

export async function openAiReview(context: () => { review: ReviewPayload; sourceHash: string; page: number }, request: Request) {
  const initial = context();
  const source = initial.review.sources.find(s => s.content_hash === initial.sourceHash);
  if (!source) throw new Error("请先选择课表原件。");
  const dialog = document.createElement("dialog");
  dialog.className = "aiReviewDialog";
  dialog.setAttribute("aria-labelledby", "aiTitle");
  dialog.innerHTML = `<header><div><h2 id="aiTitle">AI 核对原页</h2><p>${escape(source.file_name)} · 第 ${initial.page} 页</p></div><button type="button" data-close aria-label="关闭 AI 核对">关闭</button></header>
    <p class="aiNotice">本功能将选定页面发送至 OpenAI，使用安装包提供者的 API 额度。默认不联网；课程计算仍在本机完成。请遮盖姓名、学号等个人信息，保留表头、日期和课程图例。</p>
    <p id="aiStatus" role="status" aria-live="polite">正在准备本机预览，尚未发送…</p>
    <section id="aiPreviewSection" hidden>
      <p>下图是发送预览。拖动可画白色遮盖区域；已自动遮盖可定位的文本层姓名，图片中的身份信息需自行检查。</p>
      <div class="aiCanvasWrap"><canvas id="aiCanvas" aria-label="发送页面预览，可拖动遮盖个人信息"></canvas></div>
      <button type="button" id="aiResetMasks">清除手动画出的遮盖</button>
      <div class="aiOptions"><label>识别模型<select id="aiModel"></select></label><label class="aiConsent"><input type="checkbox" id="aiConsent" />我已检查遮盖内容，同意发送此页并使用提供者的额度</label></div>
      <p class="aiFinePrint">OpenAI API 默认不用于模型训练，但可能保留安全监测数据。每次只识别当前页；跨页上下文不足会保留待核对。网络失败不自动重试，本机每天最多 50 次请求。</p>
      <button type="button" id="aiSend" class="primaryButton">发送此页，开始 AI 核对</button>
    </section><section id="aiResults" hidden></section>`;
  document.body.append(dialog);
  let closed = false;
  dialog.addEventListener("close", () => { closed = true; dialog.remove(); }, { once: true });
  dialog.querySelector("[data-close]")!.addEventListener("click", () => dialog.close());
  dialog.showModal();
  const $ = <T extends HTMLElement>(s: string) => dialog.querySelector<T>(s)!;
  const status = $("#aiStatus");
  let preview: any;
  try {
    preview = await request("ai.prepare", { job_id: initial.review.job_id, source_hash: initial.sourceHash, page: initial.page - 1 });
  } catch (error) { if (!closed) status.textContent = String(error instanceof Error ? error.message : error); return; }
  if (closed) return;
  const canvas = $<HTMLCanvasElement>("#aiCanvas");
  canvas.width = preview.width; canvas.height = preview.height;
  const ctx = canvas.getContext("2d")!;
  const img = new Image();
  img.src = preview.image;
  try { await img.decode(); } catch { status.textContent = "无法显示发送预览，未发送任何页面。"; return; }
  if (closed) return;
  let masks: number[][] = [];
  let start: number[] | null = null;
  let dragging: number[] | null = null;
  let busy = false;
  function paint() {
    ctx.drawImage(img, 0, 0);
    ctx.fillStyle = "#fff";
    for (const r of [...masks, ...(dragging ? [dragging] : [])]) {
      const x = Math.floor(r[0]*canvas.width), y = Math.floor(r[1]*canvas.height);
      ctx.fillRect(x, y, Math.ceil(r[2]*canvas.width)-x, Math.ceil(r[3]*canvas.height)-y);
    }
  }
  function point(e: PointerEvent) {
    const r = canvas.getBoundingClientRect();
    return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)), Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];
  }
  canvas.addEventListener("pointerdown", e => { if (!busy) { start=point(e); canvas.setPointerCapture(e.pointerId); } });
  canvas.addEventListener("pointermove", e => {
    if (!start || busy) return;
    const p=point(e); dragging=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.max(start[0],p[0]),Math.max(start[1],p[1])]; paint();
  });
  canvas.addEventListener("pointerup", () => {
    if (dragging && dragging[2]>dragging[0] && dragging[3]>dragging[1] && masks.length<50) masks.push(dragging);
    start=null; dragging=null; paint();
    $<HTMLInputElement>("#aiConsent").checked=false;
  });
  canvas.addEventListener("pointercancel", () => { start=null; dragging=null; paint(); });
  $("#aiResetMasks").addEventListener("click", () => { if (!busy) { masks=[]; paint(); $<HTMLInputElement>("#aiConsent").checked=false; } });
  const model = $<HTMLSelectElement>("#aiModel");
  model.innerHTML = preview.models.map((m: string) => `<option>${escape(m)}</option>`).join("");
  model.value = preview.model;
  $("#aiPreviewSection").hidden=false; paint();
  status.textContent = preview.configured ? "预览已就绪。检查遮盖后再发送；结果仅作为核对建议。" : "此安装包未配置 API 凭据，离线识别仍可使用。";
  const send = $<HTMLButtonElement>("#aiSend"); send.disabled=!preview.configured;
  send.addEventListener("click", async () => {
    if (busy) return;
    if (!$<HTMLInputElement>("#aiConsent").checked) { status.textContent="请先检查预览并勾选联网识别同意项。"; return; }
    busy=true; send.disabled=true; model.disabled=true;
    $<HTMLButtonElement>("#aiResetMasks").disabled=true;
    status.textContent="正在联网读取此页并比较课程，可能需要约 3 分钟。课表尚未修改…";
    try {
      const result = await request("ai.run", { token: preview.token, masks, consent: true, model: model.value });
      if (closed) return;
      $("#aiPreviewSection").hidden=true;
      status.textContent = result.cached ? "已复用相同页面、遮盖和模型的本地结果，本次没有联网计费。" : `AI 已返回；输入 ${result.input_tokens}、输出 ${result.output_tokens} token。课程尚未修改。`;
      renderResults(result);
    } catch (error) {
      if (!closed) status.textContent=String(error instanceof Error ? error.message : error)+" 关闭后重新预览可再次请求。";
    } finally { busy=false; }
  });

  function renderResults(result: any) {
    const host = $("#aiResults"); host.hidden=false;
    const candidates: Candidate[] = result.courses;
    const matched=candidates.filter(c=>c.state==='matched').length;
    const visible=candidates.filter(c=>c.state!=='matched');
    const labels: Record<string,string> = { missing:"疑似漏课", uncertain:"依据不足", excluded:"校外课程，按规则排除" };
    host.innerHTML=`<p>本页覆盖：${escape(result.coverage)}</p><p>${matched} 条记录的占用与本地一致。一致不代表已经人工核验，也不代表整份课表完整。</p>
      ${result.warnings.length ? `<ul class="aiWarnings">${result.warnings.map((w:string)=>`<li>${escape(w)}</li>`).join("")}</ul>` : ""}
      <label>补录前填写原文核对说明<textarea id="aiReason" rows="2" maxlength="900" placeholder="例如：已核对原页周次、星期、节次和课程格"></textarea></label>
      <div class="aiCandidates">${visible.length ? visible.map(c=>`<article class="aiCandidate"><strong>${escape(labels[c.state])} · ${escape(c.course)}</strong>
        <p>第 ${c.week || "未知"} 周 ${escape(c.weekday)} · 第 ${c.periods.join("、") || "未知"} 节 ${escape(c.date)}</p>
        <p>依据：${escape(c.evidence)}</p>${c.location?`<p>地点：${escape(c.location)}</p>`:""}${c.uncertainty?`<p class="aiWarnings">${escape(c.uncertainty)}</p>`:""}
        ${c.state==='missing'?`<button type="button" data-adopt="${escape(c.id)}">已对照原文，补录第 ${c.missing_periods.join("、")} 节并重算</button>`:""}</article>`).join("") : '<p>本页没有可补录的差异建议。请继续核对其他页面和现有问题。</p>'}</div>
      ${result.local_only.length?`<details><summary>本地有记录但 AI 未确认的占用（${result.local_only.length}）</summary><p>AI 也可能漏读。请回到原文核对；这些记录不会被自动删除。</p><ul>${result.local_only.map((c:any)=>`<li>${escape(c.course)} · 第 ${c.week} 周${escape(c.weekday)} · 第 ${c.periods.join("、")} 节</li>`).join("")}</ul></details>`:""}
      <p class="aiFinePrint">补录会保留 AI 观察编号、模型、原页依据和人工核对原因，可在修正历史中撤销。缺页、身份冲突和未覆盖日期仍受导出门禁限制。</p><p id="aiAdoptStatus" role="status" aria-live="polite"></p>`;
    let adopting=false;
    host.addEventListener("click", async event => {
      const button=(event.target as HTMLElement).closest<HTMLButtonElement>("[data-adopt]");
      if (!button || button.disabled || adopting) return;
      const note=$<HTMLTextAreaElement>("#aiReason").value.trim();
      const out=$("#aiAdoptStatus");
      if (!note) { out.textContent="请先填写原文核对说明。"; return; }
      const current=context().review;
      if (current.job_id!==initial.review.job_id) { out.textContent="当前任务已变化，请重新打开 AI 核对。"; return; }
      adopting=true; button.disabled=true; out.textContent="正在保存补录、重算空闲并记录来源…";
      try {
        await request("ai.adopt", { job_id:initial.review.job_id, observation_id:result.observation_id,
          candidate_id:button.dataset.adopt, confirmed:true, reason:note, revision:current.revision, operation_id:crypto.randomUUID() });
        if (!closed) { button.textContent="已补录并重算"; status.textContent="已根据人工核对补录课程并重算；本页其他差异仍需处理。"; out.textContent="已保存。请继续处理其他差异和原有问题。"; }
      } catch (error) { if (!closed) { button.disabled=false; out.textContent=String(error instanceof Error ? error.message : error); } }
      finally { adopting=false; }
    });
  }
}
