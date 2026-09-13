import time
import secrets
from typing import Optional, Dict, Any

from db.connection import get_connection
from core.security import hash_password, verify_password

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
