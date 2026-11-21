import {readFile,writeFile} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startCoworkFixture} from './cowork-fixture.mjs';

export function register({test,expect}){
  test('browser file import and live SVG export retain editable vectors before save',async({page},info)=>{
    test.setTimeout(90000);await page.setViewportSize({width:1440,height:900});
    const f=await startCoworkFixture({settings:{editorMode:'native'},keep:true});
    try{
      const plugin=dirname(dirname(fileURLToPath(import.meta.url)));const original=await readFile(join(plugin,'tests/fixtures/native-rich.svg'));
      await writeFile(join(f.root,'figure.svg'),original);await page.goto(f.url);
      const reveal=page.getByRole('button',{name:'Show files'});if(await reveal.isVisible())await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();page.once('dialog',d=>d.accept('figure.svg'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const view=page.frameLocator('iframe[title="Inkscape"]');await expect(view.locator('#screen')).toHaveAttribute('data-connected','true',{timeout:30000});
      const picker=page.waitForEvent('filechooser');await view.getByRole('button',{name:'Import…',exact:true}).click();
      await (await picker).setFiles({name:'editable.svg',mimeType:'image/svg+xml',buffer:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><text id="axis" x="3" y="20">Imported vector marker</text></svg>')});
      await expect(view.locator('#status')).toContainText('Unsaved',{timeout:15000});
      expect(await readFile(join(f.root,'figure.svg'),'utf8')).not.toContain('Imported vector marker');
      await view.getByRole('button',{name:'Export SVG',exact:true}).click();
      const link=view.getByRole('link',{name:'Download SVG'});await expect(link).toBeVisible({timeout:15000});
      const exported=await page.request.get(new URL(await link.getAttribute('href'),page.url()).href);expect(exported.ok()).toBeTruthy();
      const raw=await exported.text();expect(raw).toContain('Imported vector marker');expect(raw).toContain('<text');
      await view.getByRole('button',{name:'Save',exact:true}).click();
      await expect.poll(()=>readFile(join(f.root,'figure.svg'),'utf8'),{timeout:15000}).toContain('Imported vector marker');
      await writeFile(info.outputPath('native-import-export.svg'),raw);
    }catch(error){await page.screenshot({path:info.outputPath('failure.png')});throw error;}
    finally{await f.close();}
  });
}
