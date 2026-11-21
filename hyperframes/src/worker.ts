import { pathToFileURL } from "node:url";
import { chdir } from "node:process";

// The job travels in argv, never stdin: the Hyperframes CLI blocks when stdin is
// an open pipe, so the child gets /dev/null instead.
const encoded = process.argv[2];
if (!encoded) throw Error("Missing job payload");
const command = JSON.parse(Buffer.from(encoded, "base64").toString("utf8")) as {
  file: string;
  args: string[];
  cwd: string;
};
// Detached workers must not outlive the adapter that owns their process group.
const parent = process.ppid;
setInterval(() => {
  if (process.ppid !== parent) process.exit(1);
}, 1000).unref();
chdir(command.cwd);
process.argv = [process.execPath, command.file, ...command.args];
await import(pathToFileURL(command.file).href);
