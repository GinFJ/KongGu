import { Child, Command } from "@tauri-apps/plugin-shell";
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

  async request<T>(command: string, payload: Record<string, unknown> = {}): Promise<T> {
    if (!ALLOWED_COMMANDS.has(command)) throw new Error("不支持的本地操作。");
    await this.start();
    const child = this.child;
    if (!child) throw new Error("本地识别功能尚未启动。");
    const id = crypto.randomUUID();
    let rejectRequest: (reason: Error) => void = () => undefined;
    const promise = new Promise<T>((resolve, reject) => {
      rejectRequest = reject;
      const timer = window.setTimeout(() => {
        const timedOut = this.pending.get(id);
        if (!timedOut) return;
        this.pending.delete(id);
        timedOut.reject(new Error("处理等待超时，请重试。"));
      }, REQUEST_TIMEOUT_MS);
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

  private async spawn() {
    this.intentionalStop = false;
    const command = Command.sidecar("binaries/konggu-worker", ["--serve"], { encoding: "utf-8" });
    command.stdout.on("data", (chunk) => {
      if (this.command === command) this.consume(chunk);
    });
    command.stderr.on("data", (chunk) => {
      if (chunk.trim() && this.command === command) console.warn("konggu-worker:", chunk);
    });
    command.on("close", () => this.onClosed(command));
    command.on("error", (error) => this.onSpawnError(command, error));
    this.command = command;
    try {
      this.child = await command.spawn();
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
          this.listeners.forEach((listener) => listener(message as ProgressEvent));
          continue;
        }
        const pending = this.pending.get(String(message.id || ""));
        if (!pending) continue;
        window.clearTimeout(pending.timer);
        this.pending.delete(String(message.id));
        if (message.ok) pending.resolve(message.data);
        else pending.reject(new Error(message.error || "本地识别功能执行失败。"));
      } catch (error) {
        console.warn("无法解析 sidecar 输出", line, error);
      }
    }
  }

  private onClosed(command: Command<string>) {
    // 只接受当前进程的 close；被替换掉的旧进程迟到 close 不得清掉新会话或误杀新请求。
    if (this.command !== command) return;
    this.child = null;
    this.command = null;
    // 旧进程写了一半的 NDJSON 行必须丢弃，否则会拼到新进程的首条响应上导致解析失败。
    this.buffer = "";
    this.rejectAll(new Error("本地识别功能意外停止，正在处理的课表已中断。"));
    if (!this.intentionalStop && this.restartCount < 1) {
      this.restartCount += 1;
      this.start().catch((error) => console.warn("konggu-worker 自动重启失败:", error));
    }
  }

  private onSpawnError(command: Command<string>, error: unknown) {
    if (this.command !== command) return;
    this.child = null;
    this.command = null;
    this.buffer = "";
    this.rejectAll(error instanceof Error ? error : new Error(String(error)));
  }

  private rejectAll(error: Error) {
    for (const pending of this.pending.values()) {
      window.clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
