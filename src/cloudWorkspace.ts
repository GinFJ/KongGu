import type { SidecarClient } from "./sidecarClient";
import "./cloudWorkspace.css";

type Batch = {id: string; title: string; revision: number; updated_at: string};
type Binding = Batch & {dirty: boolean};
type Account = {username: string; account_id: string; endpoint: string; batch?: Binding | null};
type Current = {job: string; revision: string; memberCount: number; hasFiles: boolean; busy: boolean};
type Workbench = {current(): Current; reopen(): Promise<void>; drain(): Promise<void>; render(): void};

const emptyAccount: Account = {username: "", account_id: "", endpoint: ""};
const endpointKey = "konggu-cloud-endpoint";
function previousEndpoint() {
  try { return localStorage.getItem(endpointKey) || import.meta.env.VITE_KONGGU_CLOUD_URL || ""; }
  catch { return import.meta.env.VITE_KONGGU_CLOUD_URL || ""; }
}

function formatTime(value: string) {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? "" : time.toLocaleString("zh-CN", {hour12: false});
}

export class CloudWorkspace {
  private account = emptyAccount;
  private binding: Binding | null = null;
  private boundJob = "";
  private savedRevision = "";
  private dialog: HTMLDialogElement;
  private busy = false;
  private lost = false;
  private batches: Batch[] | null = null;
  private copyAttempt: {key: string; id: string} | null = null;

  constructor(private client: SidecarClient, private work: Workbench) {
    this.dialog = document.createElement("dialog");
    this.dialog.className = "cloudWorkspace";
    this.dialog.id = "cloudWorkspace";
    this.dialog.setAttribute("aria-labelledby", "cloudTitle");
    document.body.append(this.dialog);
    this.dialog.addEventListener("cancel", event => { if (this.busy || this.lost) event.preventDefault(); });
    this.dialog.addEventListener("close", () => {
      if (!this.dialog.open) { this.dialog.replaceChildren(); this.batches = null; }
    });
  }

  private node<T extends HTMLElement>(selector: string): T { return this.dialog.querySelector<T>(selector)!; }
  private get currentBinding() { return this.boundJob === this.work.current().job ? this.binding : null; }
  private get dirty() {
    const active = this.currentBinding;
    return !active || active.dirty || this.savedRevision !== this.work.current().revision;
  }
  private bind(batch: Binding | null) {
    this.binding = batch;
    const current = this.work.current();
    this.boundJob = current.job;
    this.savedRevision = current.revision;
  }

  updateEntry(button: HTMLButtonElement) {
    const current = this.work.current();
    button.disabled = current.busy;
    const primary = button.querySelector("strong")!;
    const secondary = button.querySelector("small")!;
    primary.textContent = this.account.username ? `云端批次 · ${this.account.username}` : "登录 / 注册";
    secondary.textContent = this.account.username
      ? current.job ? (this.dirty ? "本批有待保存的数据" : "本批已保存到云端") : "打开我的云端批次"
      : "本机模式";
  }

  async open() {
    if (this.busy || this.lost || this.work.current().busy || this.dialog.open) return;
    if (this.account.account_id) this.showAccount();
    else this.showAuth();
    this.dialog.showModal();
    if (this.account.account_id) await this.run(() => this.refresh());
  }

  workerLost() {
    if (this.lost) return;
    this.lost = true;
    this.account = emptyAccount;
    this.bind(null);
    this.batches = null;
    document.querySelectorAll<HTMLDialogElement>("dialog[open]").forEach(d => { if (d !== this.dialog) d.close(); });
    document.getElementById("app")!.hidden = true;
    this.dialog.innerHTML = `<h2 id="cloudTitle">账号连接已中断</h2><p>本地服务已重启。请重新打开空谷，再登录账号继续制作。</p><button class="primary">重新打开空谷</button>`;
    this.node("button").onclick = () => location.reload();
    if (!this.dialog.open) this.dialog.showModal();
  }

  private shell(title: string, body: string) {
    this.dialog.innerHTML = `<header><h2 id="cloudTitle">${title}</h2><button type="button" data-cloud-close>关闭</button></header>
      ${body}<p id="cloudMessage" role="status" aria-live="polite"></p>`;
    this.node("[data-cloud-close]").onclick = () => { if (!this.busy) this.dialog.close(); };
  }

