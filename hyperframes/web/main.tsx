import { createRoot } from "react-dom/client";
import {
  StudioApp,
  StudioHostProvider,
  type ModelContextTool,
  usePlayerStore,
} from "@hyperframes/studio";
import "@hyperframes/studio/styles.css";
import { connect } from "./bridge";
import { exportVideo } from "./export";

async function main() {
  const bridge = await connect();
  location.hash = `project/${encodeURIComponent(bridge.context.binding.projectId)}`;
  const key = bridge.context.binding.viewKey;
  const aborts = new Map<string, AbortController>();
  const host = {
    render: (options: Parameters<typeof exportVideo>[1]) => exportVideo(bridge, options),
    registerTools: (tools: ModelContextTool[]) => {
      const controller = new AbortController();
      const run = async () => {
        const response = await fetch(
          `/__gewu/control?viewId=${encodeURIComponent(bridge.context.viewId)}`,
          { headers: { "x-gewu-view-key": key }, signal: controller.signal },
        );
        if (!response.ok || !response.body) throw Error("Studio control channel could not connect");
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let text = "";
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          text += chunk.value;
          let end: number;
          while ((end = text.indexOf("\n\n")) >= 0) {
            const line = text.slice(0, end);
            text = text.slice(end + 2);
            if (!line.startsWith("data: ")) continue;
            const command = JSON.parse(line.slice(6));
            if (command.type === "cancel") {
              aborts.get(command.id)?.abort();
              continue;
            }
            if (command.viewEpoch !== bridge.context.viewEpoch) continue;
            const abort = new AbortController();
            aborts.set(command.id, abort);
            void (async () => {
              try {
                const tool = tools.find((t) => t.name === command.tool);
                const result = tool
                  ? await tool.execute(command.args, { signal: abort.signal })
                  : { ok: false, kind: "invalid", reason: "Unknown Studio tool" };
                await fetch("/__gewu/result", {
                  method: "POST",
                  headers: { "content-type": "application/json", "x-gewu-view-key": key },
                  body: JSON.stringify({ viewId: bridge.context.viewId, id: command.id, result }),
                });
              } finally {
                aborts.delete(command.id);
              }
            })().catch((error) => console.error("Studio command failed", error));
          }
        }
      };
      void run().catch((error) => {
        if (!controller.signal.aborted) console.error("Studio control disconnected", error);
      });
      const look = tools.find((t) => t.name === "studio_look");
      let last = "",
        inFlight = false;
      const timer = setInterval(async () => {
        if (inFlight) return;
        inFlight = true;
        try {
          const state = usePlayerStore.getState();
          const ids = state.selectedElementIds;
          const selection = Array.isArray(ids) ? ids : [...ids];
          const current = {
            ...bridge.context.state,
            currentTime: Math.round(state.currentTime * 10) / 10,
            selection,
          };
          bridge.context.state = current;
          const serialized = JSON.stringify(current);
          if (serialized !== last) {
            last = serialized;
            await bridge.call("publishContext", {
              summary: JSON.stringify({
                projectId: bridge.context.binding.projectId,
                ...current,
              }).slice(0, 2048),
            });
            await bridge.call("saveState", { state: current });
          }
        } finally {
          inFlight = false;
        }
      }, 250);
      if (bridge.context.state?.currentTime)
        usePlayerStore.getState().requestSeek(bridge.context.state.currentTime);
      void look;
      return () => {
        clearInterval(timer);
        controller.abort();
        for (const abort of aborts.values()) abort.abort();
      };
    },
  };
  document.documentElement.style.height = "100%";
  document.body.style.height = "100%";
  document.body.style.margin = "0";
  const root = document.getElementById("root")!;
  root.style.cssText = "height:100%;display:flex;flex-direction:column";
  createRoot(root).render(
    <StudioHostProvider value={host}>
      <button
        style={{ position: "absolute", right: 12, bottom: 8, zIndex: 1000, fontSize: 12 }}
        onClick={() => void bridge.call("maximize")}
      >
        Maximize Studio
      </button>
      <StudioApp />
    </StudioHostProvider>,
  );
  if (bridge.context.state?.renderCallId)
    void exportVideo(bridge, {}, bridge.context.state.renderCallId);
}
void main().catch((error) => {
  document.body.textContent = error.message;
});
