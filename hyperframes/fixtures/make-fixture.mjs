import {mkdir,copyFile,writeFile,readFile} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {StudioProject} from '../src/project.ts';

const root=resolve(process.argv[2]??new URL('../../../dist/gewu-fixture',import.meta.url).pathname);
const dir=join(root,'video');await mkdir(dir,{recursive:true});
const project=new StudioProject({workspaceRoot:root,projectDir:dir,studioDir:resolve(import.meta.dirname,'../../../dist/gewu-hyperframes/web')});
try {
  await project.create({duration:12});
  for(const [file,args]of [
    ['footage.mp4',['-f','lavfi','-i','color=c=0x183454:s=640x360:r=30:d=12','-an','-c:v','libx264','-pix_fmt','yuv420p']],
    ['tone.wav',['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=12','-af','volume=0.08','-c:a','pcm_s16le']],
    ['picture.png',['-f','lavfi','-i','color=c=0x65d8c3:s=160x160','-frames:v','1']],
  ]){
    const result=spawnSync('ffmpeg',['-hide_banner','-loglevel','error',...args,join(dir,'assets',file)],{stdio:'inherit'});
    if(result.status!==0)throw Error(`Fixture media generation failed: ${file}`);
  }
  const font=process.env.GEWU_HYPERFRAMES_FONT??'/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc';
  await copyFile(font,join(dir,'assets/font.ttc'));
  await mkdir(join(dir,'compositions'));
  for(const [name,title,color]of [['intro','GeWu 视频制作','#132238'],['outro','Human + agent','#21453e']]){
    const html=`<template><div data-composition-id="${name}" data-width="1920" data-height="1080" data-duration="6"><h1 class="clip" id="${name}-title" data-hf-id="hf-${name}-title" data-start="0" data-duration="6" data-track-index="0">${title}</h1><p class="clip" id="${name}-subtitle" data-start="0" data-duration="6" data-track-index="1">Editable source · shared timeline</p><style>[data-composition-id="${name}"]{width:1920px;height:1080px;background:${color};font-family:GeWuFixture,sans-serif;color:white;position:relative}[data-composition-id="${name}"] h1{position:absolute;left:120px;top:250px;font-size:96px;margin:0}[data-composition-id="${name}"] p{position:absolute;left:125px;top:420px;font-size:42px}</style><script>(function(){const tl=gsap.timeline({paused:true});tl.fromTo('#${name}-title',{opacity:0,y:60},{opacity:1,y:0,duration:0.8});tl.to('#${name}-title',{x:80,duration:4,ease:'none'},1);window.__timelines=window.__timelines||{};window.__timelines.${name}=tl;})();</script></div></template>`;
    await project.write(`compositions/${name}.html`,html,null,`Created ${name} scene`);
  }
  const source=await project.read('index.html');
  await project.write('index.html',`<!doctype html><html><head><meta charset="utf-8"><style>@font-face{font-family:GeWuFixture;src:url('assets/font.ttc')}html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#132238;color:#fff}.clip{position:absolute}.scene{inset:0;width:1920px;height:1080px}video{left:1120px;top:520px;width:640px;height:360px}img{left:140px;top:620px;width:160px;height:160px}h2{left:140px;top:840px;font-size:52px;margin:0;color:#fff}</style><script src="assets/gsap.min.js"></script></head><body><div data-composition-id="main" data-width="1920" data-height="1080" data-duration="12" data-fps="30"><div id="intro-host" class="clip scene" data-composition-id="intro" data-composition-src="compositions/intro.html" data-start="0" data-duration="6" data-track-index="0"></div><div id="outro-host" class="clip scene" data-composition-id="outro" data-composition-src="compositions/outro.html" data-start="6" data-duration="6" data-track-index="0"></div><video id="footage" class="clip" src="assets/footage.mp4" muted playsinline data-start="0" data-duration="12" data-track-index="1"></video><img id="picture" class="clip" src="assets/picture.png" data-start="0" data-duration="12" data-track-index="2"><h2 id="caption" data-hf-id="hf-caption" class="clip" data-start="0" data-duration="12" data-track-index="4">Human and agent</h2><audio id="music" class="clip" src="assets/tone.wav" data-start="0" data-duration="12" data-track-index="3" data-volume="0.5"></audio></div><script>const tl=gsap.timeline({paused:true});tl.to('#picture',{rotation:90,duration:12,ease:'none'});window.__timelines=window.__timelines||{};window.__timelines.main=tl;</script></body></html>`,source.revision,'Built two-scene acceptance video');
  const files=['index.html','compositions/intro.html','compositions/outro.html','assets/footage.mp4','assets/tone.wav','assets/picture.png','assets/font.ttc','assets/gsap.min.js'];
  const manifest=[];for(const path of files){const bytes=await readFile(join(dir,path));manifest.push({path,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});}
  await writeFile(join(root,'fixture-manifest.json'),JSON.stringify(manifest,null,2));
  console.log(dir);
} finally {await project.close();}
