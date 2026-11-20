const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const web = path.join(__dirname, '../../easyread/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');

test('real HTML loads new helpers before their consumers', () => {
  const reader = read('reader.html'), research = read('research.html');
  for (const [html, dependency, consumer] of [
    [reader, 'common/ai-language.js', 'reader/planner.js'],
    [reader, 'common/clipboard.js', 'reader/planner.js'],
    [reader, 'reader/deep-report-actions.js', 'reader/planner.js'],
    [reader, 'reader/reader-input.js', 'reader/ink.js'],
    [research, 'common/ai-language.js', 'research/workspace.js'],
    [research, 'research/citations.js', 'research/workspace.js'],
    [research, 'research/summary-editor.js', 'research/workspace.js'],
    [research, 'research/keyword-map.js', 'research/workspace.js']]) {
    assert.ok(html.indexOf(dependency) >= 0 && html.indexOf(dependency) < html.indexOf(consumer), dependency);
  }
});

test('all Markdown entry pages load the renderer and preserve block formatting', () => {
  for (const page of ['reader.html', 'library.html', 'research.html']) {
    const sources = [...read(page).matchAll(/<script src="\/web\/([^"]+)"><\/script>/g)].map(m => m[1]);
    const dependency = sources.indexOf('vendor/markdown-it.js');
    assert.ok(dependency >= 0 && dependency < sources.indexOf('js/common/markup.js'), page);
    const PR = {t: s => s, esc: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')};
    const context = {window: {PR}, URL};
    for (const source of sources.filter(s => ['vendor/markdown-it.js', 'js/common/markup.js'].includes(s))) {
      vm.runInNewContext(read(source), context);
    }
    assert.match(PR.mdBlocks('- first\n- second'), /<ul>[\s\S]*<li>first<\/li>[\s\S]*<li>second<\/li>/, page);
    assert.match(PR.mdBlocks('| A | B |\n| --- | --- |\n| 1 | 2 |'), /<table class="tbl">/, page);
  }
});

test('task monitor and tab menu no longer use fixed Traditional UI labels', () => {
  const source = read('js/common/research-task-monitor.js') + read('js/tabs.js');
  for (const old of ['論文心智圖', '跨論文比較', '智慧標註', '加入分頁群組', '查看結果', '關閉']) {
    assert.ok(!source.includes('t("' + old + '")'), old);
  }
});

test('saved AI language is explicit and never guessed from current UI for unknown metadata', () => {
  const PR = {TARGETS: [['zh','简体中文'],['zh-Hant','繁体中文'],['en','英语']], t:(s,v)=>s.replace('{language}',v?.language)};
  vm.runInNewContext(read('js/common/ai-language.js'), {window:{PR}});
  assert.equal(PR.aiOutputLanguage('zh'), '生成语言：简体中文');
  assert.equal(PR.aiOutputLanguage('zh-TW'), '生成语言：繁体中文');
  assert.equal(PR.aiOutputLanguage('en'), '生成语言：英语');
  assert.equal(PR.aiOutputLanguage(''), '');
  assert.equal(PR.aiOutputLanguage('unknown'), '');
});

test('keyword graph renders escaped labels and bounded numeric connectors', () => {
  const PR = {esc:s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')};
  vm.runInNewContext(read('js/research/keyword-map.js'), {window:{PR}});
  const html=PR.keywordMapGraph({nodes:[{id:'a',kind:'keyword',label:'<img onerror="attack()">'},{id:'b',kind:'paper',label:'Paper'}],edges:[{from:'a',to:'b'}]});
  assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));assert.match(html,/<path d="M/);assert.ok(!html.includes('NaN'));
});

test('HTTP clipboard fallback reports failure rather than false success and restores focus', async () => {
  let copied='',focused=false,removed=false,allowed=true;
  const input={value:'',style:{},select(){},setSelectionRange(){},remove(){removed=true;}};
  const PR={t:s=>s};
  const document={activeElement:{focus(){focused=true;}},createElement:()=>input,body:{appendChild(){}},execCommand:cmd=>{assert.equal(cmd,'copy');copied=input.value;return allowed;}};
  vm.runInNewContext(read('js/common/clipboard.js'),{window:{PR,getSelection:()=>null},document,navigator:{}});
  await PR.copyText('Exact source text');assert.equal(copied,'Exact source text');assert.ok(focused&&removed);
  allowed=false;await assert.rejects(PR.copyText('Not copied'),/手动复制/);
});
