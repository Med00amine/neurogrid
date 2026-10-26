"""Turn untrusted HTML into clean text + structure. Nothing here executes or follows page content."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.core.text import clean_text

MAX_PAGE_CHARS = 30_000

# Phrases that try to instruct an AI that reads the page. Flagged, never obeyed.
INJECTION_RE = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules?|messages?)"
    r"|you\s+are\s+now\s+(a|an|the)\b"
    r"|(reveal|print|show|output)\s+(your\s+|the\s+)?(system\s+prompt|hidden\s+instructions?)"
    r"|<\|?\s*(system|im_start|im_end)\s*\|?>"
    r"|\b(assistant|system)\s*:\s*(you|ignore|from now)"
    r"|(send|forward|email)\s+(this|the|all)\s+(data|conversation|contacts?)\s+to\b"
    r"|as\s+an\s+ai\s+(language\s+)?model,?\s+you\s+(must|should|will)",
    re.IGNORECASE,
)

LINK_KEYWORDS = {
    "about": 5, "company": 4, "who-we-are": 5, "team": 3, "services": 6, "solutions": 6, "what-we-do": 6, "expertise": 5,
    "capabilities": 5, "products": 6, "product": 5, "platform": 4, "industries": 3, "case-stud": 5, "projects": 5, "work": 3,
    "customers": 3, "careers": 5, "jobs": 5, "join": 3, "hiring": 5, "blog": 2, "news": 3, "impressum": 4, "imprint": 4,
    "contact": 2, "legal": 1, "mentions-legales": 4, "ueber-uns": 5, "a-propos": 5, "leistungen": 5, "chi-siamo": 5,
}
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3", ".css", ".js", ".ico", ".xml", ".docx", ".xlsx")


@dataclass
class Page:
    url: str
    title: str | None
    meta_description: str | None
    lang: str | None
    text: str
    headings: list[tuple[int, str]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute url, anchor text)
    injection_flag: bool = False
    sha256: str = ""


def _blocks(soup: BeautifulSoup) -> list[str]:
    for t in soup(["script", "style", "noscript", "svg", "canvas", "iframe", "template", "form", "button", "select", "option"]):
        t.decompose()
    tags = soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "figcaption", "td", "th", "dd", "dt", "summary", "address"])
    seen: set[str] = set()
    out: list[str] = []
    for el in tags:
        # avoid duplicating text of a <li> that contains <p>
        if el.name in ("li", "td", "th") and el.find(["p", "li", "h1", "h2", "h3", "h4"]):
            continue
        txt = clean_text(el.get_text(" ", strip=True))
        if txt and len(txt) >= 3 and txt not in seen:
            seen.add(txt)
            out.append(txt)
    if sum(len(b) for b in out) < 300:  # sites that put text in bare <div>/<span>
        for line in soup.get_text("\n", strip=True).splitlines():
            txt = clean_text(line)
            if txt and len(txt) >= 3 and txt not in seen:
                seen.add(txt)
                out.append(txt)
    return out


def parse_html(body: bytes, base_url: str) -> Page:
    soup = BeautifulSoup(body, "lxml")
    title = clean_text(soup.title.get_text() if soup.title else None, 300)
    meta = None
    for sel in ({"name": "description"}, {"property": "og:description"}, {"name": "twitter:description"}):
        tag = soup.find("meta", attrs=sel)
        if tag and tag.get("content"):
            meta = clean_text(tag["content"], 500)
            if meta:
                break
    lang = clean_text(soup.html.get("lang") if soup.html else None, 10)

    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        links.append((absolute, clean_text(a.get_text(" ", strip=True), 100) or ""))

    headings = [(int(h.name[1]), clean_text(h.get_text(" ", strip=True), 160) or "") for h in soup.find_all(["h1", "h2", "h3"])]
    headings = [(lvl, t) for lvl, t in headings if t]
    blocks = _blocks(soup)
    parts = []
    if title:
        parts.append(title)
    if meta and meta not in parts:
        parts.append(meta)
    parts.extend(blocks)
    text = "\n".join(parts)[:MAX_PAGE_CHARS]
    return Page(
        url=base_url, title=title, meta_description=meta, lang=lang, text=text, headings=headings, links=links,
        injection_flag=bool(INJECTION_RE.search(text)), sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def discover_links(page: Page, base_url: str, limit: int) -> list[str]:
    """Pick same-site pages most likely to say what the company does and whether it is hiring."""
    base = urlsplit(base_url)
    base_host = base.hostname.removeprefix("www.") if base.hostname else ""
    scored: dict[str, int] = {}
    for href, anchor in page.links:
        p = urlsplit(href)
        if p.scheme not in ("http", "https") or not p.hostname:
            continue
        if p.hostname.removeprefix("www.") != base_host:
            continue
        if p.path.lower().endswith(SKIP_EXT) or p.path in ("", "/"):
            continue
        hay = (p.path + " " + anchor).lower()
        score = max((w for k, w in LINK_KEYWORDS.items() if k in hay), default=0)
        if score <= 0 or p.path.count("/") > 4:
            continue
        clean = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
        scored[clean] = max(scored.get(clean, 0), score)
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], len(kv[0])))
    return [u for u, _ in ranked[:limit]]


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘(])|\n+")


def sentences(text: str, min_len: int = 25, max_len: int = 320) -> list[str]:
    out = []
    for s in _SENT_SPLIT.split(text):
        s = clean_text(s)
        if s and min_len <= len(s) <= max_len:
            out.append(s)
    return out
