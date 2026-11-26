import type { SidecarClient } from "./sidecarClient";
import "./localHistory.css";

type Batch = {
  job_id: string; updated_at: string; file_count: number;
  file_names: string[]; start_date?: string; end_date?: string;
};
export type LocalHistory = {batches: Batch[]; draft: boolean};
type StorageUsage = {cache_bytes: number; cache_files: number; history_bytes: number; batch_count: number};
type Actions = {
  openBatch(jobId: string): Promise<void>;
  openDraft(): Promise<void>;
  newBatch(): Promise<void>;
  clearAll(): Promise<void>;
};

function size(bytes: number) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, bytes), unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${Number(value.toFixed(unit ? 1 : 0))} ${units[unit]}`;
}

export function showLocalHistory(client: SidecarClient, actions: Actions, initial?: LocalHistory) {
  if (document.querySelector("#localHistory")) return;
  const dialog = document.createElement("dialog");
  dialog.id = "localHistory";
  dialog.className = "localHistory";
  dialog.setAttribute("aria-labelledby", "localHistoryTitle");
  dialog.innerHTML = `<header><h2 id="localHistoryTitle">本机课表记录</h2><button type="button" id="historyClose">关闭</button></header>
    <div class="historyActions"><button type="button" class="primary" id="historyNew">新建合表</button>
      <button type="button" id="historyDraft" hidden>继续上次草稿</button></div>
    <p class="historyEmpty" id="historyEmpty">正在读取记录…</p>
    <ul class="historyList" aria-label="已生成的课表"></ul>
    <section class="historyStorage"><h3>本机存储</h3>
      <div><span>识别缓存 <strong id="historyCacheSize">读取中</strong></span><button id="historyClearCache" type="button">清除识别缓存</button></div>
      <div><span>记录数据库 <strong id="historyDbSize">读取中</strong></span><button id="historyClearAll" type="button">清除全部本机记录</button></div>
    </section>
    <footer><span id="historyNotice" role="status" aria-live="polite"></span><button id="historyRefresh" type="button">刷新</button></footer>`;
  document.body.append(dialog);
  const node = <T extends HTMLElement>(selector: string) => dialog.querySelector<T>(selector)!;
  const button = (selector: string) => node<HTMLButtonElement>(selector);
  let busy = false;
  let history = initial;
  let usage: StorageUsage | null = null;

  function lock(value: boolean) {
    busy = value;
    dialog.setAttribute("aria-busy", String(value));
    for (const item of dialog.querySelectorAll<HTMLButtonElement>("button")) item.disabled = value;
    button("#historyNew").disabled = value || !history;
    button("#historyClearCache").disabled = value || !usage || usage.cache_files === 0;
  }
  async function run(action: () => Promise<void>) {
    if (busy) return;
    lock(true);
    node("#historyNotice").textContent = "正在处理…";
    try {
      await action();
      if (node("#historyNotice").textContent === "正在处理…") node("#historyNotice").textContent = "";
    } catch (error) {
      node("#historyNotice").textContent = error instanceof Error ? error.message : "读取失败，请刷新后重试。";
    } finally { lock(false); }
  }
  function replaceDraft() {
    return !history?.draft || window.confirm("继续后会替换上次未生成的草稿，已生成的课表仍保留。继续吗？");
  }
  function drawHistory(next: LocalHistory) {
    history = next;
    button("#historyDraft").hidden = !next.draft;
    node("#historyEmpty").hidden = !!next.batches.length;
    node("#historyEmpty").textContent = "还没有已生成的本机课表。";
    const list = node<HTMLUListElement>(".historyList");
    list.replaceChildren();
    for (const batch of next.batches) {
      const row = document.createElement("li");
      const info = document.createElement("div");
      const title = document.createElement("strong");
      const files = document.createElement("span");
      const detail = document.createElement("small");
      title.textContent = batch.start_date && batch.end_date ? `${batch.start_date} 至 ${batch.end_date}` : "合表记录";
      files.textContent = batch.file_names.join("、") + (batch.file_count > batch.file_names.length ? " 等" : "");
      const changed = new Date(batch.updated_at);
      detail.textContent = `${batch.file_count} 份课表` + (!Number.isNaN(changed.valueOf()) ? ` · ${changed.toLocaleString("zh-CN", {hour12:false})} 更新` : "");
      const open = document.createElement("button");
      open.type = "button"; open.textContent = "打开";
      open.setAttribute("aria-label", `打开 ${title.textContent}，${files.textContent}`);
      open.onclick = () => void run(async () => {
        if (!replaceDraft()) return;
        await actions.openBatch(batch.job_id);
        dialog.close();
      });
      info.append(title, files, detail); row.append(info, open); list.append(row);
    }
  }
  async function loadUsage() {
    usage = await client.request<StorageUsage>("data.usage");
    node("#historyCacheSize").textContent = `${size(usage.cache_bytes)} · ${usage.cache_files} 个文件`;
    node("#historyDbSize").textContent = `${size(usage.history_bytes)} · ${usage.batch_count} 批`;
  }
  async function refresh() {
    drawHistory(await client.request<LocalHistory>("workspace.history"));
    await loadUsage();
  }
  button("#historyRefresh").onclick = () => void run(refresh);
  button("#historyClose").onclick = () => dialog.close();
  button("#historyNew").onclick = () => void run(async () => {
    if (!replaceDraft()) return;
    await actions.newBatch(); dialog.close();
  });
  button("#historyDraft").onclick = () => void run(async () => {
    await actions.openDraft(); dialog.close();
  });
  button("#historyClearCache").onclick = () => void run(async () => {
    await client.request("data.clear_cache");
    await loadUsage();
    node("#historyNotice").textContent = "识别缓存已清除，课表和核验进度已保留。";
  });
  button("#historyClearAll").onclick = () => void run(async () => {
    if (!window.confirm("将永久清除当前工作区在这台电脑上的所有课表记录、草稿、核验修正及识别缓存。原始 PDF 和已导出的文件会保留。确定清除吗？")) return;
    await actions.clearAll();
    await refresh();
    node("#historyNotice").textContent = "本机记录和识别缓存已清除。";
  });
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  dialog.addEventListener("close", () => dialog.remove(), {once: true});
  dialog.showModal();
  if (initial) drawHistory(initial);
  void run(initial ? loadUsage : refresh);
}
