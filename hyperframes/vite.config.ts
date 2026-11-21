import {defineConfig} from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import {resolve} from "node:path";
export default defineConfig({root:resolve(import.meta.dirname,"web"),plugins:[react(),tailwindcss()],build:{outDir:resolve(import.meta.dirname,"../../dist/gewu-hyperframes/web"),emptyOutDir:true},resolve:{alias:{"@hyperframes/studio/styles.css":resolve(import.meta.dirname,"../../packages/studio/dist/styles.css")},dedupe:["react","react-dom"]}});
