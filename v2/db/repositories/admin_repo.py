import time
import hashlib
import secrets
from typing import Optional, Dict, Any

from db.connection import get_connection
from core.security import hash_password, verify_password


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").strip().encode("utf-8")).hexdigest()


def create_or_update_admin(username: str, password: str, totp_secret: str = ""):
    clean_user = username.strip().lower()
    p_hash, salt = hash_password(password.strip())
    conn = get_connection()
    try:
        with conn:
            existing = conn.execute("SELECT id, totp_secret FROM admin_users WHERE lower(username) = ?", (clean_user,)).fetchone()
            if existing:
                secret_to_use = totp_secret if totp_secret else existing["totp_secret"]
                conn.execute("""
                    UPDATE admin_users 
                    SET password_hash = ?, salt = ?, totp_secret = ?
                    WHERE lower(username) = ?
                """, (p_hash, salt, secret_to_use, clean_user))
            else:
                conn.execute("""
                    INSERT INTO admin_users (username, password_hash, salt, totp_secret)
                    VALUES (?, ?, ?, ?)
                """, (clean_user, p_hash, salt, totp_secret))
    finally:
        conn.close()


def bootstrap_admin_once(username: str, password: str, totp_secret: str = "") -> Dict[str, Any]:
    """Crea el usuario administrador inicial únicamente si no existe (O02).
    Si ya existe, lanza RuntimeError para impedir sobrescrituras accidentales.
    """
    clean_user = (username or "").strip().lower()
    clean_pass = (password or "").strip()
    if not clean_user or len(clean_pass) < 8 or clean_pass.lower() in {"admin123", "password", "12345678"}:
        raise ValueError("Credenciales de bootstrap inválidas o inseguras")

    if get_admin_user(clean_user) is not None:
        raise RuntimeError("Administrador existente; usar cambio de clave explícito")

    p_hash, salt = hash_password(clean_pass)
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO admin_users (username, password_hash, salt, totp_secret)
                VALUES (?, ?, ?, ?)
            """, (clean_user, p_hash, salt, totp_secret))
        return get_admin_user(clean_user) or {}
    finally:
        conn.close()


def create_password_reset_token(username: str, ttl_seconds: int = 900, requester_ip: str = "") -> str:
    """Genera un token de recuperación de un solo uso sin modificar la contraseña actual (V02)."""
    clean_user = (username or "").strip().lower()
    raw_token = secrets.token_urlsafe(32)
    digest = _token_digest(raw_token)
    expires_at = time.time() + max(60, int(ttl_seconds))

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO password_reset_tokens (token_hash, username, expires_at, used_at, requester_ip)
                VALUES (?, ?, ?, NULL, ?)
            """, (digest, clean_user, expires_at, (requester_ip or "")[:64]))
    finally:
        conn.close()
    return raw_token


def invalidate_password_reset_token(raw_token: str) -> None:
    """Invalida un token de recuperación cuando falla la entrega por el canal seguro (V02)."""
    digest = _token_digest(raw_token)
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM password_reset_tokens WHERE token_hash = ?", (digest,))
    finally:
        conn.close()


def consume_password_reset_token_and_update_password(raw_token: str, new_password: str) -> bool:
    """Consume atómicamente el token de recuperación y actualiza la contraseña en la misma transacción (V02)."""
    clean_pass = (new_password or "").strip()
    if len(clean_pass) < 8 or clean_pass.lower() in {"admin123", "password", "12345678"}:
        return False

    digest = _token_digest(raw_token)
    now = time.time()
    p_hash, salt = hash_password(clean_pass)

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT username FROM password_reset_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
        """, (digest, now)).fetchone()
        if row is None:
            conn.rollback()
            return False

        username = str(row["username"]).strip().lower()
        cur_token = conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
        """, (now, digest, now))
        if cur_token.rowcount != 1:
            conn.rollback()
            return False

        conn.execute("""
            UPDATE admin_users
            SET password_hash = ?, salt = ?, telegram_otp = '', telegram_otp_expiry = 0
            WHERE lower(username) = ?
        """, (p_hash, salt, username))
        try:
            conn.execute("""
                UPDATE admin_sessions
                SET revoked_at = ?
                WHERE lower(username) = ? AND revoked_at IS NULL
            """, (now, username))
        except Exception:
            pass
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def get_admin_user(username: str) -> Optional[Dict[str, Any]]:
    clean_user = username.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM admin_users WHERE lower(username) = ?", (clean_user,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_admin_credentials(username: str, password: str) -> bool:
    user = get_admin_user(username)
    if not user:
        return False
    return verify_password(password.strip(), user["password_hash"], user["salt"])


def set_telegram_otp(username: str, otp: str, duration_seconds: int = 300):
    clean_user = username.strip().lower()
    expiry = time.time() + duration_seconds
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE admin_users 
                SET telegram_otp = ?, telegram_otp_expiry = ?
                WHERE lower(username) = ?
            """, (otp.strip(), expiry, clean_user))
    finally:
        conn.close()


def verify_telegram_otp(username: str, otp: str) -> bool:
    clean_user = username.strip().lower()
    user = get_admin_user(clean_user)
    if not user:
        return False
    stored_otp = user.get("telegram_otp")
    expiry = user.get("telegram_otp_expiry", 0)

    if not stored_otp or time.time() > expiry:
        return False

    if secrets.compare_digest(stored_otp, otp.strip()):
        conn = get_connection()
        try:
            with conn:
                conn.execute("UPDATE admin_users SET telegram_otp = '', telegram_otp_expiry = 0 WHERE lower(username) = ?", (clean_user,))
        finally:
            conn.close()
        return True
    return False
