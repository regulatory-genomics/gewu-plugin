import { expect, it } from "vitest";
import { PassThrough } from "node:stream";
import { serveStdio } from "../src/stdio";

it("reads cancellation while another call runs and never mixes logs into protocol stdout", async () => {
  const input = new PassThrough(),
    output = new PassThrough();
  let text = "";
  output.on("data", (b) => (text += b));
  let finish: () => void = () => {};
  const done = serveStdio(input, output, async (frame) => {
    if (frame.op === "call") {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return {
        ok: false,
        error: { code: "CANCELLED", message: "Stopped", retryable: false },
        resources: [],
      };
    }
    if (frame.op === "cancel") {
      finish();
      return { state: "requested" };
    }
    return { stopped: true };
  });
  input.write(JSON.stringify({ id: "one", op: "call", params: {} }) + "\n");
  await new Promise((resolve) => setImmediate(resolve));
  input.write(JSON.stringify({ id: "two", op: "cancel", params: { callId: "one" } }) + "\n");
  input.end();
  await done;
  const replies = text
    .trim()
    .split("\n")
    .map((s) => JSON.parse(s));
  expect(replies).toHaveLength(2);
  expect(replies.find((r) => r.id === "one").result.error.code).toBe("CANCELLED");
  expect(replies.find((r) => r.id === "two").result.state).toBe("requested");
});

it("rejects duplicate pending request identities", async () => {
  const input = new PassThrough(),
    output = new PassThrough();
  let text = "";
  output.on("data", (b) => (text += b));
  let finish: () => void = () => {};
  const done = serveStdio(input, output, async () => {
    await new Promise<void>((r) => (finish = r));
    return { stopped: true };
  });
  input.write('{"id":"same","op":"call","params":{}}\n');
  await new Promise((resolve) => setImmediate(resolve));
  input.write('{"id":"same","op":"call","params":{}}\n');
  input.end();
  finish();
  await done;
  expect(text).toContain('"code":"INVALID_REQUEST"');
});
