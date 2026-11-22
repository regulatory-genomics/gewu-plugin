import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const root = process.env.GEWU_RUNTIME_ROOT;
const require = createRequire(join(root, 'engine/cli/package.json'));
const sharp = require('sharp');
await sharp({create: {width: 4, height: 4, channels: 3, background: 'blue'}}).png().toBuffer();
const scene = mkdtempSync(join(process.cwd(), 'hyperframes-smoke-'));
function run(exe, args) {
  const result = spawnSync(exe, args, {cwd: scene, encoding: 'utf8', timeout: 90_000});
  if (result.error || result.status !== 0) throw new Error(`${exe}: ${result.error ?? result.stderr}`);
  return result.stdout;
}
try {
  run(join(root, 'bin/ffmpeg'), ['-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=330:duration=2', 'tone.wav']);
  writeFileSync(join(scene, 'index.html'), `<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#103b58;color:white"><div id="main" data-composition-id="main" data-start="0" data-duration="2" data-width="640" data-height="360">HyperFrames smoke<audio id="sound" src="tone.wav" data-start="0" data-duration="2" data-track-index="1"></audio></div></body></html>`);
  const cli = join(root, 'engine/cli/cli.js');
  run(process.execPath, [cli, 'snapshot', scene, '--at', '0.5', '--no-end', '--keep', '--output', join(scene, 'frames')]);
  const video = join(scene, 'smoke.mp4');
  run(process.execPath, [cli, 'render', scene, '--output', video, '--fps', '24']);
  const streams = JSON.parse(run(join(root, 'bin/ffprobe'), ['-v','error','-show_streams','-of','json',video])).streams;
  if (!streams.some(s => s.codec_type === 'video' && s.width === 640 && s.height === 360)) throw new Error('invalid rendered video');
  if (!streams.some(s => s.codec_type === 'audio')) throw new Error('rendered video is missing audio');
  console.log('HyperFrames: snapshot and audio/video verified');
} finally {
  rmSync(scene, {recursive: true, force: true});
}
