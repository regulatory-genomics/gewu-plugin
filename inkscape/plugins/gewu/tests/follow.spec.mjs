import {spawn,execFile} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,readFile,rm} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';
import {startCoworkFixture} from './cowork-fixture.mjs';

const run=promisify(execFile);
const plugin=dirname(dirname(fileURLToPath(import.meta.url)));
const PY=process.env.PYTHON || 'python3';

export function register({test,expect}) {
  test('browser follows the native selection and subsequent in-memory revision',async({page},info)=>{
    test.setTimeout(60000);
    const dir=await mkdtemp(join(process.env.GEWU_TEST_TMP,'follow-'));
    const session=join(dir,'desktop');
    const fixture=await startCoworkFixture({settings:{nativeSessionDirectory:session}});
    let xvfb,desktop;
    try {
      await mkdir(join(fixture.root,'figure'));
      await page.goto(fixture.url);
      const reveal=page.getByRole('button',{name:'Show files'}); if(await reveal.isVisible()) await reveal.click();
      await page.getByRole('tab',{name:'PREVIEW'}).click();
      page.once('dialog',d=>d.accept('figure'));
      await page.locator('.plugin-row').filter({has:page.locator('.plugin-head > strong').filter({hasText:/^Inkscape v/})}).getByRole('button',{name:'Open…',exact:true}).click();
      const view=page.frameLocator('iframe[title="Inkscape"]');
      await view.getByRole('button',{name:'Create figure'}).click();
      await view.getByText('Text and labels',{exact:true}).click();
      await view.getByLabel('Label text').fill('Native follow');
      await view.getByRole('button',{name:'Add label',exact:true}).click();
      await expect(view.locator('.object-row')).toHaveCount(1);
      const id=await view.locator('.object-row').first().getAttribute('data-id');
      await view.getByText('Desktop editing',{exact:true}).click();
      await view.getByRole('button',{name:'Check out a copy'}).click();
      await expect(view.locator('#checkout-path')).not.toBeEmpty();
      const file=join(fixture.root,(await view.locator('#checkout-path').textContent()).replace('Desktop copy: ',''));
      xvfb=spawn(process.env.XVFB_BIN,['-displayfd','1','-screen','0','1024x768x24'],{stdio:['ignore','pipe','ignore']});
      const [display]=await once(xvfb.stdout,'data');
      desktop=spawn(PY,[join(plugin,'live.py'),'launch','--inkscape',process.env.INKSCAPE_BIN,'--figure',file,'--session',session],
                    {env:{...process.env,DISPLAY:':'+String(display).trim(),NO_AT_BRIDGE:'1'},stdio:'ignore',detached:true});
      await expect.poll(async()=>{try{return JSON.parse(await readFile(join(session,'session.json'),'utf8')).figure;}catch{return null;}}).toBe(file);
      await run(PY,['-c',`import sys; from live import Live; b=Live(sys.argv[1],sys.argv[2],None); b.wait_ready(14); b.gdbus(b.path,'org.gtk.Actions.Activate','select-by-id',"[<"+repr(sys.argv[3])+">]",'{}')`,session,file,id],{env:{...process.env,PYTHONPATH:plugin}});
      await view.getByLabel('Follow desktop').check();
      await expect(view.locator('#live-report')).toContainText('1 selected',{timeout:18000});
      const img=view.locator('#live-preview'); await expect(img).toBeVisible(); const old=await img.getAttribute('src');
      await run(PY,['-c',`import sys; from live import Live; from native import Engine; from pathlib import Path; b=Live(sys.argv[1],sys.argv[2],Engine(sys.argv[3])); s=b.state(); b.edit(s['revision'],[{'op':'set_text','id':sys.argv[4],'text':'Changed in native'}],Path(sys.argv[2]).parent)`,session,file,process.env.INKSCAPE_BIN,id],{env:{...process.env,PYTHONPATH:plugin}});
      await expect(img).not.toHaveAttribute('src',old,{timeout:15000});
      await expect.poll(()=>img.evaluate(i=>i.complete&&i.naturalWidth>0)).toBe(true);
      expect(await readFile(join(fixture.root,'figure/figure.svg'),'utf8')).not.toContain('Changed in native');
      await page.screenshot({path:info.outputPath('follow.png')});
    }finally{
      if(desktop?.pid)try{process.kill(-desktop.pid,'SIGTERM');}catch{}
      xvfb?.kill(); await fixture.close(); await rm(dir,{recursive:true,force:true});
    }
  });
}
