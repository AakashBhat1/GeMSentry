"""Read-only GeM browser assistant."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import Error as PlaywrightError, sync_playwright

def is_gem_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "gem.gov.in" or host.endswith(".gem.gov.in"))

def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a manually logged-in GeM Chrome tab.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if not (args.cdp_url.startswith("http://127.0.0.1:") or args.cdp_url.startswith("http://localhost:")):
        print("Refusing remote CDP endpoints.", file=sys.stderr)
        return 2
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(args.cdp_url)
            pages = [page for context in browser.contexts for page in context.pages]
            if not pages:
                print("No tabs found. Open GeM in the launched Chrome window.", file=sys.stderr)
                return 1
            page = pages[-1]
            if not is_gem_url(page.url):
                print("Selected tab is not an HTTPS GeM page: " + page.url, file=sys.stderr)
                return 2
            controls = page.locator("input:visible, select:visible, textarea:visible, button:visible")
            items = []
            for i in range(min(controls.count(), 200)):
                try:
                    items.append(controls.nth(i).evaluate("""el => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type') || '',
                        name: el.getAttribute('name') || '',
                        id: el.id || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        text: el.tagName.toLowerCase() === 'button' ? (el.innerText || '').trim().slice(0, 100) : '',
                        disabled: Boolean(el.disabled)
                    })"""))
                except PlaywrightError:
                    pass
            result = {"title": page.title(), "url": page.url, "controls": items}
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot), full_page=True)
            print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else
                  f"Page: {result['title']}\nURL: {result['url']}\nVisible controls: {len(items)}\nRead-only inspection; no fields changed.")
            return 0
    except (PlaywrightError, OSError) as exc:
        print(f"Could not connect to Chrome: {exc}", file=sys.stderr)
        print("Launch Chrome with launch_chrome.ps1 and sign in manually.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
