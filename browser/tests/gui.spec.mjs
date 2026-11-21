import {createServer} from 'node:http';
import {once} from 'node:events';

const INDEX = `<!doctype html>
<html><head><title>Fixture page</title></head>
<body style="margin:0">
<h1>Fixture page</h1>
<button style="position:absolute;left:20px;top:80px;width:140px;height:30px"
  onclick="location.href='/second'">Open second page</button>
<p>Preview smoke target.</p>
</body></html>`;

const SECOND = `<!doctype html>
<html><head><title>Second page</title></head>
<body><h1>Second page</h1></body></html>`;

export function register({test, expect, startFixture}) {
  test('opens a managed tab, shows a screenshot and hands control to the user', async ({page}) => {
    const server = createServer((request, response) => {
      response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
      response.end(request.url.startsWith('/second') ? SECOND : INDEX);
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const url = `http://127.0.0.1:${server.address().port}/`;
    const fixture = await startFixture({settings: {mode: 'managed', headless: true}});
    try {
      await page.goto(fixture.url);
      await expect(page.getByRole('textbox', {name: 'Message'})).toBeEnabled();
      const reveal = page.getByRole('button', {name: 'Show files'});
      if (await reveal.isVisible()) await reveal.click();
      await page.getByRole('tab', {name: 'PREVIEW'}).click();
      // The bundled Marketplace plugin also has an Open button; scope to our row.
      const row = page.locator('strong', {hasText: 'GeWu Browser'}).locator('xpath=../..');
      await row.getByRole('button', {name: 'Open…', exact: true}).click();

      const frame = page.frameLocator('iframe[title="Browser"]');
      await expect(frame.getByRole('heading', {name: 'Browser'})).toBeVisible({timeout: 30000});
      await expect(frame.getByRole('status')).toContainText('Browser is not running');

      await frame.getByLabel('URL to open').fill(url);
      await frame.getByRole('button', {name: 'Go'}).click();
      await expect(frame.getByRole('status')).toContainText('1 tab', {timeout: 30000});
      await expect(frame.locator('.tab')).toContainText('Fixture page');

      // The Preview is a live frame, not a screenshot. Click the page's button
      // through the frame and verify the remote navigation reaches the tab.
      const image = frame.locator('#live-frame');
      await expect(image).toBeVisible({timeout: 30000});
      const natural = await image.evaluate((element) => ({
        width: element.naturalWidth,
        height: element.naturalHeight,
      }));
      expect(natural.width).toBeGreaterThan(0);
      const box = await image.boundingBox();
      await page.mouse.click(
        box.x + (80 * box.width) / natural.width,
        box.y + (95 * box.height) / natural.height,
      );
      await expect(frame.locator('#page-meta')).toContainText('Second page', {timeout: 30000});

      await frame.getByRole('button', {name: 'Take over'}).click();
      await expect(frame.getByRole('alert')).toContainText('You have control');
      await expect(frame.getByRole('button', {name: 'Resume agent'})).toBeVisible();
      await frame.getByRole('button', {name: 'Resume agent'}).click();
      await expect(frame.getByRole('alert')).toBeHidden();
    } finally {
      await fixture.close();
      server.close();
    }
  });
}
