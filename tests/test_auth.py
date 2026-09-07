"""Authentication, CSRF posture and safe-bind behaviour."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
import paths  # noqa: E402

TOKEN = "a-long-shared-secret"


# --------------------------------------------------------------------------
# Safe bind
# --------------------------------------------------------------------------

def test_default_host_is_loopback():
    assert paths.load_server_config()["host"] == "127.0.0.1"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", ""])
def test_loopback_binds_need_no_token(host):
    paths.require_safe_bind({"host": host, "auth_token": ""})


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_public_bind_without_a_token_is_refused(host):
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        paths.require_safe_bind({"host": host, "auth_token": ""})


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_public_bind_with_a_token_is_allowed(host):
    paths.require_safe_bind({"host": host, "auth_token": TOKEN})


# --------------------------------------------------------------------------
# Request authentication
# --------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        paths, "load_server_config",
        lambda: {"auth_token": TOKEN, "host": "127.0.0.1", "port": 5000,
                 "tenders_dir": "", "logs_dir": ""},
    )
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_api_rejects_an_anonymous_request(client):
    assert client.get("/api/keywords").status_code == 401


def test_api_accepts_the_bearer_header(client):
    resp = client.get("/api/keywords", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200


def test_api_rejects_a_wrong_token(client):
    resp = client.get("/api/keywords", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_query_string_token_is_no_longer_accepted(client):
    """?token= leaked the key into access logs, history and Referer headers."""
    assert client.get(f"/api/keywords?token={TOKEN}").status_code == 401


def test_cookie_authenticates_a_safe_navigation(client):
    client.set_cookie("gemsentry_token", TOKEN)
    assert client.get("/api/keywords").status_code == 200


def test_cookie_alone_cannot_drive_a_state_change(client):
    """The CSRF defence: mutating routes require the bearer header."""
    client.set_cookie("gemsentry_token", TOKEN)
    resp = client.post("/api/clear-workspace", json={"confirm": True})
    assert resp.status_code == 401


def test_bearer_header_still_drives_a_state_change(client):
    resp = client.post(
        "/api/clear-workspace",
        json={},  # no confirmation -> 400, but past the auth gate
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code != 401


def test_static_assets_are_reachable_before_sign_in(client):
    """The login modal lives in app.js; gating it would lock the user out."""
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/auth.js").status_code == 200


def test_auth_status_is_public(client):
    assert client.get("/api/auth/status").status_code == 200


# --------------------------------------------------------------------------
# Cookie issuance
# --------------------------------------------------------------------------

def _cookie_header(resp):
    return " ".join(resp.headers.getlist("Set-Cookie"))


def test_verify_issues_an_httponly_strict_cookie(client):
    resp = client.post("/api/auth/verify", json={"token": TOKEN})
    assert resp.status_code == 200
    header = _cookie_header(resp)
    assert "HttpOnly" in header
    assert "SameSite=Strict" in header


def test_cookie_is_not_marked_secure_over_plain_http(client):
    resp = client.post("/api/auth/verify", json={"token": TOKEN})
    assert "Secure" not in _cookie_header(resp)


def test_cookie_is_marked_secure_over_https(client):
    resp = client.post(
        "/api/auth/verify", json={"token": TOKEN}, base_url="https://localhost"
    )
    assert "Secure" in _cookie_header(resp)


def test_verify_rejects_a_bad_token_without_a_cookie(client):
    resp = client.post("/api/auth/verify", json={"token": "wrong"})
    assert resp.status_code == 401
    assert "gemsentry_token=" not in _cookie_header(resp).replace("gemsentry_token=;", "")


def test_logout_expires_the_cookie_without_credentials(client):
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert "gemsentry_token=;" in _cookie_header(resp)


def test_check_auth_rejects_empty_and_none(client):
    assert app_module.check_auth(None) is False
    assert app_module.check_auth("") is False


@pytest.mark.parametrize("url", ["/pyproject.toml", "/.git/config", "/config/company_profile.json",
                                  "/config/server_config.json", "/static/../paths.py"])
def test_repository_files_are_not_served(monkeypatch, url):
    monkeypatch.setattr(paths, "load_server_config", lambda: {"auth_token": ""})
    with app_module.app.test_client() as c:
        assert c.get(url).status_code == 404


@pytest.mark.parametrize("headers", [{"Host": "public.example"}, {"X-Forwarded-For": "203.0.113.1"},
                                       {"CF-Connecting-IP": "203.0.113.1"}, {"Forwarded": "for=203.0.113.1"}])
def test_unconfigured_auth_rejects_tunnel_requests(monkeypatch, headers):
    monkeypatch.setattr(paths, "load_server_config", lambda: {"auth_token": ""})
    with app_module.app.test_client() as c:
        assert c.get("/api/auth/status", headers=headers).status_code == 403


def test_private_png_requires_auth(client):
    assert client.get("/api/finalized/spec-sheet/private.png").status_code == 401


def test_authentication_does_not_enable_repository_static_access(client):
    assert client.get("/.git/config", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_auth_reads_configuration_once(client, monkeypatch):
    from unittest.mock import Mock
    loader = Mock(return_value={"auth_token": TOKEN})
    monkeypatch.setattr(paths, "load_server_config", loader)
    assert client.get("/missing", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    assert loader.call_count == 1
