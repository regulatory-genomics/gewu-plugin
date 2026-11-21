import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {startCoworkFixture} from './cowork-fixture.mjs';

export function register({test,expect}) {
  test('a configured model arranges six imported panels through the Inkscape adapter',async({page},info)=>{
    test.setTimeout(240000);
    if(!process.env.GEWU_REAL_CONFIG) throw new Error('Set GEWU_REAL_CONFIG to the configured GeWu model file');
    const f=await startCoworkFixture({realConfig:process.env.GEWU_REAL_CONFIG,modelName:process.env.GEWU_REAL_MODEL,keep:true});
    try {
      await mkdir(join(f.root,'figure'));
      for(const [i,c] of ['#204e91','#f09812','#3b8252','#412763','#48141b','#0099aa'].entries())
        await writeFile(join(f.root,`panel${i+1}.svg`),`<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"><rect width="60" height="40" fill="${c}"/></svg>`);
      await page.goto(f.url);
      const reveal=page.getByRole('button',{name:'Show files'}); if(await reveal.isVisible()) await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();
      page.once('dialog',d=>d.accept('figure'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const editor=page.frameLocator('iframe[title="Inkscape"]');
      await editor.getByRole('button',{name:'Create figure'}).click();
      await editor.getByLabel('Workspace assets').fill(Array.from({length:6},(_,i)=>`panel${i+1}.svg`).join('\n'));
      await editor.getByRole('button',{name:'Import panels'}).click();
      await expect(editor.locator('.object-row')).toHaveCount(6);
      const original=await readFile(join(f.root,'figure/figure.svg'),'utf8');
      const img=editor.locator('#canvas img'), src=await img.getAttribute('src');
      const prompt=page.getByRole('textbox',{name:'Message'});
      await prompt.fill('Use the Inkscape instance ID from the attached plugin context. Call plugin_call with tool state for exact revision and IDs. Arrange the six panels in order at top-left mm positions (10,15), (54,15), (98,15), (10,65), (54,65), (98,65) without changing width. Add labels A–F 2 mm above them. Submit one plugin_call edit with args {expectedRevision, operations}; use {op:"move",ids:[id],dx,dy} and {op:"add_text",text,x,y}. Use the plugin only and briefly confirm.');
      await prompt.press('Enter');
      await expect(editor.locator('.object-row')).toHaveCount(12,{timeout:190000});
      await expect(img).not.toHaveAttribute('src',src);
      await expect.poll(()=>img.evaluate(i=>i.complete&&i.naturalWidth>0)).toBe(true);
      const result=await readFile(join(f.root,'figure/figure.svg'),'utf8');
      expect(result).not.toBe(original);
      for(const label of 'ABCDEF') expect(result).toMatch(new RegExp(`>${label}</text>`));
      await page.screenshot({path:info.outputPath('real-model-cowork.png')});
      await writeFile(info.outputPath('figure.svg'),result);
      const preview=await page.request.get(new URL(await img.getAttribute('src'),page.url()).href);
      expect(preview.ok()).toBeTruthy();await writeFile(info.outputPath('figure.png'),await preview.body());
    }finally{await f.close();}
  });
}
