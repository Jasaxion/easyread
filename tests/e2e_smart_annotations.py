"""Smart-annotation acceptance and visible quote anchoring, with no model calls.
Run with the project's Python dependencies, optional playwright and local Chrome.
All document writes and browser storage are disposable.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from easyread.chat import smart_annotations
from easyread.library.store import Workspace, empty_reader, write_json_atomic


def run():
    with tempfile.TemporaryDirectory(prefix='easyread-smart-anchors-') as tmp:
        home = Path(tmp)
        folder = home / 'library' / 'smart-sample'; folder.mkdir(parents=True)
        blocks = [
            {'id': 'markdown', 'type': 'para', 'page': 1, 'zh': '使用 **注意力** 和 [链接](https://example.test) 改善结果。', 'en': 'Use **attention** and [a link](https://example.test) to improve results.'},
            {'id': 'formula', 'type': 'para', 'page': 1, 'zh': '指标为 $x^2$，方差较低。', 'en': 'The metric is $x^2$ with low variance.'},
            {'id': 'spaces', 'type': 'para', 'page': 1, 'zh': '第一项 \n 第二项', 'en': 'First item \n second item'},
            {'id': 'fallback', 'type': 'para', 'page': 1, 'zh': '最初的中文说明。', 'en': 'The unchanged original finding.'},
            {'id': 'provenance', 'type': 'para', 'page': 1, 'zh': '原来背景中的重要词。', 'en': 'Original paragraph identity.', 'fixture_quote': '重要词'},
        ]
        paper = {'schema': 2, 'meta': {'title_en': 'Annotation anchor sample', 'title_zh': '标注定位样例'},
                 'blocks': blocks, 'translation': {'done_pages': [1], 'gaps_checked': True}}
        write_json_atomic(folder / 'paper.json', paper)
        write_json_atomic(folder / 'reader.json', empty_reader())
        write_json_atomic(folder / 'discussion.json', {'schema': 2, 'entries': []})
        ws = Workspace(folder)
        candidates = [smart_annotations._candidate(ws.id, {'anchor': b['id'], 'quote_zh': b.get('fixture_quote', b['zh']), 'quote_en': b['en'], 'type': 'highlight', 'color': 'yellow', 'note': 'Saved explanation'}, smart_annotations._sources(paper), 'fixture', '', '2026-01-01') for b in blocks]
        ws.apply_reader_ops([{'op': 'ai_annotation', 'annotation': a} for a in candidates])
        write_json_atomic(home / 'config.json', {'engine': 'none', 'check_updates': False})
        env = {**os.environ, 'PYTHONPATH': str(ROOT), 'EASYREAD_HOME': str(home), 'EASYREAD_LIBRARY': str(home / 'library'), 'EASYREAD_ACCESS_CODE': '', 'EASYREAD_SYSTEM_LANG': 'zh-CN', 'EASYREAD_LANG': 'zh'}
        server = subprocess.Popen([sys.executable, '-m', 'easyread', 'serve', '--port', '0'], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            line = server.stdout.readline(); assert 'http://' in line, line
            base = 'http://' + line.split('http://', 1)[1].split()[0]
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=shutil.which('google-chrome'), headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/read/' + ws.id)
                page.wait_for_selector('#paper:not(.landing) .blk')
                await_accept = '''async () => { await PR.api('/api/p/'+PR.pid+'/smart-annotation', {method:'POST',body:{action:'accept_all'}}); await PR.poll(); PR.applyMarks(); }'''
                page.evaluate(await_accept)
                ids = {b['id']: 'ai-note-' + a['annotation_id'].removeprefix('ai-') for b, a in zip(blocks, candidates)}
                for block, note in ids.items():
                    page.wait_for_function('(id)=>!!PR.state.reader.notes[id]', arg=note)
                    assert not page.evaluate('(id)=>PR.quoteLost(id)', note), block
                    assert page.locator(f'#b-{block} .zh mark[data-note="{note}"]').count(), block
                assert ws.load('reader')['notes'][ids['markdown']]['quote'] == blocks[0]['zh'], 'rendering must not rewrite saved quotes'
                # The DOM conversion remains precise: no other block receives that mark.
                assert page.locator(f'#b-formula mark[data-note="{ids["markdown"]}"]').count() == 0

                # Live reader edits must rebase after the acknowledgement, without reload.
                page.evaluate('''async () => { PR.commit({op:'edit',block:'fallback',zh:'完全不同的新译文。'}); PR.renderBlock('fallback'); await PR.flush(); await PR.poll(); PR.setView('both'); }''')
                note = ids['fallback']
                page.wait_for_function('(id)=>PR.state.reader.notes[id].side === "en"', arg=note)
                page.wait_for_selector(f'#b-fallback .en mark[data-note="{note}"]')
                assert not page.evaluate('(id)=>PR.quoteLost(id)', note)
                assert page.locator(f'#b-fallback .zh mark[data-note="{note}"]').count() == 0
                assert ws.load('reader')['notes'][note]['body'] == 'Saved explanation'

                # Reused IDs/phrases must not bypass a failed backend source-identity check.
                ws.update('paper', lambda p: p['blocks'][-1].update(en='A different source paragraph.', zh='不同背景中仍有重要词。'))
                smart_annotations.refresh_after_translation(ws)
                page.evaluate('async () => { await PR.poll(); }')
                stale_note = ids['provenance']
                page.wait_for_function('(id)=>PR.quoteLost(id)', arg=stale_note)
                assert '重要词' in page.locator('#b-provenance').inner_text()
                assert page.locator(f'#b-provenance mark[data-note="{stale_note}"]').count() == 0

                # Truly missing text remains unmarked; the warning does not invent a translation change.
                page.evaluate('''async () => {
                    PR.commit({op:'note',note:{id:'missing',anchor:'markdown',key:'markdown',side:'zh',quote:'This exact unrelated quotation is absent',kind:'highlight',color:'blue',ai_generated:true,body:'Keep me'}});
                    await PR.flush();PR.applyMarks();PR.renderMargin();
                }''')
                assert page.evaluate('PR.quoteLost("missing")')
                assert page.locator('#paper mark[data-note="missing"]').count() == 0
                assert '未找到对应文字' in page.locator('.card[data-note="missing"] .quote').first.evaluate('(el)=>getComputedStyle(el,"::after").content')
                exported = page.request.get(base + '/api/p/' + ws.id + '/export').body()
                offline = home / 'offline.html'; offline.write_bytes(exported)
                page.context.set_offline(True)
                page.goto(offline.as_uri()); page.wait_for_selector('#paper:not(.landing) .blk')
                for block, note in ids.items():
                    if block == 'provenance':
                        assert page.evaluate('(id)=>PR.quoteLost(id)', note)
                        assert page.locator(f'#paper mark[data-note="{note}"]').count() == 0
                    else:
                        assert not page.evaluate('(id)=>PR.quoteLost(id)', note)
                        assert page.locator(f'#paper mark[data-note="{note}"]').count()
                assert not errors, errors
                browser.close()
                print(json.dumps({'accepted': len(ids), 'markdown_math_whitespace': True, 'live_original_fallback': True,
                                  'saved_quotes_preserved': True, 'stale_provenance_not_misattached': True, 'missing_text_not_misattached': True, 'offline': True, 'browser_errors': errors}))
        finally:
            server.terminate(); server.wait(timeout=10); server.stdout.close()


if __name__ == '__main__':
    run()
