import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';

const MANUSCRIPT = `title: Example
contents: [main.md]
`;

const MAIN = `# Introduction

A paragraph written before the editor opened.

A citation sentence [@doe2020] that must be edited as source.
`;

async function project(fixture) {
  const dir = join(fixture.root, 'manuscript');
  await mkdir(dir, {recursive:true});
  await writeFile(join(dir, 'metadata.yaml'), MANUSCRIPT);
  await writeFile(join(dir, 'main.md'), MAIN);
  return dir;
}

async function openEditor(page, fixture) {
  await page.goto(fixture.url);
  await page.getByRole('textbox', {name:'Message'}).waitFor();
  const reveal = page.getByRole('button', {name:'Show files'});
  if (await reveal.isVisible()) await reveal.click();
  await page.getByRole('tab', {name:'PREVIEW'}).click();
  page.once('dialog', (dialog) => dialog.accept('manuscript'));
  await page.getByRole('button', {name:'Open…', exact:true}).click();
  const editor = page.frameLocator('iframe[title="Manuscript editor"]');
  await editor.locator('#document .block').first().waitFor({timeout:60000});
  return editor;
}

export function register({test, expect, startFixture}) {
  test('edits a paragraph in Preview and builds the edited manuscript', async ({page}) => {
    const fixture = await startFixture({settings:{executable:process.env.PAPER_BIN}});
    try {
      const dir = await project(fixture);
      const editor = await openEditor(page, fixture);

      const paragraph = editor.locator('p.block').first();
      await expect(paragraph).toHaveText('A paragraph written before the editor opened.');
      await paragraph.click();
      await paragraph.fill('A paragraph edited directly in Preview.');
      await paragraph.press('Control+s');
      await expect(editor.locator('#status')).toContainText('Saved', {timeout:60000});

      // The edit lands in the project's own Markdown, not in a side store.
      const source = await readFile(join(dir, 'main.md'), 'utf8');
      expect(source).toContain('A paragraph edited directly in Preview.');
      expect(source).toContain('# Introduction');
      expect(source).toContain('A citation sentence [@doe2020]');
      // The untouched block kept its exact bytes.
      expect(source.endsWith('that must be edited as source.\n')).toBe(true);

      await editor.getByRole('button', {name:'Build HTML'}).click();
      await expect(editor.locator('#status')).toContainText('Built HTML', {timeout:180000});
      const preview = page.frameLocator('iframe[title="File preview"]');
      await expect(preview.getByRole('heading', {name:'Introduction'})).toBeVisible({timeout:60000});
      await expect(preview.getByText('A paragraph edited directly in Preview.')).toBeVisible();
    } finally {
      await fixture.close();
    }
  });

  test('a block with a citation opens as exact Markdown source', async ({page}) => {
    const fixture = await startFixture({settings:{executable:process.env.PAPER_BIN}});
    try {
      const dir = await project(fixture);
      const editor = await openEditor(page, fixture);

      const rich = editor.locator('.block.rich').first();
      await expect(rich).toContainText('[@doe2020]');
      await rich.click();
      const area = editor.locator('.editor-text');
      await expect(area).toHaveValue('A citation sentence [@doe2020] that must be edited as source.\n');
      await area.fill('A citation sentence [@doe2020] edited as Markdown source.\n');
      await area.press('Control+s');
      await expect(editor.locator('#status')).toContainText('Saved', {timeout:60000});

      const source = await readFile(join(dir, 'main.md'), 'utf8');
      expect(source).toContain('A citation sentence [@doe2020] edited as Markdown source.');
      // The citation itself is preserved verbatim.
      expect(source).toContain('[@doe2020]');
    } finally {
      await fixture.close();
    }
  });
}
