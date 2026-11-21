import type { StartRenderOptions } from "@hyperframes/studio";
import type { connect } from "./bridge";
type Bridge = Awaited<ReturnType<typeof connect>>;
const field = (value: unknown, key: string): unknown =>
  value && typeof value === "object" ? Reflect.get(value, key) : undefined;

export async function exportVideo(
  bridge: Bridge,
  options: StartRenderOptions = {},
  resume?: string,
) {
  const box = document.createElement("section");
  box.setAttribute("aria-label", "GeWu render status");
  box.style.cssText =
    "position:fixed;right:20px;top:70px;max-width:480px;background:#122b30;color:#fff;padding:16px;z-index:9999;border:1px solid #41d7bd;border-radius:8px;font:14px sans-serif";
  const status = document.createElement("div");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  box.append(status);
  document.body.append(box);
  const cancel = document.createElement("button");
  cancel.textContent = "Cancel render";
  box.append(cancel);
  const save = async (id?: string) => {
    const state = (bridge.context.state ??= {});
    if (id) state.renderCallId = id;
    else delete state.renderCallId;
    await bridge.call("saveState", { state });
  };
  try {
    if (options.format && !["mp4", "webm"].includes(options.format))
      throw Error("This plugin release exports MP4 and WebM. Choose one of those formats.");
    if (options.gpu || options.variables || (options.resolution && options.resolution !== "auto"))
      throw Error(
        "This plugin currently exports the authored resolution with software encoding; use the matching export settings.",
      );
    const started = resume
      ? { callId: resume }
      : await bridge.call("callTool", {
          tool: "render",
          args: {
            format: options.format ?? "mp4",
            quality:
              options.quality === "high"
                ? "delivery"
                : options.quality === "draft"
                  ? "draft"
                  : "looks",
            fps: options.fps ?? 30,
            ...(options.composition ? { compositionPath: options.composition } : {}),
          },
        });
    const id = field(started, "callId");
    if (typeof id !== "string") throw Error("GeWu did not start a render call");
    await save(id);
    cancel.onclick = () => {
      cancel.disabled = true;
      status.textContent = "Stopping worker…";
      void bridge.call("cancelCall", { callId: id });
    };
    while (true) {
      const current = await bridge.call("getCall", { callId: id }),
        result = field(current, "result");
      if (result) {
        cancel.remove();
        await save();
        if (field(result, "ok") !== true) {
          const error = field(result, "error");
          throw Error(String(field(error, "message") ?? "Render failed"));
        }
        status.textContent = "Video ready";
        const resources = field(result, "resources"),
          resource = Array.isArray(resources)
            ? resources.find((r) => field(r, "type") === "hyperframes.video")
            : undefined;
        if (resource) {
          const open = document.createElement("button");
          open.textContent = "Open video";
          open.onclick = () => void bridge.call("openResource", { resource });
          box.append(open);
          const download = document.createElement("button");
          download.textContent = "Download video";
          download.onclick = () => void bridge.call("downloadResource", { resource });
          box.append(download);
        }
        break;
      }
      const message = field(current, "progress");
      status.textContent =
        typeof message === "string"
          ? message.replace(/\x1b\[[0-9;]*m/g, "").slice(-240)
          : "Rendering frozen project revision…";
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  } catch (error) {
    cancel.remove();
    status.textContent = error instanceof Error ? error.message : String(error);
  }
  const close = document.createElement("button");
  close.textContent = "Dismiss render status";
  close.onclick = () => box.remove();
  box.append(close);
}
