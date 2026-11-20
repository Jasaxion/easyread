const path = require('node:path');
const fs = require('node:fs');
const esbuild = require('esbuild');

async function build() {
  const root = path.resolve(__dirname, '..');
  const out = path.join(root, 'easyread/web/vendor/whiteboard');
  fs.mkdirSync(out, { recursive: true });
  const result = await esbuild.build({
    absWorkingDir: root, entryPoints: { editor: 'easyread/web/src/whiteboard-editor.tsx' }, outdir: out,
    bundle: true, splitting: true, format: 'esm', minify: true, target: 'es2022', jsx: 'automatic', conditions: ['production'],
    define: { 'process.env.NODE_ENV': '"production"', 'process.env.IS_PREACT': '"false"' },
    loader: { '.woff2': 'file', '.woff': 'file', '.wasm': 'file' },
    chunkNames: 'chunks/[name]-[hash]', assetNames: 'assets/[name]-[hash]',
    legalComments: 'linked', metafile: true,
  });
  // Remove stale hashed chunks, but not the local font directory.
  const generated = new Set(Object.keys(result.metafile.outputs).map(file => path.resolve(root, file)));
  for (const dir of ['chunks', 'assets']) {
    const folder = path.join(out, dir);
    if (fs.existsSync(folder)) for (const name of fs.readdirSync(folder)) {
      const file = path.join(folder, name);
      if (!generated.has(file) && !generated.has(file.replace(/\.LEGAL\.txt$/, ''))) fs.unlinkSync(file);
    }
  }
  fs.cpSync(path.join(root, 'node_modules/@excalidraw/excalidraw/dist/prod/fonts'), path.join(out, 'fonts'), { recursive: true });
  const packages = new Set(Object.keys(result.metafile.inputs).map(file => file.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//)?.[1]).filter(Boolean));
  const licenses = [...packages].sort().map(name => {
    const dir = path.join(root, 'node_modules', name);
    const info = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
    const license = fs.readdirSync(dir).find(file => /^licen[sc]e(?:[.-].*)?$/i.test(file));
    return name + ' ' + info.version + '\n\n' + (license ? fs.readFileSync(path.join(dir, license), 'utf8') : info.license || 'See package for license');
  });
  licenses.push(fs.readFileSync(path.join(root, 'easyread/web/src/whiteboard-notices.txt'), 'utf8'));
  fs.writeFileSync(path.join(out, 'LICENSES.txt'), licenses.join('\n\n---\n\n'));
}
build().catch(error => { console.error(error); process.exitCode = 1; });
