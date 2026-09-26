"""
principal.py - Contrato Centralizado de Identidad, Scopes, Tokens de Servicio y CSRF (StreamVault v2)
Implementa la remediación estructural del Bloque 1 (V03, V04, V07, V08, V12).
"""

import os
import time
import hmac
import hashlib
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from typing import FrozenSet, Iterable, Optional, Tuple, Dict, Any

from core.config import settings
from core.security import verify_session_cookie
from db.connection import get_connection


@dataclass(frozen=True)
class Principal:
    subject: str
    kind: str  # "human", "service", "customer"
    scopes: FrozenSet[str]
    client_id: Optional[int] = None
    client_phone: str = ""
    mfa_verified: bool = False


# ContextVar aislado por petición asíncrona para ejecución de herramientas MCP (V08)
_current_mcp_principal: ContextVar[Optional[Principal]] = ContextVar(
    "current_mcp_principal", default=None
)


def set_current_mcp_principal(principal: Optional[Principal]):
    return _current_mcp_principal.set(principal)


def reset_current_mcp_principal(token) -> None:
    _current_mcp_principal.reset(token)


def get_current_mcp_principal() -> Optional[Principal]:
    return _current_mcp_principal.get()


def require_scope(principal: Optional[Principal], scope: str) -> None:
    """Verifica que el Principal autenticado posea el permiso (scope) requerido."""
    if principal is None:
        raise PermissionError("Identidad no autenticada")
    if "*" in principal.scopes or scope in principal.scopes:
        return
    raise PermissionError(f"Acción no autorizada: se requiere el permiso '{scope}'")


def token_digest(raw: str) -> str:
    """Calcula el digest SHA-256 de un token aleatorio de alta entropía."""
    return hashlib.sha256((raw or "").strip().encode("utf-8")).hexdigest()


