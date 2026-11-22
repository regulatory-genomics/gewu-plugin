import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

export function register({ test, expect, startFixture }) {
  test("installed Hyperframes Studio opens in GeWu, plays, edits and exports the shared video", async ({
    page,
  }) => {
    test.setTimeout(600000);
    if (!process.env.GEWU_EVIDENCE) throw Error("Set GEWU_EVIDENCE");
    await mkdir(process.env.GEWU_EVIDENCE, { recursive: true });
    const installed = process.env.GEWU_INSTALLED_HOME
      ? { home: process.env.GEWU_INSTALLED_HOME, package: process.env.GEWU_INSTALLED_PACKAGE }
      : undefined;
    if (installed && !installed.package)
      throw Error("Set GEWU_INSTALLED_PACKAGE with GEWU_INSTALLED_HOME");
    const fixture = await startFixture(
      installed
        ? { installed, appOrigins: true }
        : {
            settings: {
              chromePath: process.env.GEWU_HYPERFRAMES_CHROME,
              ffmpegPath: process.env.GEWU_HYPERFRAMES_FFMPEG,
              ffprobePath: process.env.GEWU_HYPERFRAMES_FFPROBE,
            },
            appOrigins: true,
          },
    );
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const failures = [];
    page.on("response", async (r) => {
      const u = new URL(r.url());
      if (r.status() >= 400 || u.pathname.includes("/preview")) {
        failures.push(
          JSON.stringify({
            status: r.status(),
            path: u.pathname,
            query: u.search.replace(/~[\w-]+/g, "~redacted"),
            csp: r.headers()["content-security-policy"],
            origin: await r.request().headerValue("origin"),
            cookie: !!(await r.request().headerValue("cookie")),
            kind: r.request().resourceType(),
          }),
        );
      }
    });
    try {
      await cp(process.env.GEWU_HYPERFRAMES_FIXTURE, join(fixture.root, "video"), {
        recursive: true,
      });
      await page.goto(fixture.url);
      await expect(page.getByRole("textbox", { name: "Message" })).toBeEnabled();
      const reveal = page.getByRole("button", { name: "Show files" });
      if (await reveal.isVisible()) await reveal.click();
      await page.getByRole("tab", { name: "PREVIEW" }).click();
      page.once("dialog", (dialog) => dialog.accept("video"));
      await page
        .locator(".plugin-row")
        .filter({ hasText: "Hyperframes" })
        .getByRole("button", { name: "Open…", exact: true })
        .click();
      const studio = page.frameLocator('iframe[title="Video Studio"]');
      await expect(studio.locator("hyperframes-player").first()).toBeVisible({ timeout: 30000 });
      await expect
        .poll(
          () =>
            studio
              .locator("hyperframes-player")
              .first()
              .evaluate((player) => player.duration),
          { timeout: 30000 },
        )
        .toBe(12);
      const agent = async (name, args, json = true) => {
        const start = fixture.requests.length;
        const previousTools =
          fixture.requests.at(-1)?.messages.filter((m) => m.role === "tool").length ?? 0;
        await page
          .getByRole("textbox", { name: "Message" })
          .fill(`call ${name} ${JSON.stringify(args)}`);
        await page.getByRole("textbox", { name: "Message" }).press("Enter");
        await expect
          .poll(
            () =>
              fixture.requests
                .slice(start)
                .some(
                  (turn) => turn.messages.filter((m) => m.role === "tool").length > previousTools,
                ),
            { timeout: 90000 },
          )
          .toBe(true);
        const turn = fixture.requests
          .slice(start)
          .find((turn) => turn.messages.filter((m) => m.role === "tool").length > previousTools);
        const text = turn.messages.filter((m) => m.role === "tool").at(-1).content;
        await expect(
          page.getByText("The adapter returned a result.", { exact: true }).last(),
        ).toBeVisible();
        const body = text.split("\n[GeWu plugin result:")[0];
        if (!json) return body;
        try {
          return JSON.parse(body);
        } catch {
          throw Error(`${name} ${JSON.stringify(args)} returned non-JSON: ${body.slice(0, 1500)}`);
        }
      };
      const inventory = await agent("plugin_list", {});
      const instance = inventory.instances.find((instance) => instance.pluginId === "hyperframes");
      expect(instance).toBeTruthy();
      const call = (tool, args = {}) =>
        agent("plugin_call", { instanceId: instance.id, tool, args });
      const ok = (value, label, detail) => {
        if (!value)
          throw Error(`${label} failed: ${String(JSON.stringify(detail ?? value)).slice(0, 2000)}`);
        return value;
      };
      const look = await call("studio_look");
      await writeFile(
        join(process.env.GEWU_EVIDENCE, "studio-look.json"),
        JSON.stringify(look, null, 2),
      );
      const runtime = await studio
        .locator("hyperframes-player")
        .first()
        .evaluate((player) => {
          const iframe = player.iframeElement;
          try {
            return {
              ready: player.ready,
              sandbox: iframe.sandbox.value,
              clips: iframe.contentDocument?.querySelectorAll(".clip").length,
              timelines: Object.keys(iframe.contentWindow.__timelines ?? {}),
            };
          } catch (e) {
            return { error: e.message };
          }
        });
      await writeFile(
        join(process.env.GEWU_EVIDENCE, "studio-runtime.json"),
        JSON.stringify(runtime, null, 2),
      );
      await expect
        .poll(() => studio.locator('[data-clip="true"]').count())
        .toBeGreaterThanOrEqual(6);
      expect(look.data.clipCount).toBe(6);
      await studio.getByRole("button", { name: "Maximize Studio" }).click();
      await studio.getByRole("button", { name: "Play", exact: true }).click();
      await expect
        .poll(() =>
          studio
            .locator("hyperframes-player")
            .first()
            .evaluate((player) => player.currentTime),
        )
        .toBeGreaterThan(0.3);
      await studio.getByRole("button", { name: "Pause", exact: true }).click();
      await call("studio_seek", { time: 2 });
      const composition = studio.locator("hyperframes-player").first().frameLocator("iframe");
      await expect(composition.locator("#intro-title")).toBeVisible();
      const old = await call("source.read", { path: "compositions/intro.html" });
      const sdkEdit = await call("element.edit", {
        sourceFile: "compositions/intro.html",
        expectedRevision: old.data.revision,
        operations: [{ type: "setText", target: "hf-intro-title", value: "GeWu 视频制作 · 嵌套" }],
      });
      ok(sdkEdit.data?.saved, "SDK nested element edit", sdkEdit);
      await expect
        .poll(() => readFile(join(fixture.root, "video/compositions/intro.html"), "utf8"))
        .toContain("嵌套");
      // The human selects a layer in Studio's own panel; the agent reads that selection.
      await studio.getByText("Layers", { exact: true }).click();
      const captionRow = studio
        .locator("[data-layer-index]")
        .filter({ hasText: "Caption" })
        .first();
      await expect(captionRow).toBeVisible({ timeout: 15000 });
      await captionRow.click();
      await expect.poll(async () => (await call("preview.context")).data.selection).toBeTruthy();
      const selected = await call("preview.context");
      expect(JSON.stringify(selected.data.selection)).toContain("Caption");
      const captionHandle = look.data.elements.find((e) => e.label === "Caption").handle;
      await agent("plugin_describe", { instanceId: instance.id, tool: "studio_set_text" });
      const edited = await call("studio_set_text", {
        handle: captionHandle,
        text: "Agent shared title",
      });
      ok(edited.ok, "Studio human-selected text edit", edited);
      await expect
        .poll(() => readFile(join(fixture.root, "video/index.html"), "utf8"))
        .toContain("Agent shared title");
      const stale = await call("source.write", {
        path: "compositions/intro.html",
        content: old.data.content,
        expectedRevision: old.data.revision,
      });
      expect(stale.error.code).toBe("REVISION_CONFLICT");
      const picture = look.data.elements.find((e) => e.label === "Picture");
      await call("studio_select", { handle: picture.handle });
      const selection = studio.locator('[data-dom-edit-selection-box="true"]').last();
      await expect(selection).toBeVisible();
      const bounds = await selection.boundingBox();
      const beforeDrag = await readFile(join(fixture.root, "video/index.html"), "utf8");
      await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
      await page.mouse.down();
      await page.mouse.move(bounds.x + bounds.width / 2 + 35, bounds.y + bounds.height / 2 + 18, {
        steps: 12,
      });
      await page.mouse.up();
      await expect
        .poll(() => readFile(join(fixture.root, "video/index.html"), "utf8"))
        .not.toBe(beforeDrag);
      const afterDrag = await call("source.read", { path: "index.html" });
      expect(afterDrag.data.content).not.toBe(beforeDrag);
      await expect(studio.getByRole("button", { name: /^Undo/ }).first()).toBeEnabled();
      await studio.getByRole("button", { name: /^Undo/ }).first().click();
      await expect
        .poll(() => readFile(join(fixture.root, "video/index.html"), "utf8"))
        .toBe(beforeDrag);
      await studio.getByRole("button", { name: /^Redo/ }).first().click();
      await expect
        .poll(() => readFile(join(fixture.root, "video/index.html"), "utf8"))
        .not.toBe(beforeDrag);
      const checked = await call("project.check");
      ok(checked.data?.checksPassed, "Project check", checked);
      const frames = await call("frame.capture", { times: [2, 8] });
      ok(frames.ok, "Frame capture", frames);
      expect(frames.data.frames).toHaveLength(2);
      await agent("read_file", frames.data.frames[0].read_file, false);
      expect(
        fixture.requests.some((turn) =>
          turn.messages.some(
            (message) =>
              Array.isArray(message.content) &&
              message.content.some(
                (part) => part.type === "image_url" && part.image_url.url.startsWith("data:image/"),
              ),
          ),
        ),
      ).toBe(true);
      await writeFile(
        join(process.env.GEWU_EVIDENCE, "agent-visual-evidence.json"),
        JSON.stringify(
          {
            imageDelivered: true,
            sourceRevision: frames.data.sourceRevision,
            frameTimes: frames.data.frames.map((f) => f.time),
          },
          null,
          2,
        ),
      );
      await studio.getByRole("button", { name: "Export", exact: true }).first().click();
      const exported = studio.getByRole("region", { name: "GeWu render status" });
      await expect(exported.getByText("Video ready", { exact: true })).toBeVisible({
        timeout: 180000,
      });
      const download = page.waitForEvent("download");
      await exported.getByRole("button", { name: "Download video", exact: true }).click();
      const artifact = await download;
      const file = join(process.env.GEWU_EVIDENCE, "acceptance.mp4");
      await artifact.saveAs(file);
      const probed = spawnSync(
        "ffprobe",
        ["-v", "error", "-show_streams", "-show_format", "-of", "json", file],
        { encoding: "utf8" },
      );
      expect(probed.status).toBe(0);
      const metadata = JSON.parse(probed.stdout);
      expect(metadata.streams.some((s) => s.codec_type === "audio")).toBe(true);
      expect(metadata.streams.find((s) => s.codec_type === "video")).toMatchObject({
        width: 1920,
        height: 1080,
      });
      expect(Number(metadata.format.duration)).toBeGreaterThan(11.9);
      await writeFile(
        join(process.env.GEWU_EVIDENCE, "acceptance-ffprobe.json"),
        JSON.stringify(metadata, null, 2),
      );
      await exported.getByRole("button", { name: "Open video" }).click();
      const output = page.locator(".plugin-panel:not([hidden]) video");
      await expect(output).toBeVisible();
      await expect.poll(() => output.evaluate((video) => video.readyState)).toBeGreaterThan(0);
      await page.screenshot({ path: join(process.env.GEWU_EVIDENCE, "studio-open.png") });
      expect(errors).toEqual([]);
    } catch (error) {
      throw Error(
        `${error.message}\nBrowser errors: ${errors.join("\n")}\nHTTP: ${failures.join("\n")}\n${fixture.log()}`,
      );
    } finally {
      await fixture.close();
    }
  });
}
