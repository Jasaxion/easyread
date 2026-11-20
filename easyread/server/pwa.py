"""Public application shell, never private documents or authentication state."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from functools import lru_cache

from ..app import config, i18n

PUBLIC = {"/sw.js", "/manifest.webmanifest", "/pwa/shell/library", "/pwa/shell/reader",
          "/web/css/pwa.css", "/web/js/common/pwa-window.js"}
PUBLIC.update(f"/web/pwa/icon-{size}.png" for size in (180, 192, 512))


_identity_lock = threading.Lock()
_readonly_nonce = secrets.token_hex(24)


def library_id(lib):
    # Persistent nonce also separates new servers/libraries at the same filesystem path.
    root = lib.root.resolve()
    marker = root / ".pwa-id"
    with _identity_lock:
        try:
            if not marker.exists():
                try:
                    with marker.open("x", encoding="ascii") as stream:
                        stream.write(secrets.token_hex(24))
                except FileExistsError:
                    pass
            nonce = marker.read_text(encoding="ascii")
        except (OSError, UnicodeError):
            # PWA identity must never make an otherwise readable library fail.
            # A read-only library can still cache for this server session.
            nonce = _readonly_nonce
    return hashlib.sha256((str(root) + ":" + nonce).encode()).hexdigest()[:24]


@lru_cache(maxsize=1)
def worker():
    web = config.WEB
    assets = set()
    for name in ("library.html", "reader.html"):
        assets.update(re.findall(r'(?:src|href)="(/web/[^"?#]+)"', (web / name).read_text(encoding="utf-8")))
    for folder in ("vendor", "stickers", "i18n", "pwa"):
        assets.update("/web/" + p.relative_to(web).as_posix() for p in (web / folder).rglob("*")
                      if p.is_file() and "whiteboard" not in p.relative_to(web).parts
                      and p.suffix in (".js", ".css", ".woff2", ".svg", ".png", ".webp", ".json"))
    # Shell routes are deliberately data-free; language dictionaries are bundled.
    files = sorted(assets)
    digest = hashlib.sha256()
    for url in files:
        digest.update(url.encode())
        digest.update((web / url[5:]).read_bytes())
    for name in ("reader.html", "library.html", "sw.js"):
        digest.update((web / name).read_bytes())
    script = (web / "sw.js").read_text(encoding="utf-8")
    return script.replace("/*PWA_CONFIG*/", "const BUILD = " + json.dumps(digest.hexdigest()[:16]) + ";\nconst ASSETS = " + json.dumps(files) + ";")


def build_id():
    return re.search(r'const BUILD = "([a-f0-9]+)"', worker()).group(1)


def get(handler, path):
    if path == "/sw.js":
        handler._send(200, worker().encode(), "text/javascript; charset=utf-8")
    elif path == "/manifest.webmanifest":
        handler._file(config.WEB / "pwa" / "manifest.webmanifest", ctype="application/manifest+json")
    elif path in ("/pwa/shell/library", "/pwa/shell/reader"):
        # No document ID, CSRF token, account state or server settings in these templates.
        page = (config.WEB / (path.rsplit("/", 1)[1] + ".html")).read_text(encoding="utf-8")
        handler._send(200, i18n.inject(page).encode(), "text/html; charset=utf-8")
    else:
        return False
    return True
