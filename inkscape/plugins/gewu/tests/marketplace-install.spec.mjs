import {readFile,writeFile} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startCoworkFixture} from './cowork-fixture.mjs';

export function register({test,expect}){
  test('default Marketplace Install provisions bundled runtime and opens native editor',async({page},info)=>{
    test.setTimeout(300000);await page.setViewportSize({width:1440,height:900});
    if(!process.env.GEWU_MARKETPLACE_BASE_URL)throw Error('Provide a signed fixture catalog using GEWU_MARKETPLACE_BASE_URL');
    const f=await startCoworkFixture({installOnly:true,cleanPATH:true,keep:true});
    try{
      const plugin=dirname(dirname(fileURLToPath(import.meta.url)));await writeFile(join(f.root,'figure.svg'),await readFile(join(plugin,'tests/fixtures/native-rich.svg')));
      await page.goto(f.url);const reveal=page.getByRole('button',{name:'Show files'});if(await reveal.isVisible())await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();await page.getByRole('button',{name:'Marketplace',exact:true}).first().click();
      const row=page.locator('.plugin-marketplace .plugin-row').filter({hasText:/inkscape/});
      await expect(row).toBeVisible({timeout:15000});
      await row.getByRole('button',{name:'Install',exact:true}).click();
      await expect(row.getByRole('status')).toContainText('via bundled',{timeout:180000});
      await page.getByRole('button',{name:'Plugins',exact:true}).first().click();page.once('dialog',d=>d.accept('figure.svg'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const view=page.frameLocator('iframe[title="Inkscape"]');await expect(view.locator('#screen')).toHaveAttribute('data-connected','true',{timeout:45000});
      await view.getByRole('button',{name:'Maximize',exact:true}).click();
      await page.screenshot({path:info.outputPath('marketplace-native-install.png')});
      const installed=JSON.parse(await readFile(join(dirname(f.root),'.gewu/plugins/state.json'),'utf8'));
      expect(JSON.stringify(installed)).toContain('bundled');
    }catch(error){await page.screenshot({path:info.outputPath('failure.png')});throw error;}
    finally{await f.close();}
  });
}
