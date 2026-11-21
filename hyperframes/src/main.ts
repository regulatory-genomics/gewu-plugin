import { resolve } from "node:path";
import { Adapter } from "./adapter.js";
import { serveStdio } from "./stdio.js";

const adapter = new Adapter(
  process.env.GEWU_PLUGIN_ROOT ?? resolve(import.meta.dirname, "../.."),
  (id, event) => process.stdout.write(JSON.stringify({ id, type: "event", event }) + "\n"),
);
process.on("SIGTERM", () => void adapter.close().finally(() => process.exit(0)));
process.on("SIGINT", () => void adapter.close().finally(() => process.exit(0)));
await serveStdio(process.stdin, process.stdout, (frame) => adapter.handle(frame));
await adapter.close();
