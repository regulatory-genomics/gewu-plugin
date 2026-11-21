import { spawn, type ChildProcess } from "node:child_process";
import { PluginError } from "./stdio.js";

/**
 * Adapter-owned worker processes.
 *
 * Workers run detached so each one leads its own process group: Chrome and FFmpeg
 * descendants can then be stopped together with one signal. GeWu's optional
 * `process.started` barrier cannot be used here — it requires a new process group
 * inside the adapter's session, which Node cannot create (only a new session).
 * Group ownership therefore stays here, and the worker self-terminates when the
 * adapter's stdin closes, so an adapter crash cannot orphan it.
 */
export class JobRunner {
  private running = new Map<ChildProcess, () => void>();
  constructor(
    readonly worker: string,
    readonly node: string,
    readonly progress: (line: string) => void = () => {},
  ) {}
  async run(
    file: string,
    args: string[],
    cwd: string,
    signal: AbortSignal,
    timeoutMs = 600000,
  ): Promise<{ stdout: string; stderr: string; code: number }> {
    if (signal.aborted) throw new PluginError("CANCELLED", "Cancelled before startup");
    const payload = Buffer.from(JSON.stringify({ file, args, cwd })).toString("base64");
    const child = spawn(this.node, [this.worker, payload], {
      cwd,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, HYPERFRAMES_NO_TELEMETRY: "1", DO_NOT_TRACK: "1" },
    });
    let stdout = "",
      stderr = "",
      overflow = false,
      stopped = false,
      timedOut = false;
    let escalate: NodeJS.Timeout | undefined;
    const stop = () => {
      stopped = true;
      try {
        if (child.pid) process.kill(-child.pid, "SIGTERM");
      } catch {}
      escalate = setTimeout(() => {
        try {
          if (child.pid) process.kill(-child.pid, "SIGKILL");
        } catch {}
      }, 5000);
    };
    this.running.set(child, stop);
    const exit = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>(
      (resolve, reject) => {
        child.once("error", reject);
        child.once("close", (code, signal) => resolve({ code, signal }));
      },
    );
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      if (Buffer.byteLength(stdout) > 4 * 1024 * 1024) {
        overflow = true;
        stop();
      }
      this.progress(chunk.toString().slice(-512));
    });
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderr = (stderr + text).slice(-64000);
      this.progress(text.slice(-512));
    });
    const timer = setTimeout(() => {
      timedOut = true;
      stop();
    }, timeoutMs);
    signal.addEventListener("abort", stop, { once: true });
    try {
      if (!child.pid) throw new PluginError("START_FAILED", "Worker has no pid");
      const result = await exit;
      if (overflow) throw new PluginError("OUTPUT_TOO_LARGE", "Worker output exceeded 4 MiB");
      if (timedOut) throw new PluginError("TIMEOUT", "Video operation timed out", true);
      if (stopped || signal.aborted)
        throw new PluginError("CANCELLED", "Worker stopped and reaped");
      return { stdout, stderr, code: result.code ?? 1 };
    } catch (error) {
      stop();
      await exit.catch(() => {});
      throw error;
    } finally {
      clearTimeout(timer);
      clearTimeout(escalate);
      signal.removeEventListener("abort", stop);
      this.running.delete(child);
    }
  }
  async close() {
    const children = [...this.running];
    for (const [, stop] of children) stop();
    await Promise.all(
      children.map(
        ([child]) =>
          new Promise<void>((resolve) => {
            if (child.exitCode !== null || child.signalCode !== null) resolve();
            else child.once("close", () => resolve());
          }),
      ),
    );
  }
}
