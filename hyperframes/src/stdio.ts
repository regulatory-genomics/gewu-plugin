import type { Readable, Writable } from "node:stream";

export interface Frame {
  id: string;
  op: string;
  params: Record<string, unknown>;
}
export class PluginError extends Error {
  constructor(
    public code: string,
    message: string,
    public retryable = false,
  ) {
    super(message);
  }
}
export function failure(error: unknown) {
  return {
    code: error instanceof PluginError ? error.code : "INTERNAL_ERROR",
    message: error instanceof Error ? error.message : String(error),
    retryable: error instanceof PluginError && error.retryable,
  };
}

export async function serveStdio(
  input: Readable,
  output: Writable,
  handle: (frame: Frame) => Promise<unknown>,
  control?: (frame: Record<string, unknown>) => void,
) {
  const pending = new Map<string, Promise<void>>();
  const emit = (value: unknown) => output.write(JSON.stringify(value) + "\n");
  let buffer = Buffer.alloc(0);
  const consume = (line: Buffer) => {
    let id = "invalid";
    try {
      if (line.length > 1024 * 1024)
        throw new PluginError("INVALID_REQUEST", "Protocol frame exceeds 1 MiB");
      const wire: unknown = JSON.parse(line.toString("utf8"));
      if (!wire || typeof wire !== "object" || !("id" in wire) || typeof wire.id !== "string")
        throw new PluginError("INVALID_REQUEST", "Request id is required");
      id = wire.id;
      if ("type" in wire && wire.type === "process.accepted") {
        control?.(wire);
        return;
      }
      if (
        !("op" in wire) ||
        typeof wire.op !== "string" ||
        !("params" in wire) ||
        !wire.params ||
        typeof wire.params !== "object" ||
        Array.isArray(wire.params)
      )
        throw new PluginError("INVALID_REQUEST", "Expected {id,op,params}");
      if (pending.has(id)) throw new PluginError("INVALID_REQUEST", "Duplicate pending request id");
      const frame: Frame = { id, op: wire.op, params: wire.params as Record<string, unknown> };
      const task = Promise.resolve()
        .then(() => handle(frame))
        .then(
          (result) => {
            emit({ id: frame.id, type: "result", result });
          },
          (error) => {
            emit({ id: frame.id, type: "error", error: failure(error) });
          },
        )
        .finally(() => pending.delete(frame.id));
      pending.set(id, task);
    } catch (error) {
      emit({ id, type: "error", error: failure(error) });
    }
  };
  for await (const chunk of input) {
    buffer = Buffer.concat([buffer, Buffer.from(chunk)]);
    let end: number;
    while ((end = buffer.indexOf(10)) >= 0) {
      const line = buffer.subarray(0, end);
      buffer = buffer.subarray(end + 1);
      if (line.length) consume(line);
    }
    if (buffer.length > 1024 * 1024)
      throw new PluginError("INVALID_REQUEST", "Unterminated frame exceeds 1 MiB");
  }
  if (buffer.length) consume(buffer);
  await Promise.allSettled([...pending.values()]);
}
