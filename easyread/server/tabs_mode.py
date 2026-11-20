"""Browser navigation into the optional tabs shell, never API/iframe requests."""
import re
from urllib.parse import urlencode, urlsplit

from ..app import prefs


def get(handler, path):
    if handler.headers.get('Sec-Fetch-Dest') != 'document':
        return False
    reader = re.fullmatch(r'/read/(?!library$)[A-Za-z0-9_-]+', path)
    workspace = path in ('/workspaces', '/web/workspaces.html')
    if path not in ('/', '/index.html') and not reader and not workspace:
        return False
    stored = prefs.load()
    ui = stored.get('ui') if isinstance(stored, dict) else {}
    features = ui.get('features') if isinstance(ui, dict) else {}
    if isinstance(features, dict) and features.get('multi_tabs') is False:
        return False
    query = urlsplit(handler.path).query
    target = path + ('?' + query if query else '')
    params = {'open': target} if reader or workspace else {'home': '1', 'library': target}
    # Browsers inherit the original fragment across this redirect. The shell
    # forwards that fragment to the reader/library without treating it as a tab ID.
    handler._redirect('/web/tabs.html?' + urlencode(params))
    return True
