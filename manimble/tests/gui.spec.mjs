import {cp, readFile} from 'node:fs/promises';
import {join} from 'node:path';

export function register({test, expect, startFixture}) {
  test('edits a deck on the canvas and downloads PowerPoint', async ({page}) => {
    test.setTimeout(300000);
    const fixture = await startFixture({settings:{python:process.env.MANIMBLE_ENGINE_PYTHON, engineRoot:process.env.MANIMBLE_ENGINE_ROOT}});
    try {
      await cp(new URL('../fixtures/deck/', import.meta.url), join(fixture.root, 'deck'), {recursive: true});
      await page.goto(fixture.url);
      await expect(page.getByRole('textbox', {name:'Message'})).toBeEnabled();

      const reveal = page.getByRole('button', {name:'Show files'});
      if (await reveal.isVisible()) await reveal.click();
      await page.getByRole('tab', {name:'PREVIEW'}).click();
      page.once('dialog', dialog => dialog.accept('deck'));
      await page.getByRole('button', {name:'Open…', exact:true}).click();

      // The application's own editor is the landing view, not a tool console.
      const editor = page.frameLocator('iframe[title="Slide editor"]');
      const slides = editor.getByRole('navigation', {name:'Slides'}).getByRole('button');
      const source = () => readFile(join(fixture.root, 'deck/slides.py'), 'utf8');
      await expect(slides).toHaveCount(1, {timeout:120000});
      await expect(editor.locator('#canvas img')).toHaveAttribute('src', /plugin-data/);
      await expect(editor.locator('#status')).toContainText('Intro · 1 build', {timeout:120000});

      // Two objects on the slide, the text on top of the circle: marquee-select
      // both (a click would only reach the topmost box) and group them.
      await expect(editor.locator('.hit')).toHaveCount(2);
      const canvas = await editor.locator('#canvas').boundingBox();
      await page.mouse.move(canvas.x + 4, canvas.y + 4);
      await page.mouse.down();
      await page.mouse.move(canvas.x + canvas.width - 4, canvas.y + canvas.height - 4, {steps: 12});
      await page.mouse.up();
      await expect(editor.getByRole('heading', {name:'2 selected'})).toBeVisible();
      await editor.getByRole('button', {name:'Group', exact:true}).click();
      await expect.poll(source, {timeout:60000}).toMatch(/Group\(/);
      await editor.getByRole('button', {name:'Undo', exact:true}).click();
      await expect.poll(source, {timeout:60000}).not.toMatch(/Group\(/);

      // Clicking empty space clears the selection; a plain click inside an
      // existing selection keeps it whole, like the desktop app.
      await page.mouse.click(canvas.x + 4, canvas.y + 4);
      await expect(editor.getByRole('heading', {name:'Selection'})).toBeVisible();
      const label = editor.locator('.hit[aria-label^="text"]');
      await label.click();
      await expect(editor.locator('#f-size')).toBeVisible();

      // Dragging moves it; alt keeps snapping out of the way.
      const before = await source();
      const box = await label.boundingBox();
      await page.keyboard.down('Alt');
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 90, box.y + box.height / 2 + 40, {steps: 12});
      await page.mouse.up();
      await page.keyboard.up('Alt');
      await expect.poll(source, {timeout:60000}).not.toBe(before);
      // The move must survive its verification render, not be rolled back.
      await expect(editor.locator('#status')).toHaveText('Saved Intro', {timeout:120000});
      await expect(editor.locator('#undo')).toBeEnabled();
      expect(await source()).toMatch(/\.shift\(/);

      // The inspector edits the selected object through the same engine op.
      await editor.locator('#f-size').fill('72');
      await editor.locator('#f-size').press('Enter');
      await expect.poll(source, {timeout:60000}).toMatch(/font_size=72/);

      await expect(editor.locator('#status')).toHaveText('Saved Intro', {timeout:120000});
      await expect(editor.locator('#f-size')).toHaveValue('72');
      const originalBox = await label.boundingBox();
      await label.dblclick();
      const inline = editor.locator('#inline');
      await expect(inline).toBeFocused();
      // Editing text should cover the rendered glyphs, not shrink to tiny type.
      const inkWidth = await inline.evaluate(area => {
        const context = document.createElement('canvas').getContext('2d');
        context.font = getComputedStyle(area).font;
        return context.measureText(area.value).width;
      });
      expect(inkWidth).toBeGreaterThan(originalBox.width * 0.8);
      expect(inkWidth).toBeLessThan(originalBox.width * 1.2);
      await inline.fill('Edited without shrinking');
      // Layout/zoom updates must not throw away an uncommitted text draft.
      await page.setViewportSize({width:1400, height:900});
      await expect(inline).toHaveValue('Edited without shrinking');
      await inline.press('Control+Enter');
      await expect(editor.locator('#status')).toHaveText('Saved Intro', {timeout:120000});
      await expect(label).toHaveAttribute('aria-label', 'text Edited without shrinking');
      await expect(editor.locator('#f-size')).toHaveValue('72');
      const colour = editor.getByLabel('Colour', {exact:true});
      await expect(colour).toHaveAttribute('type', 'color');
      await colour.fill('#ff8800');
      await expect.poll(source, {timeout:60000}).toMatch(/#ff8800/i);

      await editor.getByRole('button', {name:'Duplicate'}).click();
      await expect(slides).toHaveCount(2, {timeout:120000});
      await editor.getByRole('button', {name:'Delete slide'}).click();
      await expect(slides).toHaveCount(1, {timeout:120000});
      await editor.getByRole('button', {name:'Undo'}).click();
      await expect(slides).toHaveCount(2, {timeout:120000});
      expect(await source()).toMatch(/class \w*Copy\d*\(/);

      await editor.getByRole('button', {name:'Export PPTX'}).click();
      const download = editor.locator('#download');
      await expect(download).toBeVisible({timeout:180000});
      const response = await page.request.get(new URL(await download.getAttribute('href'), page.url()).href);
      expect(response.ok()).toBe(true);
      expect((await response.body()).subarray(0, 2).toString()).toBe('PK');
    } finally {
      await fixture.close();
    }
  });
}
