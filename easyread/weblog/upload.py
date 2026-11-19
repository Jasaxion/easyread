"""Offline HTML snapshots: private notes must not be published or fetched as URLs."""
import base64
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from . import snapshot

MAX_HTML = 8 * 1024 * 1024
# Unknown wrappers are unwrapped, while active/foreign content is discarded entirely.
SAFE_TAGS = set("html head title body article main section div span p br hr h1 h2 h3 h4 h5 h6 blockquote pre code ul ol li dl dt dd strong b em i u s del mark sub sup table thead tbody tfoot tr th td caption figure figcaption img a".split())
DROP_TAGS = "script noscript style link base meta iframe frame frameset object embed form input button textarea select template svg math video audio source track canvas"


def validate(html, title="", source_id=""):
    if not isinstance(html, str) or not html.strip():
        raise ValueError("html must be a nonempty string")
    if len(html.encode("utf-8")) > MAX_HTML:
        raise ValueError("HTML exceeds 8 MiB")
    if not isinstance(title, str) or len(title) > 500 or any(ord(c) < 32 for c in title):
        raise ValueError("title must be a string up to 500 characters without control characters")
    if not isinstance(source_id, str) or len(source_id) > 512 or any(ord(c) < 32 for c in source_id):
        raise ValueError("source_id must be an opaque string up to 512 characters")


class OfflineAssets(snapshot.Assets):
    def get(self, url):
        return None


def prepare(html, title="", source_id=""):
    validate(html, title, source_id)
    soup = BeautifulSoup(html, "html.parser")
    original_title = soup.title.get_text() if soup.title else "Uploaded document"
    for tag in list(soup.select(DROP_TAGS.replace(" ", ","))):
        if tag.parent is not None:
            tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.name not in SAFE_TAGS:
            tag.unwrap()
            continue
        keep = {}
        if tag.name == "a":
            href = str(tag.get("href", ""))
            parsed = urlsplit(href)
            if href.startswith("#") or (parsed.scheme.lower() in ("http", "https", "mailto", "tel") and not parsed.username and not parsed.password):
                keep["href"] = href
        elif tag.name == "img":
            src = str(tag.get("src", ""))
            header, comma, payload = src.partition(",")
            if header.lower() not in ("data:image/png;base64", "data:image/jpeg;base64", "data:image/gif;base64", "data:image/webp;base64") or not comma:
                tag.replace_with(soup.new_string("[Image omitted]"))
                continue
            try:
                base64.b64decode(payload, validate=True)
            except ValueError:
                tag.replace_with(soup.new_string("[Invalid image omitted]"))
                continue
            keep.update(src=src, alt=str(tag.get("alt", "")))
        elif tag.name in ("td", "th"):
            for attr in ("colspan", "rowspan"):
                value = str(tag.get(attr, ""))
                if value.isdigit() and 1 <= int(value) <= 100:
                    keep[attr] = value
        tag.attrs = keep
    if not soup.html:
        soup = BeautifulSoup("<html><head></head><body>" + str(soup) + "</body></html>", "html.parser")
    if not soup.head:
        soup.html.insert(0, soup.new_tag("head"))
    if soup.title:
        soup.title.decompose()
    tag = soup.new_tag("title")
    tag.string = title or original_title[:500]
    soup.head.append(tag)
    # No network, browser rendering or remote CSS/fonts/media, even when viewing the saved source.
    frozen, meta, blocks = snapshot.freeze(soup, "", assets=OfflineAssets(), min_text=1)
    secured = BeautifulSoup(frozen, "html.parser")
    secured.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"] = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-src 'none'; script-src 'none'")
    meta.update(url="", source="uploaded-html", source_id=source_id, snapshot_external_assets=[])
    return str(secured), meta, blocks
