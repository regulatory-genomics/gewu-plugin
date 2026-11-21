import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

it("the packaged adapter exits after deactivate and stdin EOF, including all file watchers", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-exit-"));
  mkdirSync(join(root, "video"));
  const pkg = resolve(import.meta.dirname, "../../../dist/gewu-hyperframes");
  const child = spawn(join(pkg, "bin/hyperframes-gewu"), [], {
    cwd: root,
    env: { ...process.env, GEWU_PLUGIN_ROOT: pkg },
    stdio: ["pipe", "pipe", "ignore"],
  });
  const reader = createInterface({ input: child.stdout });
  const receive = () =>
    new Promise<unknown>((resolve) => reader.once("line", (line) => resolve(JSON.parse(line))));
  const send = (value: unknown) => child.stdin.write(JSON.stringify(value) + "\n");
  let exited = false;
  const exit = new Promise<void>((resolve) =>
    child.once("exit", () => {
      exited = true;
      resolve();
    }),
  );
  try {
    let reply = receive();
    send({
      id: "a",
      op: "activate",
      params: {
        apiVersion: "gewu.plugin/v1",
        workspaceRoot: root,
        bindingResource: { uri: "workspace:///video", type: "hyperframes.project" },
        settings: {},
      },
    });
    await reply;
    reply = receive();
    send({ id: "d", op: "deactivate", params: { reason: "test" } });
    await reply;
    child.stdin.end();
    await Promise.race([exit, new Promise((resolve) => setTimeout(resolve, 2500))]);
    expect(exited).toBe(true);
  } finally {
    reader.close();
    if (!exited) {
      child.kill("SIGKILL");
      await exit;
    }
    rmSync(root, { recursive: true, force: true });
  }
});
