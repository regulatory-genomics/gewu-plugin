import { expect, it } from "vitest";
import { ControlHub } from "../src/control";

it("routes commands only to the explicit live view and rejects detached late results", async () => {
  const hub = new ControlHub();
  expect(() => hub.key("missing")).toThrow();
  const key = hub.attach("view-a", 1);
  const stream = hub.connect("view-a", key);
  const reader = stream.getReader();
  const work = hub.call(
    "view-a",
    "studio_set_text",
    { handle: "title", text: "Edited" },
    new AbortController().signal,
  );
  const chunk = await reader.read();
  const command = JSON.parse(new TextDecoder().decode(chunk.value).slice("data: ".length));
  expect(command.tool).toBe("studio_set_text");
  hub.result("view-a", key, command.id, { ok: true, value: { saved: true } });
  await expect(work).resolves.toMatchObject({ ok: true });
  hub.detach("view-a");
  expect(() => hub.result("view-a", key, command.id, { ok: true })).toThrow();
  await expect(
    hub.call("view-a", "studio_seek", { time: 2 }, new AbortController().signal),
  ).rejects.toMatchObject({ code: "VIEW_NOT_ATTACHED" });
  await reader.cancel();
});
