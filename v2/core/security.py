import os
import hashlib
import secrets
from typing import Optional, Tuple
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag

from core.config import settings

_serializer = URLSafeTimedSerializer(settings.SESSION_SECRET_KEY)

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


def _derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    """Deriva una clave simétrica de 256 bits (32 bytes) usando PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_backup(data: bytes, key: Optional[str] = None) -> bytes:
    """Cifra datos binarios utilizando AES-256-GCM con derivación PBKDF2 y salt único.
    
    Estructura binaria del backup protegido:
    [Cabecera Mágica: 7 bytes (b"SVENC01")] +
    [Salt PBKDF2: 16 bytes] +
    [Nonce / IV: 12 bytes] +
    [Ciphertext AES-256-GCM + Tag de autenticación GCM: N + 16 bytes]
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Los datos a cifrar deben ser de tipo bytes o bytearray")

    passphrase = (
        key or
        getattr(settings, "BACKUP_ENCRYPTION_KEY", "") or
        os.getenv("BACKUP_ENCRYPTION_KEY", "") or
        settings.SESSION_SECRET_KEY
    )
    if not passphrase:
        raise ValueError("No se configuró clave de cifrado (BACKUP_ENCRYPTION_KEY o SESSION_SECRET_KEY)")

    salt = secrets.token_bytes(BACKUP_SALT_LEN)
    nonce = secrets.token_bytes(BACKUP_NONCE_LEN)
    derived_key = _derive_backup_key(passphrase, salt)

    aesgcm = AESGCM(derived_key)
    # AESGCM.encrypt concatena automáticamente el tag de autenticación (16 bytes) al final del ciphertext
    encrypted_payload = aesgcm.encrypt(nonce, bytes(data), None)

    return BACKUP_MAGIC_HEADER + salt + nonce + encrypted_payload


def decrypt_backup(ciphertext: bytes, key: Optional[str] = None) -> bytes:
    """Descifra un backup previamente cifrado con encrypt_backup mediante AES-256-GCM.
    
    Verifica cabecera mágica (SVENC01), salt, nonce e integridad criptográfica con tag GCM.
    Lanza ValueError o InvalidTag si la clave es incorrecta, los datos están alterados o el formato es inválido.
    """
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

    passphrase = (
        key or
        getattr(settings, "BACKUP_ENCRYPTION_KEY", "") or
        os.getenv("BACKUP_ENCRYPTION_KEY", "") or
        settings.SESSION_SECRET_KEY
    )
    if not passphrase:
        raise ValueError("No se configuró clave de descifrado (BACKUP_ENCRYPTION_KEY o SESSION_SECRET_KEY)")

    derived_key = _derive_backup_key(passphrase, salt)
    aesgcm = AESGCM(derived_key)

    try:
        decrypted_data = aesgcm.decrypt(nonce, encrypted_payload, None)
        return decrypted_data
    except InvalidTag as e:
        raise InvalidTag("Clave de descifrado incorrecta o integridad de backup violada (tag GCM inválido)") from e

