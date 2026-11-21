import {mkdir,cp,writeFile,chmod,rm,readdir} from "node:fs/promises";
import {resolve,join} from "node:path";
import {spawnSync} from "node:child_process";
import {readFileSync} from "node:fs";
import {authoringDefinitions} from "./src/tools";
import {videoDefinitions} from "./src/video";
import {buildStudioTools,type StudioAgentToolsDeps} from "../../packages/studio/src/webmcp/useStudioAgentTools";

const root=resolve(import.meta.dirname,"../.."),out=join(root,"dist/gewu-hyperframes");
const ui=buildStudioTools({get current():StudioAgentToolsDeps{throw Error("Schema collection never executes tools");}}).map(({execute,...metadata})=>metadata);
await writeFile(join(import.meta.dirname,"ui-tools.json"),JSON.stringify(ui,null,2)+"\n");
await writeFile(join(import.meta.dirname,"tools.schema.json"),JSON.stringify({authoring:[...authoringDefinitions,...videoDefinitions],studio:ui},null,2)+"\n");
for(const command of [[process.execPath,"run","--cwd","packages/cli","build"],[process.execPath,"run","--cwd","plugins/gewu","build:web"]]) {
  const result=spawnSync(command[0]!,command.slice(1),{cwd:root,stdio:"inherit"});if(result.error)throw result.error;if(result.status!==0)throw Error(`${command.join(" ")} failed (${result.status}, ${result.signal})`);
}
await mkdir(join(out,"engine"),{recursive:true});
await cp(join(root,"packages/cli/dist"),join(out,"engine/cli"),{recursive:true});
await cp(join(root,"registry"),join(out,"registry"),{recursive:true,filter:source=>!source.includes("catalog-artifact")});
await cp(join(import.meta.dirname,"plugin.json"),join(out,"plugin.json"));
await cp(join(import.meta.dirname,"release.json"),join(out,"release.json"));
for(const file of ["README.md","INSTALL.md"])await cp(join(import.meta.dirname,file),join(out,file));
await cp(join(import.meta.dirname,"skills"),join(out,"skills"),{recursive:true});
await cp(join(import.meta.dirname,"tools.schema.json"),join(out,"tools.schema.json"));
await mkdir(join(out,"bin"),{recursive:true});
await cp(join(import.meta.dirname,"bin/hyperframes-gewu"),join(out,"bin/hyperframes-gewu"));
await chmod(join(out,"bin/hyperframes-gewu"),0o755);
const cliPackage=join(root,"packages/cli/package.json");
const original=JSON.parse(readFileSync(cliPackage,"utf8"));
const dependencies:Record<string,string>={};
for(const name of [...Object.keys(original.dependencies),"sharp","ajv"]) {
  const pkg=JSON.parse(readFileSync(join(root,"packages/cli/node_modules",name,"package.json"),"utf8"));
  if(pkg.name!==name||typeof pkg.version!=="string")throw Error(`Cannot pin runtime dependency ${name}`);
  dependencies[name]=pkg.version;
}
await writeFile(join(out,"engine/cli/package.json"),JSON.stringify({name:"@hyperframes/gewu-engine",version:original.version,private:true,type:"module",dependencies},null,2));
const installed=spawnSync("npm",["install","--omit=dev","--ignore-scripts","--no-audit","--no-fund"],{cwd:join(out,"engine/cli"),stdio:"inherit"});
if(installed.status!==0)throw Error(`npm install failed (${installed.status})`);
// GeWu rejects package/runtime trees with symlinks; `.bin` is the only one npm creates.
await rm(join(out,"engine/cli/node_modules/.bin"),{recursive:true,force:true});
for(const entry of await readdir(join(out,"engine/cli/node_modules"),{recursive:true,withFileTypes:true})){
  if(entry.isSymbolicLink())throw Error(`packaged dependency is a symlink: ${join(entry.parentPath,entry.name)}`);
}
console.log(`Staged GeWu Hyperframes at ${out}`);
