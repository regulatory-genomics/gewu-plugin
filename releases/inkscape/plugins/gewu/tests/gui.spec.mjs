import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {startCoworkFixture} from './cowork-fixture.mjs';

export function register({test, expect}) {
  test('a human gesture and a real GeWu agent edit share history and native previews', async ({page}, info) => {
    test.setTimeout(120000);
    const fixture = await startCoworkFixture();
    try {
      await mkdir(join(fixture.root,'figure'));
      for (const [i,color] of ['#204e91','#f09812','#3b8252','#412763','#48141b','#0099aa'].entries()) {
        await writeFile(join(fixture.root,`panel${i}.svg`),
          `<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"><rect width="60" height="40" fill="${color}"/></svg>`);
      }
      await page.goto(fixture.url);
      const reveal=page.getByRole('button',{name:'Show files'});
      if (await reveal.isVisible()) await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();
      page.once('dialog',dialog=>dialog.accept('figure'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v0\.1\.0$/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const editor=page.frameLocator('iframe[title="Figure layout"]');
      await editor.getByRole('button',{name:'Create figure'}).click();
      await expect(editor.locator('#canvas img')).toHaveAttribute('src',/plugin-data/);
      await editor.getByLabel('Workspace assets').fill(Array.from({length:6},(_,i)=>`panel${i}.svg`).join('\n'));
      await editor.getByRole('button',{name:'Import panels'}).click();
      await expect(editor.locator('.object-row')).toHaveCount(6);
      const one=editor.locator('.hit').first();
      const before=await readFile(join(fixture.root,'figure/figure.svg'),'utf8');
      const box=await one.boundingBox();
      await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
      await page.mouse.down(); await page.mouse.move(box.x+box.width/2+38,box.y+box.height/2+14,{steps:8}); await page.mouse.up();
      const source=()=>readFile(join(fixture.root,'figure/figure.svg'),'utf8');
      await expect.poll(source).not.toBe(before);
      await expect(editor.locator('#status')).toHaveText('Saved');
      const rev=await editor.locator('#canvas').getAttribute('data-revision');
      const id=await editor.locator('.object-row[aria-pressed=true]').getAttribute('data-id');
      const prompt=page.getByRole('textbox',{name:'Message'});
      await prompt.fill('call plugin_open '+JSON.stringify({pluginId:'inkscape',bindingResource:{uri:'workspace:///figure',type:'inkscape.document'}})); await prompt.press('Enter');
      await expect(page.getByText('The adapter returned a result.',{exact:true}).first()).toBeVisible({timeout:20000});
      const outputs=fixture.requests.flatMap(turn=>turn.messages.filter(m=>m.role==='tool'));
      const instanceId=outputs.map(m=>{try{return JSON.parse(m.content).id;}catch{return null;}}).find(Boolean);
      expect(instanceId).toBeTruthy();
      const ask=(tool,args)=>`call plugin_call ${JSON.stringify({instanceId,tool,args})}`;
      await prompt.fill(ask('selection',{})); await prompt.press('Enter');
      await expect(page.getByText('The adapter returned a result.',{exact:true})).toHaveCount(2,{timeout:20000});
      expect(fixture.requests.some(turn=>turn.messages.some(m=>m.role==='tool' && m.content.includes(id)))).toBeTruthy();
      const ids=await editor.locator('.object-row').evaluateAll(rows=>rows.map(row=>row.dataset.id));
      const beforeAgentImage=await editor.locator('#canvas img').getAttribute('src');
      const operations=[
        {op:'align',ids:ids.slice(0,3),edge:'top',reference:ids[1]},
        {op:'align',ids:[ids[0]],edge:'left',reference:ids[3]},
        {op:'distribute',ids:ids.slice(0,3),axis:'x',gap:4},
        ...ids.map((_,i)=>({op:'add_text',id:`agent-label-${i}`,text:'ABCDEF'[i],x:6+(i%3)*44,y:i<3?7:53})),
      ];
      await prompt.fill(ask('edit',{expectedRevision:rev,operations}));
      await prompt.press('Enter');
      await expect.poll(source).toContain('agent-label');
      await expect(editor.locator('.object-row')).toHaveCount(12);
      await expect(editor.locator('#canvas img')).not.toHaveAttribute('src',beforeAgentImage);
      await expect.poll(()=>editor.locator('#canvas img').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
      await page.screenshot({path:info.outputPath('cowork.png')});
      const committed=await source();
      await prompt.fill(ask('edit',{expectedRevision:rev,operations:[{op:'delete',ids:[ids[0]]}]})); await prompt.press('Enter');
      await expect(page.getByText('The adapter returned a result.',{exact:true})).toHaveCount(4,{timeout:20000});
      expect(fixture.requests.some(turn=>turn.messages.some(m=>m.role==='tool' && String(m.content).includes('STALE_REVISION')))).toBeTruthy();
      expect(await source()).toBe(committed);
      await editor.getByRole('button',{name:'Undo',exact:true}).click();
      await expect.poll(source).not.toContain('agent-label');
      await editor.getByRole('button',{name:'Redo',exact:true}).click();
      await expect.poll(source).toContain('agent-label');
      await expect(editor.locator('#status')).toHaveText('Saved');
      await expect(editor.locator('.object-row')).toHaveCount(12);
      await editor.getByRole('button',{name:'Export PDF',exact:true}).click();
      const download=editor.getByRole('link',{name:'Download PDF'});
      await expect(download).toBeVisible();
      const response=await page.request.get(new URL(await download.getAttribute('href'),page.url()).href);
      expect((await response.body()).subarray(0,5).toString()).toBe('%PDF-');
    } catch (error) {
      const trace=fixture.requests.map(turn=>({tools:turn.tools?.map(t=>t.function.name),
        messages:turn.messages.filter(m=>m.role==='user'||m.role==='tool').map(m=>[m.role,m.content])}));
      await info.attach('fixture-tool-trace',{body:JSON.stringify(trace,null,2),contentType:'application/json'});
      console.error(JSON.stringify(trace).slice(0,8000));
      throw error;
    } finally { await fixture.close(); }
  });
}
