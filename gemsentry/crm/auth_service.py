"""CRM user authentication, scrypt password hashing, opaque sessions, and rate limiting."""

from __future__ import annotations

import collections
import datetime
import hashlib
import hmac
import importlib.util
import os
import re
import secrets
import threading
import time

import paths

# TODO(security): Add OAuth2/OIDC provider integration, MFA, and leaked-password detection if deployed to public multi-tenant cloud.

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SESSION_TTL_DAYS = 7
ALLOWED_ROLES = frozenset({"admin", "vendor", "member"})
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

PUBLIC_USER_FIELDS = (
    "id",
    "username",
    "display_name",
    "role",
    "company_id",
    "company_name",
    "disk_folder",
    "subfolder_scope",
    "is_active",
)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _sanitize_user(row: dict | None) -> dict | None:
    if not isinstance(row, dict):
        return None
    sanitized = {k: row.get(k) for k in PUBLIC_USER_FIELDS if k in row}
    # Ensure expected keys exist with safe defaults.
    sanitized.setdefault("id", "")
    sanitized.setdefault("username", "")
    sanitized.setdefault("display_name", sanitized["username"])
    sanitized.setdefault("role", "vendor")
    sanitized.setdefault("company_id", None)
    sanitized.setdefault("company_name", None)
    sanitized.setdefault("disk_folder", None)
    sanitized.setdefault("subfolder_scope", "")
    sanitized.setdefault("is_active", 1)
    if "has_password" in row:
        sanitized["has_password"] = bool(row["has_password"])
    elif "password_hash" in row:
        sanitized["has_password"] = bool(row.get("password_hash"))
    return sanitized


def _crm_service_available() -> bool:
    return importlib.util.find_spec("gemsentry.crm.service") is not None


