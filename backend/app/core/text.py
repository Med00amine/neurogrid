"""Text helpers. Everything imported or fetched is untrusted, so it goes through here first."""

from __future__ import annotations

import re
import unicodedata

# characters that are invisible or can be used to smuggle headers / spoof text
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")

_LOCAL_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def strip_invisible(value: str) -> str:
    return value.translate(_INVISIBLE)


def clean_text(value: object, max_len: int | None = None) -> str | None:
    """Trim, drop control/invisible characters, collapse whitespace. Empty -> None."""
    if value is None:
        return None
    s = str(value)
    s = unicodedata.normalize("NFC", strip_invisible(s))
    s = _CONTROL.sub("", s)
    s = _WS.sub(" ", s).strip()
    if not s:
        return None
    return s[:max_len] if max_len else s


def normalize_email(raw: object) -> str | None:
    """Return the canonical lowercase address, or None if it is not a plausible single mailbox."""
    if raw is None:
        return None
    s = strip_invisible(str(raw)).strip().lower()
    if not s or len(s) > 254 or any(c in s for c in " \t\r\n,;<>()[]\\\""):
        return None
    if s.count("@") != 1:
        return None
    local, domain = s.split("@")
    if not local or len(local) > 64 or not _LOCAL_RE.match(local):
        return None
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(lbl) for lbl in labels):
        return None
    if not re.match(r"^[a-z]{2,}$|^xn--[a-z0-9-]+$", labels[-1]):
        return None
    return f"{local}@{domain}"


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1]


def csv_safe(value: object) -> object:
    """Neutralise spreadsheet formula injection in exported cells."""
    if isinstance(value, str) and value and (value[0] in "=+-@\t\r" or value.lstrip()[:1] in "=+-@"):
        return "'" + value
    return value


def norm_for_match(s: str) -> str:
    """Normalisation used to verify that an evidence quote really occurs in a source text."""
    s = unicodedata.normalize("NFKC", strip_invisible(s))
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    s = _WS.sub(" ", s).strip().casefold()
    return s


def quote_in_text(quote: str, text: str, min_chars: int = 12) -> bool:
    q = norm_for_match(quote)
    if len(q) < min_chars:
        return False
    return q in norm_for_match(text)


_GENERIC_TLDS = frozenset(
    {
        "com", "net", "org", "info", "biz", "edu", "gov", "mil", "int", "eu", "asia",
        # country-code TLDs that are in practice used as generic startup/tech domains
        "io", "ai", "co", "tv", "me", "fm", "ly", "cc", "gg", "sh", "so", "to", "ws", "vc", "ac", "gl", "gd", "im",
        "tech", "app", "cloud", "digital", "dev", "xyz", "online", "site", "store", "agency", "studio", "solutions",
        "systems", "software", "consulting", "company", "group", "global", "network", "services", "labs", "works",
        "design", "media", "ventures", "team", "space", "tools", "technology", "data", "software", "page", "one",
    }
)


def tld_country(domain: str) -> str | None:
    """Best-effort country from a ccTLD. Generic / ambiguous TLDs return None (-> unknown country)."""
    tld = domain.rsplit(".", 1)[-1].lower()
    if tld in _GENERIC_TLDS or len(tld) != 2 or not tld.isalpha():
        return None
    return "GB" if tld == "uk" else tld.upper()
