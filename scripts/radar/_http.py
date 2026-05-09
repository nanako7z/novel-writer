"""urllib helper for radar adapters.

stdlib only. Provides:
- get(url, ...) -> (status, text, headers) with charset detection
- get_json(url, ...) -> parsed JSON or raises
- per-host rate limit (>= 1s between hits to same host)
- UA pool (5 real-browser UAs rotated)
- macOS-friendly SSL context: probes a usable cafile so vanilla python.org
  Pythons (where ssl.get_default_verify_paths().cafile is None) can still
  verify certs without running Install Certificates.command.

No retries on 4xx; one retry on transient 5xx/timeouts. Caller decides what
to do with failure — adapters convert exceptions to PlatformRankings.failures
entries, never propagate.
"""
from __future__ import annotations

import gzip
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Optional

# 5 real browser UAs (rotated per request).
UA_POOL = [
    # macOS Chrome 124
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # macOS Safari 17
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Windows Chrome 124
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Windows Edge 124
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Windows Firefox 125
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

DEFAULT_TIMEOUT_SEC = 8
HOST_MIN_INTERVAL_SEC = 1.0
_LAST_HIT: dict[str, float] = {}

# Probe cafile in priority order. ssl.get_default_verify_paths() returns None
# on python.org's macOS framework Pythons that haven't run
# Install Certificates.command. Falling back to OS-provided bundles makes
# urllib usable without certifi.
_CAFILE_CANDIDATES = [
    os.environ.get("SSL_CERT_FILE") or "",
    os.environ.get("REQUESTS_CA_BUNDLE") or "",
    "/etc/ssl/cert.pem",                            # macOS LibreSSL
    "/private/etc/ssl/cert.pem",                    # macOS alternate path
    "/opt/homebrew/etc/openssl@3/cert.pem",         # Apple Silicon brew
    "/usr/local/etc/openssl@3/cert.pem",            # Intel brew
    "/etc/ssl/certs/ca-certificates.crt",           # Linux Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",             # Linux RHEL/CentOS
]


def _resolve_cafile() -> Optional[str]:
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.isfile(paths.cafile):
        return paths.cafile
    if paths.openssl_cafile and os.path.isfile(paths.openssl_cafile):
        return paths.openssl_cafile
    for p in _CAFILE_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


_SSL_CTX: Optional[ssl.SSLContext] = None
_SSL_INSECURE = False


def _ssl_context() -> ssl.SSLContext:
    global _SSL_CTX, _SSL_INSECURE
    if _SSL_CTX is not None:
        return _SSL_CTX
    cafile = _resolve_cafile()
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
    else:
        # No cafile found anywhere: fall back to unverified context with a
        # one-time stderr warning. This lets radar still run on machines
        # missing every standard CA bundle, with the user warned.
        ctx = ssl._create_unverified_context()  # noqa: SLF001
        _SSL_INSECURE = True
        print(
            "[radar/_http] warning: no CA bundle found "
            "(set SSL_CERT_FILE or run python's Install Certificates.command); "
            "TLS verification disabled for this run.",
            file=sys.stderr,
        )
    _SSL_CTX = ctx
    return ctx

CHARSET_META_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*['\"]?([A-Za-z0-9_\-]+)", re.IGNORECASE
)


def _rate_limit(host: str) -> None:
    last = _LAST_HIT.get(host, 0.0)
    elapsed = time.monotonic() - last
    if elapsed < HOST_MIN_INTERVAL_SEC:
        time.sleep(HOST_MIN_INTERVAL_SEC - elapsed)
    _LAST_HIT[host] = time.monotonic()


def _detect_encoding(content_type: str, body: bytes, fallback: str) -> str:
    # 1. Content-Type header
    m = re.search(r"charset\s*=\s*([A-Za-z0-9_\-]+)", content_type or "", re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # 2. <meta charset> in first 4KB
    head = body[:4096]
    m = CHARSET_META_RE.search(head)
    if m:
        return m.group(1).decode("ascii", errors="ignore").lower()
    return fallback


def get(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    headers: Optional[dict] = None,
    ua: Optional[str] = None,
    fallback_encoding: str = "utf-8",
) -> tuple[int, str, dict]:
    """Fetch URL, return (status, text, response_headers).

    Raises urllib.error.URLError / TimeoutError on transport problems —
    caller catches and reports.
    """
    parsed = urllib.parse.urlparse(url)
    _rate_limit(parsed.hostname or "")

    req_headers = {
        "User-Agent": ua or random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        # accept gzip — some servers (jjwxc) ignore identity and send gzip
        # anyway; we decompress below. deflate is stdlib via zlib.
        "Accept-Encoding": "gzip, deflate, identity",
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers, method="GET")
    ctx = _ssl_context() if url.lower().startswith("https://") else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        status = resp.status
        body = resp.read()
        resp_headers = dict(resp.headers.items())

    # Decompress per Content-Encoding header (or magic-byte sniff for gzip).
    enc_header = (resp_headers.get("Content-Encoding") or "").strip().lower()
    if enc_header == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    elif enc_header == "deflate":
        try:
            body = zlib.decompress(body)
        except zlib.error:
            try:
                body = zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error:
                pass

    enc = _detect_encoding(resp_headers.get("Content-Type", ""), body, fallback_encoding)
    try:
        text = body.decode(enc, errors="replace")
    except LookupError:
        text = body.decode(fallback_encoding, errors="replace")
    return status, text, resp_headers


def get_json(url: str, **kwargs) -> dict:
    """Fetch URL and parse JSON. Raises on non-2xx or parse failure."""
    status, text, _ = get(url, **kwargs)
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "non-2xx", {}, None)
    return json.loads(text)
