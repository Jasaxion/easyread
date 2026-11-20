const path = require('node:path');
const fs = require('node:fs');
const esbuild = require('esbuild');

async function build() {
  const root = path.resolve(__dirname, '..');
  const result = await esbuild.build({
    entryPoints: [path.join(root, 'easyread/web/src/workspace-editor.js')],
    outfile: path.join(root, 'easyread/web/vendor/workspace-editor.js'),
    bundle: true, minify: true, format: 'iife', target: 'es2022', legalComments: 'linked', metafile: true,
  });
  const packages = new Set(Object.keys(result.metafile.inputs).map(file => file.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//)?.[1]).filter(Boolean));
  const licenses = [...packages].sort().map(name => {
    const dir = path.join(root, 'node_modules', name);
    const info = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
    const license = fs.readdirSync(dir).find(file => /^license(?:[.-].*)?$/i.test(file));
    return name + ' ' + info.version + '\n\n' + (license ? fs.readFileSync(path.join(dir, license), 'utf8') : info.license);
  });
  fs.writeFileSync(path.join(root, 'easyread/web/vendor/workspace-editor.LICENSE.txt'), licenses.join('\n\n---\n\n'));
  await esbuild.build({
    entryPoints: [path.join(root, 'easyread/web/src/workspace-pdf.js')],
    outfile: path.join(root, 'easyread/web/vendor/workspace-pdf.js'),
    bundle: true, minify: true, format: 'esm', target: 'es2022', legalComments: 'linked',
    banner: { js: '/*! PDF.js (Apache-2.0) and its core-js compatibility layer (MIT). See /web/vendor/pdfjs/LICENSE and LICENSE_CORE_JS. */' },
  });
  const pdfjs = path.join(root, 'node_modules/pdfjs-dist');
  const assets = path.join(root, 'easyread/web/vendor/pdfjs');
  fs.mkdirSync(assets, { recursive: true });
  for (const name of ['cmaps', 'standard_fonts', 'wasm']) {
    fs.rmSync(path.join(assets, name), { recursive: true, force: true });
    fs.cpSync(path.join(pdfjs, name), path.join(assets, name), { recursive: true });
  }
  fs.copyFileSync(path.join(pdfjs, 'legacy/build/pdf.worker.min.mjs'), path.join(assets, 'pdf.worker.min.mjs'));
  fs.copyFileSync(path.join(pdfjs, 'LICENSE'), path.join(assets, 'LICENSE'));
  // The official text-layer rules must track PDF.js's generated span transforms.
  // No viewer shell, scripting, annotations, or unrelated image assets are included.
  const css = fs.readFileSync(path.join(pdfjs, 'web/pdf_viewer.css'), 'utf8');
  const start = css.indexOf('.textLayer {'), end = css.indexOf('.textLayerImages {', start);
  if (start < 0 || end < 0) throw new Error('PDF.js text layer stylesheet boundary changed');
  const textCss = await esbuild.transform(css.slice(start, end), { loader: 'css', minify: true, target: ['chrome110', 'firefox115', 'safari16'] });
  fs.writeFileSync(path.join(root, 'easyread/web/vendor/workspace-pdf.css'), '/* PDF.js text layer: Apache-2.0, see pdfjs/LICENSE. */\n' + textCss.code);
}
build().catch(error => { console.error(error); process.exitCode = 1; });
