"""Authenticated Apps Script transport shared by sync, uploads and connection tests."""

from urllib.parse import urljoin, urlparse

import requests

APPS_SCRIPT_HOSTS = {"script.google.com", "script.googleusercontent.com"}


def is_apps_script_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
        return (parsed.scheme == "https" and parsed.hostname in APPS_SCRIPT_HOSTS
                and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment)
    except (ValueError, AttributeError):
        return False


def post_webhook(config: dict, payload: dict, *, timeout: int = 12) -> dict:
    url = (config.get("apps_script_url") or "").strip()
    secret = (config.get("webhook_secret") or "").strip()
    if not is_apps_script_url(url):
        raise ValueError("Configure an HTTPS Google Apps Script webhook URL.")
    if not secret:
        raise ValueError("Configure the webhook secret locally and in Apps Script first.")
    body = {"vendor_sheets": config.get("vendor_sheets", {}), **payload, "webhook_secret": secret}
    method = "POST"
    response = requests.post(url, json=body, timeout=timeout, allow_redirects=False)
    # ContentService normally redirects to a one-time googleusercontent URL.
    # Validate every hop and never forward credentials outside Google Apps Script.
    for _ in range(3):
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        url = urljoin(url, response.headers.get("Location", ""))
        if not is_apps_script_url(url):
            raise ValueError("Webhook returned an untrusted redirect.")
        if response.status_code in (301, 302, 303):
            method = "GET"
        if method == "POST":
            response = requests.post(url, json=body, timeout=timeout, allow_redirects=False)
        else:
            response = requests.get(url, timeout=timeout, allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError("Webhook returned too many redirects.")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Webhook returned an invalid response.")
    return data


def public_config(config: dict) -> dict:
    return {**{key: value for key, value in config.items() if key != "webhook_secret"},
            "webhook_secret_configured": bool(config.get("webhook_secret"))}
