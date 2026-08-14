import { Child, Command } from "@tauri-apps/plugin-shell";
import type { ProgressEvent } from "./types";

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: number;
};

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
    if (!this.child) throw new Error("本地识别功能尚未启动。");
    const id = crypto.randomUUID();
    const promise = new Promise<T>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("处理等待超时，请重试。"));
      }, 10 * 60 * 1000);
      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timer
      });
    });
    await this.child.write(`${JSON.stringify({ id, command, payload })}\n`);
    return promise;
  }

  async forceRestart() {
    this.intentionalStop = true;
    if (this.child) {
      await this.child.kill().catch(() => undefined);
    }
    this.child = null;
    this.command = null;
    this.intentionalStop = false;
    this.restartCount = 0;
    await this.start();
  }

  private async spawn() {
    this.intentionalStop = false;
    const command = Command.sidecar("binaries/konggu-worker", ["--serve"], { encoding: "utf-8" });
    this.command = command;
    command.stdout.on("data", (chunk) => this.consume(chunk));
    command.stderr.on("data", (chunk) => {
      if (chunk.trim()) console.warn("konggu-worker:", chunk);
    });
    command.on("close", () => this.onClosed());
    command.on("error", (error) => this.rejectAll(new Error(String(error))));
    this.child = await command.spawn();
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

  private onClosed() {
    this.child = null;
    this.command = null;
    this.rejectAll(new Error("本地识别功能意外停止，正在处理的课表已中断。"));
    if (!this.intentionalStop && this.restartCount < 1) {
      this.restartCount += 1;
      void this.start();
    }
  }

  private rejectAll(error: Error) {
    for (const pending of this.pending.values()) {
      window.clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
