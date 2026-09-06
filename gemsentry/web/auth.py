"""Access-key verification, session cookie and sign-out."""

import logging

from flask import Blueprint, jsonify, request

from gemsentry.web.context import check_auth, is_auth_enabled

logger = logging.getLogger("gemsentry")

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/api/auth/status", methods=["GET"])
def get_auth_status():
    """Return whether authentication is currently active on the server."""
    return jsonify({"auth_required": is_auth_enabled()})


@auth_bp.route("/api/auth/verify", methods=["POST"])
def verify_auth_token():
    """Validate token and return an authentication session cookie."""
    data = request.json or {}
    token = (data.get("token") or "").strip()
    if not is_auth_enabled():
        return jsonify({"valid": True, "message": "Authentication disabled."})
    if check_auth(token):
        resp = jsonify({"valid": True, "message": "Authentication successful."})
        resp.set_cookie(
            "gemsentry_token",
            token,
            max_age=86400 * 7,
            # The page never reads this cookie -- API calls use the bearer
            # header from localStorage -- so keep it out of reach of any
            # injected script.
            httponly=True,
            # Strict: this cookie authorises nothing but same-site navigation.
            samesite="Strict",
            # Set only over TLS when the server is behind the tunnel, so the
            # token is never sent in clear over someone else's network.
            secure=request.is_secure,
        )
        return resp
    return jsonify({"valid": False, "error": "Invalid company access key."}), 401


@auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    """Expire the navigation cookie. It is HttpOnly, so only the server can."""
    resp = jsonify({"message": "Signed out."})
    resp.delete_cookie("gemsentry_token", path="/", samesite="Strict")
    return resp
