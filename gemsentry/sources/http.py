"""Shared HTTP fetching for portal adapters.

Portal listing pages are plain server-rendered HTML, so a verified urllib GET
is both faster and far cheaper than launching a browser. Adapters use
:func:`fetch_html` first and only fall back to Playwright when a page really
needs JavaScript.
"""

import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from gemsentry.constants import logger

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20

# Hosts whose certificate SAN lists only the apex name. A request to
# ``www.<host>`` fails hostname verification even though the apex URL works.
# mahatenders.gov.in is the current example (SAN: mahatenders.gov.in only).
APEX_ONLY_TLS_HOSTS = frozenset({
    "mahatenders.gov.in",
})

# Patch point for unit tests.
_urlopen = urllib.request.urlopen


def _certifi_cafile():
    """Return certifi's CA bundle path, or None if the package is absent."""
    try:
        import certifi
    except ImportError:
        return None
    try:
        return certifi.where()
    except Exception:
        return None


def build_verified_ssl_context(cafile=None):
    """Verified TLS context.

    Prefers certifi's CA bundle so clients with an incomplete system store
    (older Windows installs missing GlobalSign Root R46, used by
    mahatenders.gov.in) still validate the chain. Falls back to the platform
    store when certifi is not installed.
    """
    path = cafile if cafile is not None else _certifi_cafile()
    if path:
        return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def canonicalize_request_url(url: str) -> str:
    """Rewrite ``www.`` aliases that the portal certificate does not cover.

    Does not disable verification. Hosts not in :data:`APEX_ONLY_TLS_HOSTS`
    are returned unchanged, including their ``www.`` form.
    """
    if not url:
        return url
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.startswith("www."):
        return url
    apex = host[4:]
    if apex not in APEX_ONLY_TLS_HOSTS:
        return url

    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        prefix = userinfo + "@"
    else:
        hostport, prefix = netloc, ""
    # IPv6 literals are bracketed; none of the apex-only hosts are IPv6.
    if hostport.startswith("["):
        return url
    if ":" in hostport:
        _hostname, port = hostport.rsplit(":", 1)
        new_hostport = f"{apex}:{port}"
    else:
        new_hostport = apex
    return urllib.parse.urlunparse(parsed._replace(netloc=prefix + new_hostport))


# Verified TLS is the default and should stay that way.
_SSL_CTX = build_verified_ssl_context()

# Opt-out context for the handful of portals that serve a self-signed chain
# (BHEL is the current example). Enabling it is a per-source decision in
# config/sources.json -- never a global default -- and it is logged every time
# so an unverified fetch is always visible in the scrape log.
_UNVERIFIED_CTX = ssl.create_default_context()
_UNVERIFIED_CTX.check_hostname = False
_UNVERIFIED_CTX.verify_mode = ssl.CERT_NONE


def fetch_html(url: str, timeout: int = DEFAULT_TIMEOUT, label: str = "",
               verify_tls: bool = True) -> Optional[str]:
    """GET ``url`` and return decoded HTML, or ``None`` if the request failed.

    ``verify_tls=False`` skips certificate validation for that one request.
    Only set it for a public, read-only listing on a portal whose chain is
    genuinely broken -- it forfeits protection against interception.
    """
    tag = label or url
    request_url = canonicalize_request_url(url)
    context = _SSL_CTX
    if not verify_tls:
        logger.warning("[%s] TLS verification disabled for this source (%s)", tag, request_url)
        context = _UNVERIFIED_CTX

    request = urllib.request.Request(
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
        },
    )
    try:
        with _urlopen(request, context=context, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        logger.warning("[%s] HTTP %s fetching %s", tag, exc.code, request_url)
    except ssl.SSLError as exc:
        logger.warning("[%s] TLS error fetching %s: %s", tag, request_url, exc)
    except Exception as exc:
        logger.warning("[%s] fetch failed for %s: %s", tag, request_url, exc)
    return None
