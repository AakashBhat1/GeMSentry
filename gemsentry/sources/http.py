"""Shared HTTP fetching for portal adapters.

Portal listing pages are plain server-rendered HTML, so a verified urllib GET
is both faster and far cheaper than launching a browser. Adapters use
:func:`fetch_html` first and only fall back to Playwright when a page really
needs JavaScript.
"""

import ssl
import urllib.error
import urllib.request
from typing import Optional

from gemsentry.constants import logger

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20

# Verified TLS is the default and should stay that way.
_SSL_CTX = ssl.create_default_context()

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
    context = _SSL_CTX
    if not verify_tls:
        logger.warning("[%s] TLS verification disabled for this source (%s)", tag, url)
        context = _UNVERIFIED_CTX

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        logger.warning("[%s] HTTP %s fetching %s", tag, exc.code, url)
    except ssl.SSLError as exc:
        logger.warning("[%s] TLS error fetching %s: %s", tag, url, exc)
    except Exception as exc:
        logger.warning("[%s] fetch failed for %s: %s", tag, url, exc)
    return None
