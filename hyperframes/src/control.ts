import { randomUUID } from "node:crypto";
import { PluginError } from "./stdio.js";

interface View {
  key: string;
  epoch: number;
  stream?: ReadableStreamDefaultController<Uint8Array>;
}
interface Pending {
  view: string;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
  finish: () => void;
}
export class ControlHub {
  private views = new Map<string, View>();
  private pending = new Map<string, Pending>();
  attach(id: string, epoch: number) {
    this.detach(id);
    const key = randomUUID();
    this.views.set(id, { key, epoch });
    return key;
  }
  key(id: string) {
    const view = this.views.get(id);
    if (!view) throw new PluginError("VIEW_NOT_ATTACHED", "Open this project in Preview first");
    return view.key;
  }
  private view(id: string, key: string) {
    const view = this.views.get(id);
    if (!view || view.key !== key) throw new PluginError("VIEW_NOT_ATTACHED", "View expired");
    return view;
  }
  connect(id: string, key: string) {
    const view = this.view(id, key);
    view.stream?.close();
    return new ReadableStream<Uint8Array>({
      start: (controller) => {
        view.stream = controller;
      },
      cancel: () => {
        if (this.views.get(id) === view) this.detach(id);
      },
    });
  }
  result(id: string, key: string, callId: string, value: unknown) {
    this.view(id, key);
    const pending = this.pending.get(callId);
    if (!pending || pending.view !== id)
      throw new PluginError("STALE_RESULT", "Command is no longer pending");
    pending.finish();
    pending.resolve(value);
  }
  async call(
    id: string | undefined,
    tool: string,
    args: Record<string, unknown>,
    signal: AbortSignal,
  ): Promise<unknown> {
    if (!id)
      throw new PluginError(
        "VIEW_NOT_ATTACHED",
        "Focus this project's Preview before calling a Studio tool",
      );
    const view = this.views.get(id);
    if (!view?.stream) throw new PluginError("VIEW_NOT_ATTACHED", "Studio has not connected");
    if (signal.aborted) throw new PluginError("CANCELLED", "Cancelled");
    const requestId = randomUUID();
    return new Promise((resolve, reject) => {
      const finish = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", cancel);
        this.pending.delete(requestId);
      };
      const cancel = () => {
        view.stream?.enqueue(
          new TextEncoder().encode(
            `data: ${JSON.stringify({ type: "cancel", id: requestId })}\n\n`,
          ),
        );
        finish();
        reject(
          new PluginError(
            "CANCELLED",
            "Studio operation cancelled; inspect its saved receipt before retrying",
          ),
        );
      };
      const timer = setTimeout(() => {
        finish();
        reject(
          new PluginError(
            "OUTCOME_UNKNOWN",
            "Studio response timed out; inspect source before retrying",
          ),
        );
      }, 30000);
      signal.addEventListener("abort", cancel, { once: true });
      this.pending.set(requestId, { view: id, resolve, reject, finish });
      view.stream?.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({ id: requestId, tool, args, viewId: id, viewEpoch: view.epoch })}\n\n`,
        ),
      );
    });
  }
  detach(id: string) {
    const view = this.views.get(id);
    this.views.delete(id);
    try {
      view?.stream?.close();
    } catch {}
    for (const pending of [...this.pending.values()])
      if (pending.view === id) {
        pending.finish();
        pending.reject(
          new PluginError(
            "VIEW_NOT_ATTACHED",
            "Studio disconnected; inspect any pending write before retrying",
          ),
        );
      }
  }
  close() {
    for (const id of [...this.views.keys()]) this.detach(id);
  }
}