def issue_service_token(
    subject: str,
    scopes: Iterable[str],
    ttl_seconds: int = 86400 * 90,
    ttl_days: Optional[int] = None,
) -> str:
    """Emite un token opaco de servicio y almacena únicamente su hash SHA-256 en la base de datos."""
    clean_subject = (subject or "").strip()
    if not clean_subject:
        raise ValueError("El subject del token de servicio no puede estar vacío")

    scope_list = sorted({s.strip() for s in scopes if s and s.strip()})
    if not scope_list:
        raise ValueError("Se debe especificar al menos un scope para el token de servicio")

    effective_ttl = (int(ttl_days) * 86400) if ttl_days is not None else int(ttl_seconds)
    raw_token = f"sv_svc_{secrets.token_urlsafe(32)}"
    digest = token_digest(raw_token)
    expires_at = time.time() + max(60, effective_ttl)
    scopes_csv = ",".join(scope_list)

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO service_tokens (token_hash, subject, scopes, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, NULL)
            """, (digest, clean_subject, scopes_csv, expires_at))
    finally:
        conn.close()

    return raw_token


def revoke_service_token(raw_or_hash_or_subject: str) -> bool:
    """Revoca un token de servicio por su valor en bruto, su hash SHA-256 o su subject."""
    candidate = (raw_or_hash_or_subject or "").strip()
    if not candidate:
        return False
    digest = candidate if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate.lower()) else token_digest(candidate)
    now = time.time()
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute("""
                UPDATE service_tokens
                SET revoked_at = ?
                WHERE (token_hash = ? OR subject = ?) AND revoked_at IS NULL
            """, (now, digest, candidate))
            return cur.rowcount > 0
    finally:
        conn.close()


def authenticate_service_token(raw_token: str) -> Optional[Principal]:
    """Autentica un token de servicio opaco o el token financiero dedicado de solo lectura."""
    candidate = (raw_token or "").strip()
    if not candidate:
        return None

    # 1. Prohibir explícitamente que claves maestras o contraseñas actúen como tokens de servicio (V07)
    forbidden_reused_secrets = {
        s for s in (
            getattr(settings, "SESSION_SECRET_KEY", ""),
            getattr(settings, "ADMIN_PASSWORD", ""),
            getattr(settings, "WEBHOOK_SECRET", ""),
            getattr(settings, "EVOLUTION_WEBHOOK_SECRET", ""),
            getattr(settings, "CHATWOOT_WEBHOOK_SECRET", ""),
            os.getenv("SESSION_SECRET_KEY", ""),
            os.getenv("ADMIN_PASSWORD", ""),
            os.getenv("WEBHOOK_SECRET", ""),
            "admin123",
            "mcp-super-secret-key-change-in-prod-2026",
        ) if s
    }
    if candidate in forbidden_reused_secrets:
        return None

    # 2. Verificar token financiero dedicado de solo lectura (FINANCE_API_TOKEN >= 24 chars) (V07)
    finance_token = (
        getattr(settings, "FINANCE_API_TOKEN", "") or
        os.getenv("FINANCE_API_TOKEN", "")
    ).strip()
    if (
        finance_token
        and len(finance_token) >= 24
        and finance_token not in forbidden_reused_secrets
        and secrets.compare_digest(candidate, finance_token)
    ):
        return Principal(
            subject="service:finance-digest",
            kind="service",
            scopes=frozenset({"finance:read"}),
            mfa_verified=False,
        )

    # 3. Buscar en tabla service_tokens por su hash SHA-256
    digest = token_digest(candidate)
    now = time.time()
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT subject, scopes, expires_at, revoked_at
            FROM service_tokens
            WHERE token_hash = ?
        """, (digest,)).fetchone()
        if row:
            if row["revoked_at"] is not None or float(row["expires_at"]) <= now:
                return None
            parsed_scopes = frozenset(
                s.strip() for s in str(row["scopes"] or "").split(",") if s.strip()
            )
            return Principal(
                subject=str(row["subject"]),
                kind="service",
                scopes=parsed_scopes,
                mfa_verified=False,
            )

        # 4. Verificar si es un token OAuth 2.0 válido emitido por el servidor (no revocado ni usado)
        oauth_row = conn.execute("""
            SELECT client_id, user_id, expires_at, scope, client_phone, used_at, revoked_at
            FROM oauth_tokens
            WHERE access_token_hash = ? OR access_token = ?
        """, (digest, candidate)).fetchone()
        if (
            oauth_row
            and oauth_row["revoked_at"] is None
            and oauth_row["used_at"] is None
            and float(oauth_row["expires_at"]) > now
        ):
            raw_scope = str(oauth_row["scope"] or "mcp").strip()
            scopes_set = {s.strip() for s in raw_scope.replace(",", " ").split() if s.strip()}
            c_phone = str(oauth_row["client_phone"] or "").strip()
            if c_phone:
                return Principal(
                    subject=f"customer:{c_phone}",
                    kind="customer",
                    scopes=frozenset(scopes_set | {"mcp:client"}),
                    client_phone=c_phone,
                    mfa_verified=False,
                )
            return Principal(
                subject=f"oauth:{oauth_row['user_id']}",
                kind="service",
                scopes=frozenset(scopes_set | {"mcp:admin", "secrets:create", "finance:read"}),
                mfa_verified=True,
            )
    except Exception:
        return None
    finally:
        conn.close()

    return None


def authenticate_request(request: Any) -> Optional[Principal]:
    """Extrae y verifica el Principal desde una petición HTTP (cookie de sesión 2FA o Bearer token)."""
    cookies = getattr(request, "cookies", {}) or {}
    headers = getattr(request, "headers", {}) or {}

    session_cookie = cookies.get("session_token") or cookies.get("mcp_session")
    session_user = verify_session_cookie(session_cookie)
    if session_user:
        return Principal(
            subject=session_user,
            kind="human",
            scopes=frozenset({
                "*",
                "secrets:create",
                "finance:read",
                "payments:reverse",
                "mcp:admin",
            }),
            mfa_verified=True,
        )

    auth_header = (headers.get("Authorization") or headers.get("authorization") or "").strip()
    bearer_token = ""
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()

    x_api_key = (headers.get("X-API-KEY") or headers.get("x-api-key") or "").strip()
    token_candidate = bearer_token or x_api_key
    if token_candidate:
        return authenticate_service_token(token_candidate)

    return None


def generate_csrf_token(session_user: str) -> str:
    """Genera un token CSRF determinista por usuario y clave de sesión."""
    secret = (getattr(settings, "SESSION_SECRET_KEY", "") or os.getenv("SESSION_SECRET_KEY", "")).encode("utf-8")
    msg = f"csrf:v2:{session_user.strip().lower()}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def verify_csrf_token(session_user: str, supplied_token: Optional[str]) -> bool:
    """Verifica en tiempo constante un token CSRF ligado al usuario de la sesión activa."""
    if not session_user or not supplied_token:
        return False
    expected = generate_csrf_token(session_user)
    return secrets.compare_digest(expected, supplied_token.strip())
