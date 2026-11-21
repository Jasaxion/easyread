const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  let dialog, copied;
  const el = (tag, attrs = {}) => Object.assign({ tag, children: [], value: '',
    append(...items) { this.children.push(...items); }, showModal() { this.open = true; },
  }, attrs);
  const W = { modal: () => (dialog = el('dialog')), input: (value, attrs) => el('input', { value, ...attrs }),
    field: (label, input, node) => { node.append(input); return input; },
    button: (text, onclick) => el('button', { text, onclick }),
  };
  const PR = { workspaces: W, el, t: text => text, token: 'BROWSER-CSRF-MUST-NOT-BE-COPIED',
    copyText: async text => { copied = text; }, toast() {} };
  const context = vm.createContext({ window: { PR }, location: { origin: 'https://library.example.com' }, URL });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../easyread/web/js/workspaces/library-access.js'), 'utf8'), context);
  W.libraryAccess.open();
  const all = node => [node, ...node.children.flatMap(all)];
  return { W, PR, dialog, all: all(dialog), copied: () => copied };
}

test('remote library setup uses full existing API token placeholders, never reads or stores real credentials', async () => {
  const { dialog, all, copied, PR } = fixture();
  assert.equal(dialog.open, true);
  const inputs = all.filter(n => n.tag === 'input');
  assert.equal(inputs.length, 1); assert.equal(inputs[0].type, 'url');
  const example = all.find(n => n.tag === 'pre');
  const value = JSON.parse(example.textContent).mcpServers.easyread;
  assert.equal(value.url, 'https://library.example.com/mcp');
  assert.equal(value.headers.Authorization, 'Bearer <EASYREAD_FULL_ACCESS_CODE>');
  assert.ok(all.some(n => n.text?.includes('整个文献库')));
  assert.ok(all.some(n => n.text?.includes('完整 API 的 Bearer')));
  await all.find(n => n.tag === 'button').onclick();
  assert.equal(copied(), example.textContent); assert.ok(!copied().includes(PR.token));
});

test('API helper supports reverse-proxy prefixes and rejects credentials/query/script URLs', async () => {
  const { all, copied } = fixture();
  const input = all.find(n => n.tag === 'input'), example = all.find(n => n.tag === 'pre');
  input.value = 'https://library.example.com/prefix/'; input.oninput();
  assert.equal(JSON.parse(example.textContent).mcpServers.easyread.url, 'https://library.example.com/prefix/mcp');
  for (const value of ['javascript:alert(1)', 'https://user:secret@host.test', 'https://host.test?token=x', 'https://host.test#x']) {
    input.value = value; input.oninput();
    assert.equal(example.textContent, '');
    await all.find(n => n.tag === 'button').onclick(); assert.equal(copied(), undefined);
  }
});
