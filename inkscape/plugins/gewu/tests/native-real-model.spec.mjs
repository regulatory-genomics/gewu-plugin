import {readFile,writeFile,readdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startCoworkFixture} from './cowork-fixture.mjs';
const run=promisify(execFile);

export function register({test,expect}) {
  test('a real model discovers native schemas and edits nested curves without flattening',async({page},info)=>{
    test.setTimeout(240000);
    if(!process.env.GEWU_REAL_CONFIG)throw Error('GEWU_REAL_CONFIG is required');
    await page.setViewportSize({width:1440,height:900});
    const f=await startCoworkFixture({settings:{editorMode:'native'},realConfig:process.env.GEWU_REAL_CONFIG,modelName:process.env.GEWU_REAL_MODEL,keep:true});
    try{
      const plugin=dirname(dirname(fileURLToPath(import.meta.url)));
      const path=join(f.root,'figure.svg');await writeFile(path,await readFile(join(plugin,'tests/fixtures/native-rich.svg')));
      await page.goto(f.url);
      const reveal=page.getByRole('button',{name:'Show files'});if(await reveal.isVisible())await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();
      page.once('dialog',d=>d.accept('figure.svg'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const view=page.frameLocator('iframe[title="Inkscape"]');
      await expect(view.locator('#screen')).toHaveAttribute('data-connected','true',{timeout:40000});
      const input=page.getByRole('textbox',{name:'Message'});
      await input.fill('Use the open Inkscape plugin to edit this document. Change the two original curves inside its nested group (curve-a and curve-b) to blue #2050d0 with stroke width 0.5 mm, and change the existing title to "Agent + human". Preserve the other objects and their layers, mask, clone and path effect. Discover the operation schemas you need from the plugin. Save the document and export a PDF. Do not use shell or edit SVG files directly.');
      await input.press('Enter');
      await expect.poll(()=>readFile(path,'utf8'),{timeout:190000}).toContain('Agent + human');
      const checked=await run(process.env.PYTHON||'python3',['-c',`from lxml import etree as E;import sys; r=E.parse(sys.argv[1]);
for key in ('curve-a','curve-b'):
 n=r.xpath('//*[@id=$key]',key=key)[0];assert '#2050d0' in n.get('style','');assert n.getparent().get('id')=='nested'
assert r.xpath('//*[@id="mask"]') and r.xpath('//*[@id="clone"]') and r.xpath('//*[@id="live-path"]');print('Verified native structure')`,path]);
      expect(checked.stdout).toContain('Verified native structure');
      await expect.poll(async()=>{
        const paths=await readdir(join(f.root,'.gewu'),{recursive:true});
        for(const path of paths.filter(p=>p.endsWith('.pdf'))){if((await readFile(join(f.root,'.gewu',path))).subarray(0,5).toString()==='%PDF-')return true;}
        return false;
      },{timeout:90000}).toBe(true);
      await view.getByRole('button',{name:'Maximize',exact:true}).click();
      await page.screenshot({path:info.outputPath('native-agent-cowork.png')});
      await writeFile(info.outputPath('figure.svg'),await readFile(path));
    }catch(error){await page.screenshot({path:info.outputPath('failure.png')});throw error;}
    finally{await f.close();}
  });
}
