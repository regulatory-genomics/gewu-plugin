import {readFile,writeFile} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startCoworkFixture} from './cowork-fixture.mjs';

export function register({test,expect}) {
  test.use({ignoreHTTPSErrors:true});
  test('GeWu opens the real Inkscape workspace and saves human text through the proxy',async({page},info)=>{
    test.setTimeout(90000);
    await page.setViewportSize({width:1440,height:900});
    const f=await startCoworkFixture({settings:{editorMode:'native'},keep:true});
    try {
      const plugin=dirname(dirname(fileURLToPath(import.meta.url)));
      await writeFile(join(f.root,'figure.svg'),await readFile(join(plugin,'tests/fixtures/native-rich.svg')));
      await page.goto(f.url);
      const reveal=page.getByRole('button',{name:'Show files'});if(await reveal.isVisible())await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();
      page.once('dialog',d=>d.accept('figure.svg'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const view=page.frameLocator('iframe[title="Inkscape"]');
      await expect(view.locator('#screen')).toHaveAttribute('data-connected','true',{timeout:40000});
      await view.getByRole('button',{name:'Maximize',exact:true}).click();
      await expect(view.locator('canvas')).toBeVisible();
      await expect.poll(()=>view.locator('canvas').evaluate(c=>c.width),{timeout:10000}).toBeGreaterThan(1300);
      await expect.poll(()=>view.locator('canvas').evaluate(c=>c.height),{timeout:10000}).toBeGreaterThan(600);
      const b=await view.locator('canvas').boundingBox();
      await page.mouse.click(b.x+Math.min(300,b.width*.4),b.y+Math.min(300,b.height*.5));
      await page.keyboard.press('t');
      await page.mouse.click(b.x+Math.min(350,b.width*.5),b.y+Math.min(350,b.height*.6));
      await page.keyboard.type('GeWu native edit');
      await page.keyboard.press('Escape');
      await view.getByRole('button',{name:'Save',exact:true}).click();
      await expect.poll(()=>readFile(join(f.root,'figure.svg'),'utf8'),{timeout:15000}).toContain('GeWu native edit');
      await page.screenshot({path:info.outputPath('native-workspace.png')});
    }catch(error){await page.screenshot({path:info.outputPath('failure.png')});throw error;}
    finally{await f.close();}
  });
}
