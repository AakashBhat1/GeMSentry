"""Access-key and user-credential verification, session cookie, and sign-out."""

import logging

from flask import Blueprint, g, jsonify, request

from gemsentry.crm.auth_service import crm_auth_service
from gemsentry.web.context import (
    ADMIN_DEFAULT_USER, _bearer_token, check_auth, is_auth_enabled,
)

logger = logging.getLogger("gemsentry")

auth_bp = Blueprint("auth", __name__)


def _set_auth_cookie(resp, token_value: str) -> None:
    resp.set_cookie(
        "gemsentry_token",
        token_value,
        max_age=86400 * 7,
        # The page never reads this cookie -- API calls use the bearer
        # header -- so keep it out of reach of any injected script.
        httponly=True,
        # Strict: this cookie authorises nothing but same-site navigation.
        samesite="Strict",
        # Set only over TLS when the server is behind the tunnel, so the
        # token is never sent in clear over someone else's network.
        secure=request.is_secure,
    )


@auth_bp.route("/api/auth/status", methods=["GET"])
def get_auth_status():
    """Return authentication status and current user profile (if authenticated)."""
    cfg = getattr(g, "server_config", None)
    user = getattr(g, "current_user", None)
    return jsonify({
        "auth_required": is_auth_enabled(cfg),
        "authenticated": user is not None,
        "user": user,
    })


@auth_bp.route("/api/auth/verify", methods=["POST"])
def verify_auth_token():
    """Validate {username, password} or {token} and issue an HttpOnly session cookie."""
    client_ip = request.remote_addr or "unknown"
    if crm_auth_service.is_rate_limited(client_ip):
        return jsonify({
            "valid": False,
            "error": "Too many failed sign-in attempts. Please wait a minute and try again.",
        }), 429

    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({"valid": False, "error": "JSON body must be an object."}), 400

    cfg = getattr(g, "server_config", None)
    username = data.get("username")
    if isinstance(username, str) and username.strip():
        password = data.get("password")
        if not isinstance(password, str) or not password:
            crm_auth_service.record_failed_attempt(client_ip)
            return jsonify({"valid": False, "error": "Username and password are required."}), 401

        user, session_token = crm_auth_service.authenticate(
            username.strip(), password, cfg=cfg, client_ip=client_ip,
        )
        if not user or not session_token:
            return jsonify({"valid": False, "error": "Invalid username or password."}), 401

        resp = jsonify({
            "valid": True,
            "message": "Authentication successful.",
            "session_token": session_token,
            "user": user,
        })
        _set_auth_cookie(resp, session_token)
        return resp

    token = str(data.get("token") or "").strip()
    if not is_auth_enabled(cfg):
        return jsonify({
            "valid": True,
            "message": "Authentication disabled.",
            "user": dict(ADMIN_DEFAULT_USER),
        })

    if check_auth(token, cfg):
        crm_auth_service.clear_failed_attempts(client_ip)
        user = dict(ADMIN_DEFAULT_USER)
        resp = jsonify({
            "valid": True,
            "message": "Authentication successful.",
            "session_token": token,
            "user": user,
        })
        _set_auth_cookie(resp, token)
        return resp

    session_user = crm_auth_service.resolve_session(token) if token else None
    if session_user is not None:
        crm_auth_service.clear_failed_attempts(client_ip)
        resp = jsonify({
            "valid": True,
            "message": "Authentication successful.",
            "session_token": token,
            "user": session_user,
        })
        _set_auth_cookie(resp, token)
        return resp

    crm_auth_service.record_failed_attempt(client_ip)
    return jsonify({"valid": False, "error": "Invalid company access key."}), 401


@auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    """Revoke the caller's session (if any) and expire the navigation cookie."""
    bearer = _bearer_token()
    if bearer:
        crm_auth_service.logout_token(bearer)
    cookie_token = request.cookies.get("gemsentry_token")
    if cookie_token and cookie_token != bearer:
        crm_auth_service.logout_token(cookie_token)

    resp = jsonify({"message": "Signed out."})
    resp.delete_cookie("gemsentry_token", path="/", samesite="Strict")
    return resp