class CRMAuthService:
    """Handles password hashing, opaque session tokens, user management, and login throttling."""

    def __init__(self, repo=None, max_failures: int = 15, window_seconds: int = 60):
        self._repo = repo
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, collections.deque[float]] = {}

    def _get_repo(self):
        if self._repo is not None:
            return self._repo
        from gemsentry.crm import crm_service
        return crm_service.repo

    def _ensure_ready(self) -> None:
        if self._repo is not None:
            return
        from gemsentry.crm import crm_service
        crm_service.ensure_initialized(run_migration=False)

    def _db_exists(self) -> bool:
        if self._repo is not None:
            return True
        if not _crm_service_available():
            return False
        repo = self._get_repo()
        explicit = getattr(repo, "_explicit_db_path", None)
        db_path = explicit or os.path.join(paths.DATA_DIR, "crm.db")
        return os.path.exists(db_path)

    # ------------------------------------------------------------------
    # Password hashing & verification
    # ------------------------------------------------------------------

    @staticmethod
    def hash_password(password: str) -> str:
        if not isinstance(password, str):
            raise ValueError("Password must be a string.")
        if not (8 <= len(password) <= 128):
            raise ValueError("Password must be between 8 and 128 characters.")
        salt = os.urandom(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=SCRYPT_DKLEN,
        )
        return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        if not isinstance(password, str) or not isinstance(stored_hash, str) or not stored_hash:
            return False
        parts = stored_hash.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        try:
            n = int(parts[1])
            r = int(parts[2])
            p = int(parts[3])
            if n <= 1 or r <= 0 or p <= 0:
                return False
            salt = bytes.fromhex(parts[4])
            expected = bytes.fromhex(parts[5])
            if not salt or not expected:
                return False
        except (ValueError, TypeError):
            return False

        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(derived, expected)

    # ------------------------------------------------------------------
    # Sliding-window failed-login rate limiter
    # ------------------------------------------------------------------

    def _prune_locked(self, key: str, now: float) -> collections.deque[float]:
        dq = self._failures.get(key)
        if dq is None:
            dq = collections.deque()
            self._failures[key] = dq
        cutoff = now - self._window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()
        return dq

    def is_rate_limited(self, client_ip: str) -> bool:
        key = (client_ip or "unknown").strip() or "unknown"
        now = time.monotonic()
        with self._lock:
            dq = self._prune_locked(key, now)
            return len(dq) >= self._max_failures

    def record_failed_attempt(self, client_ip: str) -> None:
        key = (client_ip or "unknown").strip() or "unknown"
        now = time.monotonic()
        with self._lock:
            dq = self._prune_locked(key, now)
            dq.append(now)

    def clear_failed_attempts(self, client_ip: str) -> None:
        key = (client_ip or "unknown").strip() or "unknown"
        with self._lock:
            self._failures.pop(key, None)

    # ------------------------------------------------------------------
    # Authentication & session lifecycle
    # ------------------------------------------------------------------

    def authenticate(
        self,
        username: str,
        password: str,
        cfg: dict | None = None,
        client_ip: str | None = None,
    ) -> tuple[dict | None, str | None]:
        if client_ip is not None and self.is_rate_limited(client_ip):
            return None, None

        uname = (username or "").strip() if isinstance(username, str) else ""
        if not uname or not isinstance(password, str) or not password:
            if client_ip is not None:
                self.record_failed_attempt(client_ip)
            return None, None

        self._ensure_ready()
        repo = self._get_repo()
        user = repo.get_user_by_username(uname)
        if not user or int(user.get("is_active", 1)) != 1:
            if client_ip is not None:
                self.record_failed_attempt(client_ip)
            return None, None

        stored_hash = (user.get("password_hash") or "").strip()
        if stored_hash:
            verified = self.verify_password(password, stored_hash)
        elif uname == "etspl":
            cfg_token = ((cfg or {}).get("auth_token") or "").strip()
            verified = bool(cfg_token) and hmac.compare_digest(password, cfg_token)
        else:
            verified = False

        if not verified:
            if client_ip is not None:
                self.record_failed_attempt(client_ip)
            return None, None

        if client_ip is not None:
            self.clear_failed_attempts(client_ip)

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        expires_at = (
            datetime.datetime.now(datetime.UTC)
            + datetime.timedelta(days=SESSION_TTL_DAYS)
        ).isoformat()
        repo.create_session(token_hash, user["id"], expires_at)
        return _sanitize_user(user), raw_token

    def resolve_session(self, raw_token: str) -> dict | None:
        if not isinstance(raw_token, str):
            return None
        token = raw_token.strip()
        if not token:
            return None
        if not self._db_exists():
            return None
        repo = self._get_repo()
        user = repo.get_session_user(_hash_token(token), _utcnow_iso())
        if not user or int(user.get("is_active", 1)) != 1:
            return None
        return _sanitize_user(user)

    def logout_token(self, raw_token: str) -> None:
        if not isinstance(raw_token, str):
            return
        token = raw_token.strip()
        if not token:
            return
        if not self._db_exists():
            return
        repo = self._get_repo()
        repo.delete_session(_hash_token(token))

    # ------------------------------------------------------------------
    # User administration
    # ------------------------------------------------------------------

    def list_users(self) -> list[dict]:
        self._ensure_ready()
        repo = self._get_repo()
        rows = repo.list_users()
        result = []
        for row in rows:
            clean = _sanitize_user(row)
            if clean is not None:
                result.append(clean)
        return result

    def upsert_user_account(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")

        self._ensure_ready()
        repo = self._get_repo()

        raw_id = str(payload.get("id") or "").strip()
        raw_username = str(payload.get("username") or "").strip()

        existing = None
        if raw_id:
            existing = repo.get_user_by_id(raw_id)
        if existing is None and raw_username:
            existing = repo.get_user_by_username(raw_username)

        username = raw_username or (existing["username"] if existing else "")
        if not username or not USERNAME_RE.match(username):
            raise ValueError("Username must be 1-64 characters using letters, numbers, '_', '.', or '-'.")

        user_id = raw_id or (existing["id"] if existing else username.lower())

        role = str(
            payload.get("role")
            if payload.get("role") is not None
            else (existing["role"] if existing else "vendor")
        ).strip().lower()
        if role not in ALLOWED_ROLES:
            raise ValueError("Role must be one of: admin, vendor, member.")

        if "company_id" in payload:
            company_id = (str(payload["company_id"]).strip() or None) if payload["company_id"] is not None else None
        else:
            company_id = existing.get("company_id") if existing else None

        if role != "admin" and not company_id:
            raise ValueError("Non-admin users must be assigned to a company.")
        if company_id is not None:
            if repo.get_company(company_id) is None:
                raise ValueError(f"Unknown company_id: {company_id}")

        if "display_name" in payload and payload["display_name"] is not None:
            display_name = str(payload["display_name"]).strip()[:120] or username
        else:
            display_name = (existing.get("display_name") if existing else None) or username

        if "subfolder_scope" in payload:
            subfolder_scope = str(payload["subfolder_scope"] or "").strip()[:120]
        else:
            subfolder_scope = str((existing.get("subfolder_scope") if existing else "") or "").strip()

        if "is_active" in payload:
            raw_active = payload["is_active"]
            if isinstance(raw_active, bool):
                is_active = 1 if raw_active else 0
            elif isinstance(raw_active, (int, float)):
                is_active = 1 if int(raw_active) != 0 else 0
            else:
                is_active = 0 if str(raw_active).strip().lower() in ("0", "false", "no") else 1
        else:
            is_active = int(existing.get("is_active", 1)) if existing else 1

        raw_password = payload.get("password")
        password_changed = False
        if raw_password is not None and str(raw_password) != "":
            password_hash = self.hash_password(str(raw_password))
            password_changed = True
        else:
            password_hash = (existing.get("password_hash") or "") if existing else ""

        saved = repo.upsert_user({
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "role": role,
            "company_id": company_id,
            "subfolder_scope": subfolder_scope,
            "password_hash": password_hash,
            "is_active": is_active,
        })

        if password_changed or is_active == 0:
            target_id = saved.get("id", user_id) if isinstance(saved, dict) else user_id
            repo.delete_sessions_for_user(target_id)

        sanitized = _sanitize_user(saved if isinstance(saved, dict) else None)
        if sanitized is None:
            refreshed = repo.get_user_by_id(user_id)
            sanitized = _sanitize_user(refreshed) or {
                "id": user_id,
                "username": username,
                "display_name": display_name,
                "role": role,
                "company_id": company_id,
                "subfolder_scope": subfolder_scope,
                "is_active": is_active,
            }
        sanitized["has_password"] = bool(password_hash)
        return sanitized


crm_auth_service = CRMAuthService()
