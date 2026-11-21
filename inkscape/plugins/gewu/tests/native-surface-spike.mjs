import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFile,writeFile} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

export function register({test,expect}) {
  test('real native tools and composed Chinese text work through a sandboxed surface',async({page},info)=>{
    test.setTimeout(70000);
    const plugin=dirname(dirname(fileURLToPath(import.meta.url)));
    const proc=spawn(process.env.PYTHON||'python3',[join(plugin,'tests/serve_native_spike.py'),join(process.env.GEWU_TEST_TMP,'workspace')],{env:{...process.env,PYTHONPATH:plugin},stdio:['ignore','pipe','pipe']});
    let logs='';proc.stderr.on('data',data=>logs+=data);
    try {
      let buffer='';const config=await new Promise((resolve,reject)=>{
        proc.stdout.on('data',data=>{buffer+=data;if(buffer.includes('\n'))try{resolve(JSON.parse(buffer.split('\n')[0]));}catch(error){reject(error);}});
        proc.on('exit',code=>reject(Error(`Desktop exited ${code}: ${logs}`)));
      });
      await page.goto(config.url);
      const frame=page.frameLocator('#native');const surface=frame.locator('#screen');
      await expect(surface).toHaveAttribute('data-connected','true',{timeout:20000});
      const bounds=await frame.locator('canvas').boundingBox();
      await page.mouse.click(bounds.x+420,bounds.y+350);
      await page.keyboard.press('r');
      await page.mouse.move(bounds.x+300,bounds.y+260);await page.mouse.down();await page.mouse.move(bounds.x+500,bounds.y+390,{steps:12});await page.mouse.up();
      await page.keyboard.press('Escape');await page.keyboard.press('t');
      await page.mouse.click(bounds.x+350,bounds.y+440);
      await page.keyboard.type('Native typing ');
      const input=frame.getByLabel('Native text input');
      const cdp=await page.context().newCDPSession(page);
      await cdp.send('Input.imeSetComposition',{text:'原生编辑',selectionStart:4,selectionEnd:4});
      await cdp.send('Input.insertText',{text:'原生编辑'});
      await expect.poll(async()=>{const r=await page.request.get(config.url+'/snapshot');return r.ok()?await r.text():'';}).toContain('原生编辑');
      await page.keyboard.press('Escape');await page.keyboard.press('Control+s');
      await expect.poll(()=>readFile(config.figure,'utf8')).toContain('原生编辑');
      const saved=await readFile(config.figure,'utf8');expect(saved).toContain('Native typing');expect(saved).toContain('inkscape:path-effect');
      const pathPattern=/<path\b(?=[^>]*\bid="curve-a")(?=[^>]*\bd="([^"]+)")[^>]*>/s;
      const beforePath=saved.match(pathPattern)?.[1];expect(beforePath).toBeTruthy();
      await page.keyboard.press('F1');await page.keyboard.press('Escape');
      const point=await surface.evaluate(()=>{
        const {data,width,height}=window.surface.rfb.getImageData();let left=null;
        for(let y=100;y<height-70;y++)for(let x=40;x<width;x++){
          const i=(y*width+x)*4;if(data[i]===20&&data[i+1]===125&&data[i+2]===146&&(!left||x<left.x))left={x,y};
        }
        return left;
      });
      expect(point).not.toBeNull();
      await page.keyboard.down('Control');await page.mouse.click(bounds.x+point.x,bounds.y+point.y);await page.keyboard.up('Control');await page.keyboard.press('F2');
      await page.mouse.move(bounds.x+point.x,bounds.y+point.y);await page.mouse.down();await page.mouse.move(bounds.x+point.x+18,bounds.y+point.y+12,{steps:10});await page.mouse.up();
      await page.keyboard.press('Control+s');
      await expect.poll(async()=>{
        const raw=await readFile(config.figure,'utf8');
        const afterPath=raw.match(pathPattern)?.[1];return !!afterPath&&afterPath!==beforePath;
      }).toBe(true);
      await page.screenshot({path:info.outputPath('native-spike.png')});
      await writeFile(info.outputPath('spike.json'),JSON.stringify({nativeTools:true,composedChinese:true,nodeDrag:true,saved:true,canvas:bounds},null,2));
    }finally{
      if(proc.exitCode===null){const done=once(proc,'exit');proc.kill('SIGINT');const kill=setTimeout(()=>proc.kill('SIGKILL'),8000);await done;clearTimeout(kill);}
    }
  });
}
