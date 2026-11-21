# GeM Browser Assistant (MVP)

A separate local helper that attaches to a dedicated Chrome profile after you
sign in to GeM yourself. This first version is read-only: it checks that the
selected tab is on gem.gov.in and lists visible form controls without reading
their entered values.

## What it does

- Launches a dedicated Chrome profile with a localhost-only DevTools endpoint.
- Lets you sign in to GeM manually; credentials are not stored by this tool.
- Connects with Playwright over Chrome DevTools Protocol (CDP).
- Lists visible inputs, selects, textareas, and buttons.
- Can save a screenshot for local review.

## What it does not do

- Does not bypass CAPTCHA, bot protection, access controls, or GeM restrictions.
- Does not collect or store passwords, OTPs, cookies, or session tokens.
- Does not modify or submit bid forms.
- Does not click payment, confirmation, final-submit, OTP, DSC, or eSign controls.
- Does not upload documents.

Future form preparation should be limited to fields the user can review, with
final legal submission kept manual.

## Windows setup

Run these commands from the GeMSentry repository root in PowerShell:

    uv sync
    powershell -ExecutionPolicy Bypass -File .\tools\gem_browser_assistant\launch_chrome.ps1

In the new Chrome window, sign in to GeM yourself. Keep that window open.

Open another PowerShell window in the repository root:

    uv run python .\tools\gem_browser_assistant\assistant.py

Optional JSON output and screenshot:

    uv run python .\tools\gem_browser_assistant\assistant.py --json
    uv run python .\tools\gem_browser_assistant\assistant.py --screenshot .\logs\gem-current-page.png

The tool inspects the most recently opened tab. It refuses non-GeM pages and
remote CDP endpoints.

## Security notes

- CDP is bound to 127.0.0.1 only. Never expose port 9222 to a LAN or public network.
- The dedicated browser profile contains an authenticated GeM session. Treat it
  like a password: do not share, upload, or commit it.
- Close the dedicated Chrome window when finished.
