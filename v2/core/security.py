import os
import time
import hashlib
import secrets
import base64
from typing import Optional, Tuple, List
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag

from core.config import settings


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SESSION_SECRET_KEY)


def _hash_session_id(sid: str) -> str:
    return hashlib.sha256((sid or "").strip().encode("utf-8")).hexdigest()


BACKUP_MAGIC_HEADER = b"SVENC01"
BACKUP_SALT_LEN = 16
BACKUP_NONCE_LEN = 12
BACKUP_TAG_LEN = 16


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Genera hash seguro PBKDF2-SHA256 con salt."""
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Compara una contraseña con su hash almacenado en tiempo constante."""
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, stored_hash)


def create_session_cookie(username: str, ttl_seconds: int = 86400 * 7) -> str:
    """Crea una sesión autenticada con 2FA, persistiendo su hash en servidor para permitir revocación (V13)."""
    clean_user = (username or "").strip().lower()
    sid = secrets.token_urlsafe(24)
    sid_hash = _hash_session_id(sid)
    now = time.time()
    expires_at = now + max(60, int(ttl_seconds))

    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO admin_sessions (session_hash, username, mfa_verified, expires_at, last_seen_at, revoked_at)
                    VALUES (?, ?, 1, ?, ?, NULL)
                """, (sid_hash, clean_user, expires_at, now))
        finally:
            conn.close()
    except Exception:
        pass

    return _get_serializer().dumps(
        {"user": clean_user, "auth": True, "mfa": True, "sid": sid},
        salt="session-auth",
    )


def verify_session_cookie(cookie: Optional[str]) -> Optional[str]:
    """Verifica firma, expiración y estado de revocación en servidor de la cookie de sesión (V13)."""
    if not cookie:
        return None
    try:
        data = _get_serializer().loads(cookie, salt="session-auth", max_age=86400 * 7)
    except (SignatureExpired, BadSignature):
        return None

    if not isinstance(data, dict) or data.get("auth") is not True:
        return None

    username = str(data.get("user") or "").strip().lower()
    if not username:
        return None

    sid = str(data.get("sid") or "").strip()
    if sid:
        sid_hash = _hash_session_id(sid)
        try:
            from db.connection import get_connection
            conn = get_connection()
            try:
                row = conn.execute("""
                    SELECT username, expires_at, revoked_at
                    FROM admin_sessions
                    WHERE session_hash = ?
                """, (sid_hash,)).fetchone()
                if row is not None:
                    if row["revoked_at"] is not None or float(row["expires_at"]) <= time.time():
                        return None
                    with conn:
                        conn.execute(
                            "UPDATE admin_sessions SET last_seen_at = ? WHERE session_hash = ?",
                            (time.time(), sid_hash),
                        )
            finally:
                conn.close()
        except Exception:
            pass

    return username


def revoke_session_cookie(cookie: Optional[str]) -> bool:
    """Revoca inmediatamente una sesión activa en el servidor (V13)."""
    if not cookie:
        return False
    try:
        data = _get_serializer().loads(cookie, salt="session-auth", max_age=86400 * 30)
    except (SignatureExpired, BadSignature):
        return False

    sid = str((data or {}).get("sid") or "").strip()
    if not sid:
        return False

    sid_hash = _hash_session_id(sid)
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            with conn:
                cur = conn.execute("""
                    UPDATE admin_sessions
                    SET revoked_at = ?
                    WHERE session_hash = ? AND revoked_at IS NULL
                """, (time.time(), sid_hash))
                return cur.rowcount > 0
        finally:
            conn.close()
    except Exception:
        return False


def revoke_all_user_sessions(username: str) -> int:
    """Revoca todas las sesiones activas de un usuario tras cambio/recuperación de contraseña (V13)."""
    clean_user = (username or "").strip().lower()
    if not clean_user:
        return 0
    try:
        from db.connection import get_connection
        conn = get_connection()
        try:
            with conn:
                cur = conn.execute("""
                    UPDATE admin_sessions
                    SET revoked_at = ?
                    WHERE lower(username) = ? AND revoked_at IS NULL
                """, (time.time(), clean_user))
                return int(cur.rowcount or 0)
        finally:
            conn.close()
    except Exception:
        return 0


def create_preauth_cookie(username: str) -> str:
    """Crea cookie temporal durante el paso intermedio de 2FA (nunca válida como sesión completa)."""
    return _get_serializer().dumps({"user": username.lower(), "step": "2fa"}, salt="preauth")


def verify_preauth_cookie(cookie: Optional[str]) -> Optional[str]:
    """Verifica la cookie temporal de 2FA (válida por 5 minutos)."""
    if not cookie:
        return None
    try:
        data = _get_serializer().loads(cookie, salt="preauth", max_age=300)
        if not isinstance(data, dict) or data.get("step") != "2fa":
            return None
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None


def _derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    """Deriva una clave simétrica de 256 bits (32 bytes) usando PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _resolve_backup_passphrase(explicit_key: Optional[str] = None) -> str:
    """Resuelve la clave exclusiva de cifrado de backups sin reutilizar SESSION_SECRET_KEY en producción (V16)."""
    if explicit_key:
        return explicit_key
    backup_key = (
        getattr(settings, "BACKUP_ENCRYPTION_KEY", "") or
        os.getenv("BACKUP_ENCRYPTION_KEY", "")
    ).strip()
    if backup_key:
        return backup_key
    if getattr(settings, "APP_ENV", "development") == "production":
        raise ValueError("CRITICAL [V16]: BACKUP_ENCRYPTION_KEY independiente es obligatoria en producción")
    return settings.SESSION_SECRET_KEY


