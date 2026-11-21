interface Binding {
  viewId: string;
  nonce: string;
  parentOrigin: string;
}
export interface ViewContext {
  viewId: string;
  viewEpoch: number;
  binding: { viewKey: string; projectId: string };
  state?: { currentTime?: number; selection?: string[]; renderCallId?: string };
}
export async function connect() {
  const binding: Binding = JSON.parse(sessionStorage.getItem("gewu.app.binding") ?? "null");
  if (!binding?.viewId) throw Error("Open this Studio from GeWu Preview");
  let port: MessagePort;
  let id = 0;
  const pending = new Map<
    number,
    { resolve: (result: unknown) => void; reject: (error: Error) => void }
  >();
  const context = await new Promise<ViewContext>((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("GeWu view handshake timed out")), 15000);
    const ready = (event: MessageEvent) => {
      if (
        event.source !== parent ||
        event.origin !== binding.parentOrigin ||
        event.data?.type !== "gewu.view.bound" ||
        event.ports.length !== 1
      )
        return;
      const activePort = event.ports[0];
      if (!activePort) return;
      window.removeEventListener("message", ready);
      clearTimeout(timer);
      port = activePort;
      port.onmessage = ({ data }) => {
        const p = pending.get(data.id);
        if (!p) return;
        pending.delete(data.id);
        data.error ? p.reject(Error(data.error)) : p.resolve(data.result);
      };
      resolve(event.data.context);
    };
    window.addEventListener("message", ready);
    parent.postMessage(
      { type: "gewu.view.ready", viewId: binding.viewId, nonce: binding.nonce },
      binding.parentOrigin,
    );
  });
  const call = (method: string, params: Record<string, unknown> = {}) =>
    new Promise<unknown>((resolve, reject) => {
      const key = ++id;
      pending.set(key, { resolve, reject });
      port.postMessage({ id: key, method, params, viewEpoch: context.viewEpoch });
      setTimeout(() => {
        if (pending.delete(key)) reject(Error(`${method} timed out`));
      }, 30000);
    });
  return { context, call };
}
