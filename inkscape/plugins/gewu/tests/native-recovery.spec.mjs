import {readFile,writeFile,readdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startCoworkFixture} from './cowork-fixture.mjs';

export function register({test,expect}) {
  test('native session survives prolonged editing, view reloads and reconnects',async({page},info)=>{
    const seconds=Number(process.env.GEWU_SOAK_SECONDS||1800);test.setTimeout((seconds+90)*1000);
    await page.setViewportSize({width:1440,height:900});
    const f=await startCoworkFixture({settings:{editorMode:'native'},keep:true});
    try{
      const plugin=dirname(dirname(fileURLToPath(import.meta.url))),file=join(f.root,'figure.svg');
      await writeFile(file,await readFile(join(plugin,'tests/fixtures/native-rich.svg')));
      await page.goto(f.url);
      const reveal=page.getByRole('button',{name:'Show files'});if(await reveal.isVisible())await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();page.once('dialog',d=>d.accept('figure.svg'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const view=()=>page.frameLocator('iframe[title="Inkscape"]');
      await expect(view().locator('#screen')).toHaveAttribute('data-connected','true',{timeout:30000});
      const initial=await readdir(join(f.root,'.gewu/inkscape'),{recursive:true});
      const initialSession=initial.find(x=>x.endsWith('/session.json'));
      const deadline=Date.now()+seconds*1000;let rounds=0,reloads=0;
      while(Date.now()<deadline){
        const v=view();await v.getByRole('button',{name:'Maximize',exact:true}).click();
        const b=await v.locator('canvas').boundingBox();await page.mouse.click(b.x+300,b.y+350);
        await page.keyboard.press('F1');await page.keyboard.press('Escape');await page.keyboard.press('5');
        await v.getByRole('button',{name:'Save',exact:true}).click();
        await expect(v.locator('#error')).toBeHidden({timeout:5000});
        if(rounds%5===0){
          await page.getByRole('button',{name:'Reload view',exact:true}).click();
          await expect(view().locator('#screen')).toHaveAttribute('data-connected','true',{timeout:15000});reloads++;
          const sessions=await readdir(join(f.root,'.gewu/inkscape'),{recursive:true});
          expect(sessions.filter(x=>x.endsWith('/session.json'))).toEqual([initialSession]);
        }
        rounds++;await page.waitForTimeout(5000);
      }
      await writeFile(info.outputPath('reliability.json'),JSON.stringify({seconds,rounds,reloads,sameNativeSession:true}));
      await page.screenshot({path:info.outputPath('reliability.png')});
    }catch(error){await page.screenshot({path:info.outputPath('failure.png')});throw error;}
    finally{await f.close();}
  });
}
