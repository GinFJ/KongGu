import { Child, Command } from "@tauri-apps/plugin-shell";
import { invoke } from "@tauri-apps/api/core";
import type { ProgressEvent } from "./types";

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: number;
};

const REQUEST_TIMEOUT_MS = 10 * 60 * 1000;

const ALLOWED_COMMANDS = new Set([
  "resources.status", "resources.repair", "data.clear", "settings.calendar.get", "settings.calendar.save",
  "pdf.inspect", "job.start", "job.get", "job.cancel", "job.retry_failed", "review.get", "review.apply",
  "review.confirm", "exports.excel"
]);

export class SidecarClient {
  private child: Child | null = null;
  private command: Command<string> | null = null;
  private pending = new Map<string, PendingRequest>();
  private listeners = new Set<(event: ProgressEvent) => void>();
  private buffer = "";
  private starting: Promise<void> | null = null;
  private restartCount = 0;
  private intentionalStop = false;
  private answered = false;
  private startupReason = "";
  private closeError: Error | null = null;

  onEvent(listener: (event: ProgressEvent) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async start() {
    if (this.child) return;
    if (this.starting) return this.starting;
    this.starting = this.spawn();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  async request<T>(command: string, payload: Record<string, unknown> = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
    if (!ALLOWED_COMMANDS.has(command)) throw new Error("不支持的本地操作。");
    await this.start();
    const child = this.child;
    if (!child) throw this.closeError || new Error("本地识别功能尚未启动。");
    const id = crypto.randomUUID();
    let rejectRequest: (reason: Error) => void = () => undefined;
    const promise = new Promise<T>((resolve, reject) => {
      rejectRequest = reject;
      const timer = window.setTimeout(() => {
        const timedOut = this.pending.get(id);
        if (!timedOut) return;
        this.pending.delete(id);
        timedOut.reject(new Error("处理等待超时，请重试。"));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timer
      });
    });
    try {
      await child.write(`${JSON.stringify({ id, command, payload })}\n`);
    } catch (error) {
      // 写入失败时立即结束该请求；否则调用方会一直等一个永不返回的 Promise 直至超时。
      const normalized = error instanceof Error ? error : new Error(String(error));
      const pending = this.pending.get(id);
      if (pending) {
        window.clearTimeout(pending.timer);
        this.pending.delete(id);
      }
      rejectRequest(normalized);
    }
    return promise;
  }

  async forceRestart() {
    this.intentionalStop = true;
    const previous = this.child;
    // 先清空当前会话引用：旧进程迟到的 close 和输出都不能再影响重启后的新会话。
    this.child = null;
    this.command = null;
    this.buffer = "";
    this.rejectAll(new Error("本地识别功能正在重新启动，请稍后重试。"));
    if (previous) {
      await previous.kill().catch(() => undefined);
    }
    this.intentionalStop = false;
    this.restartCount = 0;
    await this.start();
  }

  async shutdown() {
    // 页面刷新/关闭时必须结束当前 worker：否则重载后的新 worker 会与旧进程
    // 争用同一个 OCR 进程槽位与 SQLite 状态。
    this.intentionalStop = true;
    const previous = this.child;
    this.child = null;
    this.command = null;
    this.buffer = "";
    this.rejectAll(new Error("本地识别功能已关闭。"));
    if (previous) {
      await previous.kill().catch(() => undefined);
    }
  }

  private async spawn() {
    this.intentionalStop = false;
    const workerTemp = await invoke<string>("prepare_worker_temp");
    if (this.intentionalStop) throw new Error("本地识别功能已关闭。");
    this.answered = false;
    this.startupReason = "";
    this.closeError = null;
    const command = Command.sidecar("binaries/konggu-worker", ["--serve"], {
      encoding: "utf-8", env: { TEMP: workerTemp, TMP: workerTemp }
    });
    command.stdout.on("data", (chunk) => {
      if (this.command === command) this.consume(chunk);
    });
    command.stderr.on("data", (chunk) => {
      if (this.command !== command || this.answered) return;
      // Bootloader errors arrive before Python can emit a fatal event. Keep only their category.
      if (/no space left|not enough space|disk (?:is )?full/i.test(chunk)) {
        this.startupReason = "临时盘空间不足，识别组件无法解包。请释放磁盘空间后重试。";
      } else if (/extraction path length exceeds/i.test(chunk)) {
        this.startupReason = "临时目录路径过长，识别组件无法解包。请使用较短的本机临时目录后重试。";
      } else if (/decompression resulted in return code/i.test(chunk)) {
        this.startupReason = "识别组件解包时解压失败。请重试，仍失败时请使用完整安装包重新安装。";
      } else if (!this.startupReason && /failed to (?:extract|uncompress)|could not create temporary directory/i.test(chunk)) {
        this.startupReason = "识别组件未能解包。请检查临时盘空间和写入权限后重试。";
      }
    });
    command.on("close", ({ code }) => this.onClosed(command, code));
    command.on("error", (error) => this.onSpawnError(command, error));
    this.command = command;
    try {
      const child = await command.spawn();
      if (this.command !== command) {
        await child.kill().catch(() => undefined);
        throw this.closeError || new Error("本地识别功能已关闭。");
      }
      this.child = child;
    } catch (error) {
      if (this.command === command) {
        this.child = null;
        this.command = null;
        this.buffer = "";
      }
      throw error;
    }
  }

  private consume(chunk: string) {
    this.buffer += chunk;
    const lines = this.buffer.split(/\r?\n/);
    this.buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const message = JSON.parse(line);
        if (message.type === "event") {
          if (message.event === "sidecar.fatal") this.startupReason = message.message;
          this.listeners.forEach((listener) => listener(message as ProgressEvent));
          continue;
        }
        const pending = this.pending.get(String(message.id || ""));
        if (!pending) continue;
        this.answered = true;
        window.clearTimeout(pending.timer);
        this.pending.delete(String(message.id));
        if (message.ok) pending.resolve(message.data);
        else pending.reject(new Error(message.error || "本地识别功能执行失败。"));
      } catch {
        console.warn("无法解析本地识别服务响应");
      }
    }
  }

  private onClosed(command: Command<string>, code: number | null) {
    // 只接受当前进程的 close；被替换掉的旧进程迟到 close 不得清掉新会话或误杀新请求。
    if (this.command !== command) return;
    this.child = null;
    this.command = null;
    // 旧进程写了一半的 NDJSON 行必须丢弃，否则会拼到新进程的首条响应上导致解析失败。
    this.buffer = "";
    const reason = this.startupReason || (this.answered
      ? "本地识别服务意外停止，请检查当前操作并重试。"
      : "本地识别服务未完成启动，请重新启动识别服务。");
    this.closeError = new Error(code === null ? reason : `${reason}（退出码 ${code}）`);
    this.rejectAll(this.closeError);
    if (this.answered && !this.intentionalStop && this.restartCount < 1) {
      this.restartCount += 1;
      this.start().catch((error) => console.warn("konggu-worker 自动重启失败:", error));
    }
  }

  private onSpawnError(command: Command<string>, error: unknown) {
    if (this.command !== command) return;
    this.child = null;
    this.command = null;
    this.buffer = "";
    this.closeError = error instanceof Error ? error : new Error(String(error));
    this.rejectAll(this.closeError);
  }

  private rejectAll(error: Error) {
    for (const pending of this.pending.values()) {
      window.clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
