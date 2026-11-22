const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, 'easyread/web', file), 'utf8');
test('browser library exposes research/workspaces and tabs-mode integration alongside current resources', () => {
  const html = read('library.html');
  for (const feature of ['href="/research"', 'href="/workspaces"', 'common/tabs-mode.js', 'id="libraryAskBtn"', 'library-search.js', 'pdf-export.js', 'settings-cloud.js', 'settings-custom-apis.js', 'translation-task.js']) assert.ok(html.includes(feature), feature);
  assert.ok(html.indexOf('common/markup.js') < html.indexOf('library/app.js'));
  assert.ok(html.indexOf('common/util.js') < html.indexOf('common/research-task-monitor.js'));
  assert.match(read('css/library.css'), /\.library-workspaces[^}]*display: flex/);
});
test('research and shell entrypoints load real resources in dependency order and keep EasyRead branding', () => {
  for (const file of ['library.html', 'research.html', 'tabs.html']) {
    const html = read(file);
    for (const match of html.matchAll(/(?:src|href)="(\/web\/[^"?#]+)"/g)) {
      assert.ok(fs.existsSync(path.join(root, 'easyread', match[1])), `${file}: ${match[1]}`);
    }
    assert.ok(!html.includes('PaperRead'));
    assert.ok(!html.includes('paperread-mark.png'));
  }
  const research = read('research.html'), tabs = read('tabs.html');
  for (const view of ['board','explore','candidates','summaries','retrieval','comparisons','keywords','progress','path']) assert.ok(research.includes(`data-view="${view}"`));
  for (const dependency of ['common/desktop-bridge.js','common/confirm.js','common/pick-folder.js','common/markup.js','research/progress.js','research/retrieval.js','research/path-map.js','common/research-task-monitor.js']) assert.ok(research.indexOf(dependency) < research.indexOf('research/workspace.js'));
  assert.ok(tabs.indexOf('common/tabs-session.js') < tabs.indexOf('js/tabs.js'));
});
