const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const web = path.join(__dirname, '../../easyread/web');
const source = fs.readFileSync(path.join(web, 'js/common/i18n.js'), 'utf8');

function element(tag, attrs = {}, text = '', children = []) {
  const node = {tag, attrs, children, parentElement:null};
  node.text = {nodeValue:text, parentElement:node};
  for (const child of children) child.parentElement = node;
  node.getAttribute = name => node.attrs[name] ?? null;
  node.setAttribute = (name, value) => { node.attrs[name] = value; };
  node.querySelectorAll = () => children.flatMap(child => [child, ...child.querySelectorAll('*')]);
  node.closest = selector => {
    const match = selector.split(',').some(part => {
      const s = part.trim();
      if (s.startsWith('#')) return node.attrs.id === s.slice(1);
      if (s.startsWith('.')) return (node.attrs.class || '').split(' ').includes(s.slice(1));
      if (s.startsWith('[')) {
        const [, name, value] = s.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
        return name in node.attrs && (value === undefined || node.attrs[name] === value);
      }
      return node.tag === s;
    });
    return match ? node : node.parentElement?.closest(selector);
  };
  return node;
}

function load(lang, body, extra = {}, staticTitle = true) {
  const dict = lang === 'zh' ? {} : JSON.parse(fs.readFileSync(path.join(web, `i18n/${lang}.json`), 'utf8'));
  const style = new Map();
  const document = {body, title:staticTitle ? '研究探索｜EasyRead' : '设置',
    querySelector:selector => staticTitle && selector === 'title[data-i18n-title]' ? {} : null,
    documentElement:{lang:lang === 'zh' ? 'zh-CN' : lang, dataset:{langChoice:lang}, style:{setProperty:(key, value) => style.set(key, value)}},
    getElementById:() => ({textContent:JSON.stringify({...dict, ...extra})}),
    createTreeWalker(root) { const nodes = [root, ...root.querySelectorAll('*')].map(node => node.text); return {nextNode:() => nodes.shift()}; },
    addEventListener() {},
  };
  const context = {window:{PR:{}}, document, NodeFilter:{SHOW_TEXT:4}};
  vm.runInNewContext(source, context);
  return {PR:context.window.PR, document, style, dict};
}

test('static UI and CSS labels follow zh/en/zh-Hant without translating stored content', () => {
  for (const lang of ['zh','en','zh-Hant']) {
    const button = element('button', {title:'设置', 'aria-label':'关闭'}, ' 设置 ');
    const input = element('input', {value:'设置', placeholder:'设置'});
    const textarea = element('textarea', {placeholder:'设置'}, '设置');
    const contents = [element('script', {}, '设置'), element('style', {}, '设置'),
      element('article', {id:'paper'}, '设置', [element('img', {alt:'设置'})]),
      element('div', {class:'card'}, '设置'), element('h1', {class:'rw-quick-peek-title'}, '设置'),
      element('div', {class:'cm'}, '设置'), element('div', {class:'pn-rich'}, '设置'),
      element('div', {id:'list'}, '设置'), element('nav', {id:'side'}, '设置'), element('span', {class:'bar-titles'}, '设置'),
      element('div', {contenteditable:'true'}, '设置'), element('div', {'data-i18n-skip':''}, '设置')];
    const native = element('option', {translate:'no'}, '繁體中文');
    const body = element('body', {}, '', [button, input, textarea, ...contents, native]);
    const {PR, style, dict} = load(lang, body, {'繁體中文':'DO NOT TRANSLATE'});
    const expected = key => dict[key] || key;
    assert.equal(PR.lang, lang);
    assert.equal(load(lang, element('body')).document.title, expected('研究探索｜EasyRead'));
    assert.equal(load(lang, element('body'), {}, false).document.title, '设置');
    assert.equal(button.text.nodeValue, ` ${expected('设置')} `);
    assert.equal(button.attrs.title, expected('设置'));
    assert.equal(button.attrs['aria-label'], expected('关闭'));
    assert.equal(input.attrs.placeholder, expected('设置'));
    assert.equal(textarea.attrs.placeholder, expected('设置'));
    assert.equal(input.attrs.value, '设置');
    assert.equal(textarea.text.nodeValue, '设置');
    for (const node of contents) assert.equal(node.text.nodeValue, '设置', `${lang}: ${node.tag}`);
    assert.equal(contents[2].children[0].attrs.alt, '设置');
    assert.equal(native.text.nodeValue, '繁體中文');
    assert.equal(JSON.parse(style.get('--appendix-label')), expected('附录'));
    assert.equal(JSON.parse(style.get('--question-label')), expected('问　'));
    assert.equal(JSON.parse(style.get('--lost-quote-label')), expected('（未找到对应文字，请核对原文或译文）'));
    assert.equal(PR.t('作者：{authors}', {authors:'設置'}), expected('作者：{authors}').replace('{authors}', '設置'));
  }
});

test('recognized status defaults localize by ID while custom and renamed builtin labels remain intact', () => {
  for (const lang of ['zh','en','zh-Hant']) {
    const {PR, dict} = load(lang, element('body'));
    for (const [id, label, source] of [['unread','待閱讀','待阅读'], ['unread','To read','待阅读'], ['reading','閱讀中','阅读中'], ['done','Completed','已完成']]) {
      const status = {id, label, builtin:true};
      assert.equal(PR.researchStatusLabel(status), dict[source] || source);
      assert.equal(status.label, label);
    }
    for (const status of [{id:'unread', label:'自己的狀態', builtin:true}, {id:'custom', label:'待閱讀', builtin:false}, {id:'reading', label:'设置', builtin:true}]) {
      assert.equal(PR.researchStatusLabel(status), status.label);
    }
  }
});

test('toast no longer runs DOM translation over interpolated content', () => {
  const util = fs.readFileSync(path.join(web, 'js/common/util.js'), 'utf8');
  const toast = util.slice(util.indexOf('PR.toast ='), util.indexOf('PR.errorText ='));
  assert.doesNotMatch(toast, /translateDom/);
});