  private showAuth() {
    this.shell("登录空谷", `<form id="cloudAuth" autocomplete="off">
      <label>服务地址<input name="endpoint" id="cloudEndpoint" type="url" required placeholder="填写组织提供的云服务地址" /></label>
      <label>用户名<input id="cloudUsername" name="username" autocomplete="username" required maxlength="150" /></label>
      <label>密码<input id="cloudPassword" name="password" type="password" autocomplete="current-password" required maxlength="256" /></label>
      <p>登录后可打开自己保存的课表批次。首次使用可用至少 10 位密码注册。</p>
      <footer><button type="submit" value="login" class="primary">登录</button><button type="submit" value="register">注册并登录</button></footer></form>`);
    this.node<HTMLInputElement>("#cloudEndpoint").value = previousEndpoint();
    this.node<HTMLFormElement>("#cloudAuth").onsubmit = event => {
      event.preventDefault();
      const method = (event.submitter as HTMLButtonElement | null)?.value === "register" ? "register" : "login";
      void this.run(async () => {
        const endpoint = this.node<HTMLInputElement>("#cloudEndpoint").value.trim();
        const username = this.node<HTMLInputElement>("#cloudUsername").value.trim();
        const passwordInput = this.node<HTMLInputElement>("#cloudPassword");
        const password = passwordInput.value;
        if (method === "register" && password.length < 10) throw new Error("注册密码至少需要 10 位。");
        await this.work.drain();
        const account = await this.client.request<Account>(`cloud.${method}`, {endpoint, username, password});
        passwordInput.value = "";
        if (this.lost) return;
        this.account = {...account, endpoint};
        this.copyAttempt = null;
        this.bind(null);
        try { localStorage.setItem(endpointKey, endpoint); } catch { /* Optional address preference. */ }
        await this.work.reopen();
        this.showAccount();
        await this.refresh();
      });
    };
  }

  private showAccount() {
    this.shell("我的云端批次", `<p id="cloudIdentity"></p>
      <section class="cloudSection"><h3>当前批次</h3><p id="cloudCurrent"></p>
      <label>批次名称<input id="cloudBatchTitle" maxlength="100" placeholder="例如：秋季学期办公室课表" /></label>
      <p class="cloudScope">保存内容包括成员、课程、空课结果和核验修正。原始 PDF 和本机路径留在这台电脑。</p>
      <div class="cloudActions"><button id="cloudSave" class="primary">保存当前批次到云端</button><button id="cloudSaveAs" hidden>另存为新批次</button></div></section>
      <section class="cloudSection"><div class="cloudListHeading"><h3>已保存的批次</h3><button id="cloudRefresh">刷新</button></div>
      <ul id="cloudBatchList"></ul></section>
      <details id="cloudGuest"><summary>从本机模式转入已有批次</summary>
        <p>将这台电脑在本机模式下最近一次生成的批次复制到当前账号。</p>
        <label>转入后的批次名称<input id="cloudGuestTitle" maxlength="100" /></label>
        <button id="cloudCopyGuest">复制到我的云端</button></details>
      <footer><button id="cloudLogout">退出账号，返回本机模式</button></footer>`);
    this.node("#cloudIdentity").textContent = `${this.account.username} · ${this.account.endpoint}`;
    this.node<HTMLInputElement>("#cloudBatchTitle").value = this.currentBinding?.title || "";
    this.node("#cloudSave").onclick = () => void this.run(() => this.save(false));
    this.node("#cloudSaveAs").onclick = () => void this.run(() => this.save(true));
    this.node("#cloudRefresh").onclick = () => void this.run(() => this.refresh());
    this.node("#cloudCopyGuest").onclick = () => void this.run(async () => {
      const title = this.title("#cloudGuestTitle");
      if (!window.confirm(`将本机模式最近完成的一批成员课表与核验记录上传到「${this.account.username}」的云端？`)) return;
      await this.client.request<Batch>("cloud.copy_guest", {title});
      await this.refresh();
      this.message("批次已复制，可从列表打开。");
    });
    this.node("#cloudLogout").onclick = () => void this.run(async () => {
      await this.work.drain();
      await this.client.request("cloud.logout");
      if (this.lost) return;
      this.account = emptyAccount;
      this.copyAttempt = null;
      this.bind(null);
      this.batches = null;
      await this.work.reopen();
      this.dialog.close();
    });
    this.draw();
  }

  private title(selector: string) {
    const input = this.node<HTMLInputElement>(selector);
    const title = input.value.trim();
    if (!title) { input.focus(); throw new Error("请先填写批次名称。"); }
    return title;
  }

