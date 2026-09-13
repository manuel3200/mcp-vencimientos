import hashlib
import secrets
from typing import Optional, Tuple
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from core.config import settings

_serializer = URLSafeTimedSerializer(settings.SESSION_SECRET_KEY)

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

def create_session_cookie(username: str) -> str:
    """Crea una cookie firmada con expiración para una sesión autenticada."""
    return _serializer.dumps({"user": username.lower(), "auth": True}, salt="session-auth")

def verify_session_cookie(cookie: Optional[str]) -> Optional[str]:
    """Verifica y decodifica la cookie de sesión (válida por 7 días)."""
    if not cookie:
        return None
    try:
        data = _serializer.loads(cookie, salt="session-auth", max_age=86400 * 7)
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

def create_preauth_cookie(username: str) -> str:
    """Crea cookie temporal durante el paso intermedio de 2FA."""
    return _serializer.dumps({"user": username.lower(), "step": "2fa"}, salt="preauth")

def verify_preauth_cookie(cookie: Optional[str]) -> Optional[str]:
    """Verifica la cookie temporal de 2FA (válida por 5 minutos)."""
    if not cookie:
        return None
    try:
        data = _serializer.loads(cookie, salt="preauth", max_age=300)
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None