def _resolve_db_passphrases(explicit_key: Optional[str] = None) -> List[str]:
    """Resuelve la clave primaria de cifrado de columnas (DB_ENCRYPTION_KEY) y claves históricas para rotación sin pérdida (V16)."""
    if explicit_key:
        return [explicit_key]
    candidates: List[str] = []
    for k in (
        getattr(settings, "DB_ENCRYPTION_KEY", ""),
        os.getenv("DB_ENCRYPTION_KEY", ""),
        getattr(settings, "DB_SECRET_KEY", ""),
        os.getenv("DB_SECRET_KEY", ""),
    ):
        clean_k = (k or "").strip()
        if clean_k and clean_k not in candidates:
            candidates.append(clean_k)

    if not candidates and getattr(settings, "APP_ENV", "development") == "production":
        raise ValueError("CRITICAL [V16]: DB_ENCRYPTION_KEY independiente es obligatoria en producción")

    sess_key = (getattr(settings, "SESSION_SECRET_KEY", "") or "").strip()
    if sess_key and sess_key not in candidates:
        candidates.append(sess_key)

    if not candidates:
        raise ValueError("No se configuró clave de cifrado de secretos (DB_ENCRYPTION_KEY)")
    return candidates


def encrypt_backup(data: bytes, key: Optional[str] = None) -> bytes:
    """Cifra datos binarios utilizando AES-256-GCM con derivación PBKDF2 y salt único (V16)."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Los datos a cifrar deben ser de tipo bytes o bytearray")

    passphrase = _resolve_backup_passphrase(key)
    salt = secrets.token_bytes(BACKUP_SALT_LEN)
    nonce = secrets.token_bytes(BACKUP_NONCE_LEN)
    derived_key = _derive_backup_key(passphrase, salt)

    aesgcm = AESGCM(derived_key)
    encrypted_payload = aesgcm.encrypt(nonce, bytes(data), None)
    return BACKUP_MAGIC_HEADER + salt + nonce + encrypted_payload


def decrypt_backup(ciphertext: bytes, key: Optional[str] = None) -> bytes:
    """Descifra un backup previamente cifrado con encrypt_backup mediante AES-256-GCM."""
    min_len = len(BACKUP_MAGIC_HEADER) + BACKUP_SALT_LEN + BACKUP_NONCE_LEN + BACKUP_TAG_LEN
    if not isinstance(ciphertext, (bytes, bytearray)) or len(ciphertext) < min_len:
        raise ValueError("Longitud de backup cifrado insuficiente o datos corruptos")

    if not ciphertext.startswith(BACKUP_MAGIC_HEADER):
        raise ValueError("Cabecera mágica inválida: no es un archivo de backup compatible de StreamVault")

    offset = len(BACKUP_MAGIC_HEADER)
    salt = ciphertext[offset : offset + BACKUP_SALT_LEN]
    offset += BACKUP_SALT_LEN
    nonce = ciphertext[offset : offset + BACKUP_NONCE_LEN]
    offset += BACKUP_NONCE_LEN
    encrypted_payload = ciphertext[offset:]

    passphrase = _resolve_backup_passphrase(key)
    derived_key = _derive_backup_key(passphrase, salt)
    aesgcm = AESGCM(derived_key)

    try:
        return aesgcm.decrypt(nonce, encrypted_payload, None)
    except InvalidTag as e:
        raise InvalidTag("Clave de descifrado incorrecta o integridad de backup violada (tag GCM inválido)") from e


SECRET_PREFIX = "enc:v1:"
SECRET_PREFIX_V2 = "enc:v2:"
COLUMN_SALT_LEN = 16
COLUMN_NONCE_LEN = 12


def is_encrypted_secret(text: Optional[str]) -> bool:
    """Indica si un string ya contiene el prefijo de cifrado a nivel de columna (v1 o v2)."""
    return isinstance(text, str) and (text.startswith(SECRET_PREFIX) or text.startswith(SECRET_PREFIX_V2))


def encrypt_secret(plaintext: Optional[str], key: Optional[str] = None) -> str:
    """Cifra un texto plano para almacenamiento seguro en base de datos (AES-256-GCM) con DB_ENCRYPTION_KEY (V16)."""
    if plaintext is None:
        return ""
    text_str = str(plaintext)
    if not text_str:
        return ""
    if is_encrypted_secret(text_str):
        return text_str

    passphrase = _resolve_db_passphrases(key)[0]
    salt = secrets.token_bytes(COLUMN_SALT_LEN)
    nonce = secrets.token_bytes(COLUMN_NONCE_LEN)
    derived_key = _derive_backup_key(passphrase, salt)

    aesgcm = AESGCM(derived_key)
    encrypted_bytes = aesgcm.encrypt(nonce, text_str.encode("utf-8"), None)

    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    nonce_b64 = base64.urlsafe_b64encode(nonce).decode("ascii")
    enc_b64 = base64.urlsafe_b64encode(encrypted_bytes).decode("ascii")

    return f"{SECRET_PREFIX}{salt_b64}:{nonce_b64}:{enc_b64}"


def decrypt_secret(ciphertext: Optional[str], key: Optional[str] = None) -> str:
    """Descifra un texto cifrado con encrypt_secret mediante AES-256-GCM soportando rotación de claves (V16)."""
    if ciphertext is None:
        return ""
    text_str = str(ciphertext)
    if not text_str:
        return ""
    if not is_encrypted_secret(text_str):
        return text_str

    prefix_len = len(SECRET_PREFIX_V2) if text_str.startswith(SECRET_PREFIX_V2) else len(SECRET_PREFIX)
    raw_payload = text_str[prefix_len:]
    parts = raw_payload.split(":")
    if len(parts) != 3:
        raise ValueError("Formato de secreto cifrado inválido (se esperaban 3 componentes base64)")

    try:
        salt = base64.urlsafe_b64decode(parts[0].encode("ascii"))
        nonce = base64.urlsafe_b64decode(parts[1].encode("ascii"))
        encrypted_bytes = base64.urlsafe_b64decode(parts[2].encode("ascii"))
    except Exception as e:
        raise ValueError(f"Error decodificando componentes base64 del secreto: {e}") from e

    passphrases = _resolve_db_passphrases(key)
    last_err: Optional[Exception] = None
    for passphrase in passphrases:
        derived_key = _derive_backup_key(passphrase, salt)
        aesgcm = AESGCM(derived_key)
        try:
            decrypted_bytes = aesgcm.decrypt(nonce, encrypted_bytes, None)
            return decrypted_bytes.decode("utf-8")
        except InvalidTag as e:
            last_err = e

    raise ValueError("Clave incorrecta o integridad de secreto alterada (tag AES-GCM inválido)") from last_err