  private async save(asCopy: boolean) {
    const current = this.work.current();
    if (!current.job || current.busy) throw new Error("请先生成本批课表，再保存到云端。");
    const title = this.title("#cloudBatchTitle");
    const binding = this.currentBinding;
    const key = JSON.stringify([current.job, current.revision, title]);
    if (asCopy && this.copyAttempt?.key !== key) this.copyAttempt = {key, id: crypto.randomUUID()};
    const batch = await this.client.request<Batch>("cloud.save", {
      job_id: current.job, title, ...(asCopy ? {as_copy: true, id: this.copyAttempt!.id, base_revision: 0}
        : binding ? {id: binding.id, base_revision: binding.revision} : {})
    });
    if (this.lost) return;
    this.copyAttempt = null;
    this.bind({...batch, dirty: false});
    this.batches = [batch, ...(this.batches || []).filter(item => item.id !== batch.id)];
    this.draw();
    this.message(`已保存「${title}」。在另一台电脑登录同一账号即可打开。`);
  }

  private async refresh() {
    const status = await this.client.request<Account>("cloud.status");
    if (this.lost) return;
    if (!status.account_id || status.account_id !== this.account.account_id) {
      this.workerLost();
      return;
    }
    this.bind(status.batch || null);
    const input = this.node<HTMLInputElement>("#cloudBatchTitle");
    if (!input.value && this.currentBinding) input.value = this.currentBinding.title;
    const {batches} = await this.client.request<{batches: Batch[]}>("cloud.list");
    if (this.lost) return;
    this.batches = batches;
    this.draw();
  }

  private async openBatch(batch: Batch) {
    const current = this.work.current();
    if (current.hasFiles && (!current.job || this.dirty) &&
        !window.confirm("当前工作区还有未保存到云端的数据。要切换到所选云端批次吗？")) return;
    await this.work.drain();
    const opened = await this.client.request<Batch & {job_id: string}>("cloud.open", {id: batch.id});
    if (this.lost) return;
    this.bind(null);
    await this.work.reopen();
    if (this.lost) return;
    if (this.work.current().job !== opened.job_id) throw new Error("批次已下载，但工作区未恢复。请关闭窗口查看本机提示。");
    this.bind({...batch, ...opened, dirty: false});
    this.dialog.close();
  }

  private draw() {
    const current = this.work.current();
    const binding = this.currentBinding;
    this.node("#cloudCurrent").textContent = !current.job ? "生成一批课表后，即可保存到此账号。"
      : binding ? `${binding.title} · 最近保存于 ${formatTime(binding.updated_at)}${this.dirty ? " · 有未同步修改" : " · 已同步"}`
      : `本批 ${current.memberCount} 位成员，尚未保存到云端。`;
    this.node<HTMLButtonElement>("#cloudSave").disabled = !current.job;
    this.node<HTMLButtonElement>("#cloudSaveAs").hidden = !binding;
    const list = this.node<HTMLUListElement>("#cloudBatchList");
    list.replaceChildren();
    if (!this.batches?.length) {
      const li = document.createElement("li");
      li.textContent = this.batches ? "这个账号还没有云端批次。" : "点刷新可读取账号中的云端批次。";
      list.append(li);
    }
    for (const batch of this.batches || []) {
      const row = document.createElement("li");
      const meta = document.createElement("span");
      const name = document.createElement("strong");
      const date = document.createElement("small");
      name.textContent = batch.title;
      date.textContent = formatTime(batch.updated_at);
      meta.append(name, date);
      const button = document.createElement("button");
      button.textContent = "打开";
      button.setAttribute("aria-label", `打开${batch.title}`);
      button.onclick = () => void this.run(() => this.openBatch(batch));
      row.append(meta, button);
      list.append(row);
    }
  }

  private message(text: string) { const node = this.dialog.querySelector("#cloudMessage"); if (node) node.textContent = text; }
  private async run(operation: () => Promise<void>) {
    if (this.busy || this.lost) return;
    this.busy = true;
    this.dialog.setAttribute("aria-busy", "true");
    this.message("正在处理…");
    try { await operation(); if (this.dialog.querySelector("#cloudMessage")?.textContent === "正在处理…") this.message(""); }
    catch (error) {
      if (!this.lost) {
        const detail = error instanceof Error ? error.message : "云端暂时无法连接，请重试。";
        this.message(detail.includes("云端已有更新")
          ? "另一台电脑已更新本批。可另存为新批次保留当前改动，或打开列表中的最新版本。"
          : detail.includes("登录已过期") ? "登录已过期。退出后重新登录同一账号，即可继续同步本机修改。" : detail);
      }
    }
    finally {
      this.busy = false;
      this.dialog.removeAttribute("aria-busy");
      this.work.render();
    }
  }
}
