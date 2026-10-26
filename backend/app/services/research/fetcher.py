"""SSRF-safe, polite page fetcher for company websites.

Security properties
* only http/https on the default ports; no credentials in URLs
* the hostname is resolved HERE, every returned address must be globally routable (no loopback,
  private, link-local, cloud-metadata, multicast, reserved), and the connection is pinned to the
  validated IP (TLS still verifies the certificate against the real hostname via SNI), so DNS
  rebinding between check and connect is impossible
* every redirect hop is re-validated; at most 4 hops
* hard caps on bytes and time; only text/HTML-like content types are read
* robots.txt is honoured (4xx = allowed, 5xx/unreachable = treated as disallowed for this run)
`allow_private` exists only so tests can reach a local fixture server; it is refused in production.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config import get_settings

DEFAULT_UA = "NeurogridResearchBot/1.0"
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4


class FetchError(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind  # blocked | robots | network | http | too_large | content_type | bad_url


def is_public_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return is_public_ip(addr.ipv4_mapped)
        if addr.sixtofour is not None and not is_public_ip(addr.sixtofour):
            return False
        if addr.teredo is not None:
            return False
    return bool(addr.is_global) and not addr.is_multicast and not addr.is_loopback and not addr.is_link_local


def resolve_public(host: str, allow_private: bool = False) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"DNS lookup failed for {host}", "network") from e
    ips: list[str] = []
    for fam, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise FetchError(f"No address for {host}", "network")
    if not allow_private:
        bad = [ip for ip in ips if not is_public_ip(ip)]
        if bad:
            raise FetchError(f"{host} resolves to a non-public address ({bad[0]}); refusing to connect", "blocked")
    return ips


def check_url(url: str, allow_private: bool = False) -> tuple[str, str, int, str]:
    """Validate a URL -> (scheme, ascii host, port, path+query). Raises FetchError(kind='bad_url'|'blocked')."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs are allowed", "bad_url")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials are not allowed", "bad_url")
    host = parts.hostname
    if not host:
        raise FetchError("URL has no host", "bad_url")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise FetchError("Invalid hostname", "bad_url") from e
    default = 443 if parts.scheme == "https" else 80
    port = parts.port or default
    if port != default and not allow_private:
        raise FetchError(f"Port {port} is not allowed", "blocked")
    if host.lower() in ("localhost",) and not allow_private:
        raise FetchError("localhost is not allowed", "blocked")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port, path


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)


def _build_request_target(scheme: str, ip: str, port: int, path: str) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    default = 443 if scheme == "https" else 80
    netloc = host if port == default else f"{host}:{port}"
    return f"{scheme}://{netloc}{path}"


def _request_once(scheme: str, host: str, ip: str, port: int, path: str, *, user_agent: str, timeout: float,
                  max_bytes: int, accept_any_type: bool):
    """One pinned request. Returns ("redirect", location) or ("done", status, ctype, body, truncated)."""
    target = _build_request_target(scheme, ip, port, path)
    host_header = host if port in (80, 443) else f"{host}:{port}"
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 8.0)), follow_redirects=False, verify=True) as client:
        with client.stream(
            "GET", target,
            headers={"Host": host_header, "User-Agent": user_agent,
                     "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1", "Accept-Encoding": "gzip"},
            extensions={"sni_hostname": host},
        ) as resp:
            status = resp.status_code
            if status in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise FetchError("Redirect without Location", "http")
                return ("redirect", urljoin(f"{scheme}://{host_header}{path}", loc))
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if status >= 400:
                return ("done", status, ctype, b"", False)
            if not accept_any_type and ctype and ctype not in ALLOWED_TYPES:
                raise FetchError(f"Content type {ctype} is not text", "content_type")
            chunks: list[bytes] = []
            size, truncated = 0, False
            for chunk in resp.iter_bytes():
                if size + len(chunk) > max_bytes:
                    chunks.append(chunk[: max_bytes - size])
                    truncated = True
                    break
                size += len(chunk)
                chunks.append(chunk)
            return ("done", status, ctype, b"".join(chunks), truncated)


def fetch(url: str, *, user_agent: str = DEFAULT_UA, timeout: float = 15.0, max_bytes: int = MAX_BYTES,
          accept_any_type: bool = False, allow_private: bool | None = None) -> FetchResult:
    allow_private = get_settings().allow_private_fetch if allow_private is None else allow_private
    current = url
    redirects: list[str] = []
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = check_url(current, allow_private)
        ips = resolve_public(host, allow_private)  # every hop is validated again
        outcome = None
        last_err: Exception | None = None
        for ip in ips[:3]:
            try:
                outcome = _request_once(scheme, host, ip, port, path, user_agent=user_agent, timeout=timeout,
                                        max_bytes=max_bytes, accept_any_type=accept_any_type)
                break
            except FetchError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, OSError) as e:
                last_err = e
        if outcome is None:
            raise FetchError(f"Could not fetch {host}: {type(last_err).__name__ if last_err else 'unknown error'}", "network")
        if outcome[0] == "redirect":
            redirects.append(current)
            current = outcome[1]
            continue
        _, status, ctype, body, truncated = outcome
        return FetchResult(url, current, status, ctype, body, truncated, redirects)
    raise FetchError("Too many redirects", "http")


class RobotsCache:
    """Per-run robots.txt cache. Conservative: an unreachable or 5xx robots.txt means 'do not fetch'."""

    def __init__(self, user_agent: str = DEFAULT_UA, enabled: bool = True):
        self.ua = user_agent
        self.enabled = enabled
        self._cache: dict[str, urllib.robotparser.RobotFileParser | bool] = {}

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin)
        entry = self._cache[origin]
        if entry is True:
            return True
        if entry is False:
            return False
        return entry.can_fetch(self.ua, url)

    def _load(self, origin: str):
        try:
            res = fetch(origin + "/robots.txt", user_agent=self.ua, timeout=8.0, max_bytes=300_000, accept_any_type=True)
        except FetchError as e:
            return False if e.kind in ("network", "http", "too_large") else True
        if res.status >= 500:
            return False
        if res.status >= 400:
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(res.body.decode("utf-8", "replace").splitlines())
        return rp
