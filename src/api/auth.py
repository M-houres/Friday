"""认证中间件 —— API Key / JWT 双模式"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings

logger = logging.getLogger(__name__)

_DEV_JWT_SECRET = secrets.token_urlsafe(48)


SKIP_PATHS = {"/", "/panel"}
DEV_ONLY_SKIP_PATHS = {"/panel", "/docs", "/openapi.json", "/redoc"}
SKIP_PREFIXES = (
    "/api/v1/health",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
)


def is_public_path(path: str) -> bool:
    if path in SKIP_PATHS or path.startswith(SKIP_PREFIXES):
        return True
    if settings.environment != "prod" and path in DEV_ONLY_SKIP_PATHS:
        return True
    return False


def get_request_user_id(request: Request) -> str:
    state_user = getattr(request.state, "user", None)
    if isinstance(state_user, dict):
        return str(state_user.get("sub") or state_user.get("user_id") or "default")
    header_user = request.headers.get("X-User-Id", "").strip()
    if header_user:
        return header_user
    return "default"


def get_request_roles(request: Request) -> list[str]:
    state_user = getattr(request.state, "user", None)
    if isinstance(state_user, dict):
        roles = state_user.get("roles", [])
        if isinstance(roles, list) and roles:
            return [str(role) for role in roles]
    header_roles = request.headers.get("X-User-Roles", "").strip()
    if header_roles:
        return [role.strip() for role in header_roles.split(",") if role.strip()]
    return ["anonymous"]


def resolve_jwt_secret() -> str:
    secret = settings.jwt_secret.strip()
    if secret:
        return secret
    if settings.environment != "prod":
        return _DEV_JWT_SECRET
    return ""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if len(password) < 8:
        raise ValueError("PASSWORD_TOO_SHORT")
    resolved_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt.encode("utf-8"),
        120_000,
    )
    return digest.hex(), resolved_salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    if not salt or not expected_hash:
        return False
    actual_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(actual_hash, expected_hash)


def encode_jwt(payload: dict, secret: str, algorithm: str = "HS256") -> str:
    if algorithm != "HS256":
        raise ValueError("UNSUPPORTED_JWT_ALGORITHM")
    try:
        import jwt

        return jwt.encode(payload, secret, algorithm=algorithm)
    except ImportError:
        header = {"alg": algorithm, "typ": "JWT"}
        header_part = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def decode_jwt(token: str, secret: str, algorithm: str = "HS256") -> dict:
    if algorithm != "HS256":
        raise ValueError("UNSUPPORTED_JWT_ALGORITHM")
    try:
        import jwt

        return jwt.decode(token, secret, algorithms=[algorithm])
    except ImportError:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("INVALID_JWT")
        header_part, payload_part, signature_part = parts
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected_signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual_signature = _b64url_decode(signature_part)
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise ValueError("INVALID_JWT_SIGNATURE")
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        exp = payload.get("exp")
        if exp is not None and float(exp) < time.time():
            raise ValueError("JWT_EXPIRED")
        return payload


def issue_access_token(
    *,
    user_id: str,
    email: str,
    roles: list[str],
    name: str = "",
    expires_in_seconds: int = 30 * 24 * 60 * 60,
) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "user_id": user_id,
        "email": email,
        "name": name,
        "roles": roles,
        "iat": now,
        "exp": now + max(expires_in_seconds, 60),
    }
    return encode_jwt(payload, resolve_jwt_secret(), settings.jwt_algorithm)


def require_roles(request: Request, allowed_roles: set[str]):
    if settings.auth_mode == "none" and settings.environment != "prod":
        return
    roles = set(get_request_roles(request))
    if roles & allowed_roles:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


class AuthMiddleware(BaseHTTPMiddleware):
    """API Key / JWT 认证中间件

    模式选择 (通过环境变量 AUTH_MODE):
    - "none": 跳过所有认证 (默认，向后兼容)
    - "api_key": 验证 X-API-Key 头
    - "jwt": 验证 Authorization Bearer token (需要 PyJWT)
    """

    def __init__(self, app, auth_mode: str = "none", api_keys: set[str] | None = None,
                 jwt_secret: str = "", jwt_algorithm: str = "HS256"):
        super().__init__(app)
        self.auth_mode = auth_mode
        self.api_keys = api_keys or set()
        self.jwt_secret = jwt_secret
        self.jwt_algorithm = jwt_algorithm

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if is_public_path(path):
            return await call_next(request)

        if self.auth_mode == "none":
            return await call_next(request)

        if self.auth_mode == "api_key":
            api_key = request.headers.get("X-API-Key", "")
            if not api_key or not self._verify_api_key(api_key):
                raise HTTPException(status_code=401, detail="Invalid or missing API key")
            request.state.user = {
                "sub": request.headers.get("X-User-Id", "").strip() or f"api_key:{api_key[:8]}",
                "auth_mode": "api_key",
                "roles": get_request_roles(request),
            }
            logger.debug(f"API key authenticated: {api_key[:8]}...")

        elif self.auth_mode == "jwt":
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Missing Bearer token")
            token = auth_header[7:]
            payload = self._verify_jwt(token)
            if payload is None:
                raise HTTPException(status_code=401, detail="Invalid or expired token")
            payload.setdefault("roles", payload.get("roles", []))
            request.state.user = payload
            logger.debug(f"JWT authenticated: {payload.get('sub', 'unknown')}")

        return await call_next(request)

    def _verify_api_key(self, key: str) -> bool:
        return key in self.api_keys or self._constant_time_compare(key)

    def _constant_time_compare(self, key: str) -> bool:
        if not self.api_keys:
            return False
        for stored in self.api_keys:
            if hmac.compare_digest(key, stored):
                return True
        return False

    def _verify_jwt(self, token: str) -> Optional[dict]:
        try:
            return decode_jwt(token, self.jwt_secret, self.jwt_algorithm)
        except Exception as e:
            logger.warning(f"JWT verification failed: {e}")
            return None


class AuthConfig:
    """认证配置辅助"""

    @staticmethod
    def parse_keys(env_value: str) -> set[str]:
        """从逗号分隔的环境变量解析 API Keys"""
        if not env_value:
            return set()
        return {k.strip() for k in env_value.split(",") if k.strip()}

    @staticmethod
    def create_middleware() -> Optional[AuthMiddleware]:
        """根据配置创建认证中间件"""
        mode = getattr(settings, "auth_mode", "none")
        if mode == "none":
            return None

        if mode == "api_key":
            keys = AuthConfig.parse_keys(getattr(settings, "api_keys", ""))
            if not keys:
                logger.warning("API key auth enabled but no keys configured")
                return None
            return AuthMiddleware(app=None, auth_mode="api_key", api_keys=keys)

        if mode == "jwt":
            secret = resolve_jwt_secret()
            if not secret:
                logger.warning("JWT auth enabled but no secret configured")
                return None
            return AuthMiddleware(
                app=None, auth_mode="jwt",
                jwt_secret=secret,
                jwt_algorithm=getattr(settings, "jwt_algorithm", "HS256"),
            )

        return None
