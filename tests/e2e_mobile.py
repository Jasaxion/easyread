"""Focused mobile reader smoke. Uses a disposable library/profile and local Chrome.
Run: .venv/bin/python tests/e2e_mobile.py (optional playwright dependency).
This is browser emulation, not Android WebAPK or physical stylus certification.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'mobile-review'


def seed(home):
    folder = home / 'library' / 'mobile-sample'
    folder.mkdir(parents=True)
    text = 'Mobile selection preserves this quotation and its source. ' * 5
    paper = {'schema': 2, 'meta': {'title_en': 'Mobile reading sample', 'title_zh': '移动端阅读样例', 'page_count': 1},
             'translation': {'done_pages': [1], 'gaps_checked': True},
             'blocks': [{'id': f'p{i}', 'type': 'para', 'page': 1, 'en': text,
                         'zh': '选中文字后可以划线或写笔记。手写笔书写，手指翻页，原有批注保持不变。' * 5} for i in range(25)]}
    reader = {'schema': 2, 'rev': 0, 'edits': {}, 'ink': {}, 'stickers': {}, 'notes': {
        'sample-note': {'id': 'sample-note', 'anchor': 'p0', 'key': 'p0', 'kind': 'note',
                        'body': 'Sample paragraph note', 'quote': '选中文字', 'color': 'yellow'}}, 'paper_note': {}}
    for name, data in {'paper': paper, 'reader': reader, 'discussion': {'schema': 2, 'entries': []},
                       'item': {'tags': [], 'status': 'reading'}}.items():
        (folder / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False))
    from bs4 import BeautifulSoup
    from easyread.weblog.snapshot import freeze
    html, meta, blocks = freeze(BeautifulSoup('<html><head><title>Mobile web sample</title></head><body><p>' + ('Archived webpage selections keep their original quotation. ' * 20) + '</p></body></html>', 'html.parser'), 'https://example.test/mobile')
    web = home / 'library' / 'mobile-web'; web.mkdir()
    (web / 'source.html').write_text(html)
    for name, data in {'paper': {'schema': 2, 'meta': meta, 'blocks': blocks, 'translation': {'done_pages': [1], 'gaps_checked': True}},
                       'reader': {'schema': 2, 'rev': 0, 'notes': {}, 'edits': {}, 'ink': {}, 'stickers': {}},
                       'discussion': {'schema': 2, 'entries': []}}.items():
        (web / f'{name}.json').write_text(json.dumps(data))
    (home / 'config.json').write_text(json.dumps({'engine': 'none', 'check_updates': False}))
    return folder


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='easyread-mobile-') as tmp:
        home = Path(tmp)
        folder = seed(home)
        env = {**os.environ, 'EASYREAD_HOME': str(home), 'EASYREAD_LIBRARY': str(home / 'library'), 'EASYREAD_ACCESS_CODE': ''}
        log = (home / 'server.log').open('w+')
        server = subprocess.Popen([sys.executable, '-m', 'easyread', 'serve', '--port', '0'], cwd=ROOT,
                                  env=env, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            line = server.stdout.readline()
            assert 'http://' in line, line
            base = 'http://' + line.split('http://', 1)[1].split()[0]
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=shutil.which('google-chrome'), headless=True)
                context = browser.new_context(viewport={'width': 1024, 'height': 768}, is_mobile=True, has_touch=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/read/mobile-sample')
                page.wait_for_selector('#paper:not(.landing) .blk')
                page.evaluate('document.fonts.ready')
                page.wait_for_function('PR.marginWide() && document.body.classList.contains("margin-wide")')
                expect(page.locator('#margin [data-note="sample-note"]')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.screenshot(path=str(OUT / 'tablet-notes.png'))

                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_function('!PR.marginWide()')
                page.locator('[data-act="settings"]').tap()
                page.locator('[data-p="marginLayout"][data-v="inline"]').tap()
                expect(page.locator('.inline-notes [data-note="sample-note"]')).to_be_visible()
                page.locator('[data-act="settings"]').tap()
                page.reload()
                page.wait_for_selector('#paper:not(.landing) .blk')
                expect(page.locator('.inline-notes [data-note="sample-note"]')).to_be_visible()

                def select(pid='p1', end=12):
                    return page.evaluate('''([id,end]) => {
                        const el=document.querySelector('#b-'+id+' .zh');
                        el.scrollIntoView({block:'center'});
                        const node=el.firstChild, range=document.createRange();
                        range.setStart(node,0);range.setEnd(node,end);
                        getSelection().removeAllRanges();getSelection().addRange(range);
                        return getSelection().toString();
                    }''', [pid, end])

                selected = select()
                page.wait_for_selector('#selbar.touch-selection.open')
                # A selection-handle update, without any mouseup/showSelectionBar call.
                selected = select(end=16)
                page.wait_for_timeout(250)
                rect = page.locator('#selbar').bounding_box()
                assert rect['x'] >= 0 and rect['x'] + rect['width'] <= 391 and rect['y'] + rect['height'] <= 845
                page.screenshot(path=str(OUT / 'phone-selection.png'))
                page.locator('#selbar [data-s="highlight"][data-color="yellow"]').tap()
                page.wait_for_function('(q) => PR.myNotes().some(n=>n.kind==="highlight" && n.quote===q)', arg=selected)
                page.wait_for_timeout(300)
                assert not page.locator('#selbar').evaluate('(el)=>el.classList.contains("open")')
                assert page.locator('#b-p1 mark.hl').count() > 0
                selected = select('p2', 10)
                page.wait_for_selector('#selbar.open')
                page.locator('#selbar [data-s="note"]').tap()
                page.wait_for_function('(q) => PR.myNotes().some(n=>n.kind==="note" && n.quote===q)', arg=selected)
                editor = page.locator('.card.editing textarea:visible').first
                editor.fill('Mobile toolbar note')
                editor.press('Tab')
                page.wait_for_function('PR.store.pending === 0')

                page.evaluate('PR.setInkActive(true); PR.setInkInputMode("pen"); window.scrollTo(0,600)')
                page.wait_for_timeout(180)
                assert page.locator('#inkLayer').evaluate('(el)=>getComputedStyle(el).touchAction') == 'none'
                cdp = context.new_cdp_session(page)
                before = page.evaluate('({y:scrollY,n:Object.keys(PR.state.reader.ink||{}).length})')
                cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': 180, 'y': 390}]})
                for y in (360, 325, 290, 250):
                    cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': 180, 'y': y}]})
                cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                after = page.evaluate('({y:scrollY,n:Object.keys(PR.state.reader.ink||{}).length})')
                assert after['y'] > before['y'] + 80, (before, after)
                assert after['n'] == before['n']
                y0 = page.evaluate('scrollY')
                for kind, x, y, buttons in [('mousePressed', 110, 260, 1), ('mouseMoved', 135, 290, 1), ('mouseMoved', 155, 320, 1), ('mouseReleased', 155, 320, 0)]:
                    cdp.send('Input.dispatchMouseEvent', {'type': kind, 'pointerType': 'pen', 'x': x, 'y': y,
                                                        'button': 'left', 'buttons': buttons, 'force': .6 if buttons else 0})
                page.wait_for_function('(n)=>Object.values(PR.state.reader.ink||{}).filter(x=>!x.deleted).length>n', arg=before['n'])
                assert abs(page.evaluate('scrollY') - y0) < 2
                tools = page.locator('#inkTools').bounding_box()
                assert tools['x'] >= 0 and tools['x'] + tools['width'] <= 391
                page.screenshot(path=str(OUT / 'phone-ink-tools.png'))
                page.locator('[data-ink-close]').tap()
                assert not page.evaluate('PR.inkActive()')

                page.locator('.pwa-button').tap()
                expect(page.locator('#pwaDialog')).to_be_visible()
                assert page.locator('.pwa-diagnostics').inner_text()
                page.locator('.pwa-close').tap()
                manifest = cdp.send('Page.getAppManifest')
                assert not manifest.get('errors'), manifest
                parsed = json.loads(manifest['data'])
                assert parsed['id'] == '/' and parsed['display'] == 'standalone' and parsed['orientation'] == 'any'
                assert parsed['prefer_related_applications'] is False
                page.wait_for_function('PR.store.pending === 0')
                page.goto(base + '/read/mobile-web')
                page.wait_for_function('!!PR.webContentWindow?.()')
                def web_select(start, end):
                    return page.evaluate('''([start,end]) => {
                        const d=PR.webContentWindow().document, el=d.querySelector('p [data-easyread-side="en"]');
                        const nodes=[], w=d.createTreeWalker(el,NodeFilter.SHOW_TEXT); for(let n;(n=w.nextNode());) nodes.push(n);
                        const at=offset=>{for(const n of nodes){if(offset<=n.length)return [n,offset];offset-=n.length;}throw Error('short selection');};
                        const r=d.createRange(); r.setStart(...at(start)); r.setEnd(...at(end));
                        d.getSelection().removeAllRanges();d.getSelection().addRange(r);return d.getSelection().toString();
                    }''', [start, end])
                quote = web_select(0, 16)
                page.wait_for_selector('#selbar.touch-selection.open')
                page.locator('#selbar [data-s="highlight"][data-color="green"]').tap()
                page.wait_for_function('(q)=>PR.myNotes().some(n=>n.quote===q)', arg=quote)
                page.wait_for_function('PR.store.pending === 0')
                exported = page.request.get(base + '/api/p/mobile-web/export').body()
                offline = home / 'mobile-offline.html'; offline.write_bytes(exported)
                context.set_offline(True)
                page.goto(offline.as_uri()); page.wait_for_function('!!PR.webContentWindow?.()')
                assert page.evaluate('PR.store.mode') == 'static'
                quote = web_select(19, 29)
                page.wait_for_selector('#selbar.open')
                page.locator('#selbar [data-s="highlight"][data-color="blue"]').tap()
                page.wait_for_function('(q)=>PR.myNotes().some(n=>n.quote===q)', arg=quote)
                assert not errors, errors
                browser.close()
                result = {'tablet_margin': True, 'inline_notes_persist': True, 'selectionchange_toolbar': True,
                          'touch_annotation_actions': True, 'finger_scroll_without_ink': True,
                          'pen_stroke_without_scroll': True, 'mobile_tool_bounds': True, 'manifest_parses': True,
                          'iframe_selection': True, 'offline_html_annotations': True,
                          'browser_errors': errors, 'device_scope': 'Chromium mobile emulation; WebAPK/iOS install and physical stylus still require real-device checks'}
                (OUT / 'result.json').write_text(json.dumps(result, indent=2))
                print(json.dumps(result, ensure_ascii=False))
        finally:
            server.terminate(); server.wait(timeout=10); server.stdout.close(); log.close()


if __name__ == '__main__':
    run()
