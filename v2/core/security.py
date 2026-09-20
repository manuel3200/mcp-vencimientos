import os
import hashlib
import secrets
import base64
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


SECRET_PREFIX = "enc:v1:"
COLUMN_SALT_LEN = 16
COLUMN_NONCE_LEN = 12

def is_encrypted_secret(text: Optional[str]) -> bool:
    """Indica si un string ya contiene el prefijo de cifrado a nivel de columna."""
    return isinstance(text, str) and text.startswith(SECRET_PREFIX)


def encrypt_secret(plaintext: Optional[str], key: Optional[str] = None) -> str:
    """Cifra un texto plano para almacenamiento seguro en base de datos (AES-256-GCM).
    
    Formato serializado:
    enc:v1:<salt_b64>:<nonce_b64>:<ciphertext_tag_b64>
    
    Idempotente: si el texto ya está cifrado (comienza con 'enc:v1:'), se devuelve tal cual.
    Si plaintext es None o vacío, devuelve "".
    """
    if plaintext is None:
        return ""
    text_str = str(plaintext)
    if not text_str:
        return ""
    if is_encrypted_secret(text_str):
        return text_str

    passphrase = (
        key or
        getattr(settings, "DB_SECRET_KEY", "") or
        os.getenv("DB_SECRET_KEY", "") or
        settings.SESSION_SECRET_KEY
    )
    if not passphrase:
        raise ValueError("No se configuró clave de cifrado de secretos (DB_SECRET_KEY o SESSION_SECRET_KEY)")

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
    """Descifra un texto cifrado con encrypt_secret mediante AES-256-GCM.
    
    Retrocompatibilidad: Si el string no empieza con 'enc:v1:', asume texto plano histórico
    y lo retorna sin error, permitiendo migraciones continuas sin downtime.
    Si falla el tag o la clave, lanza ValueError.
    """
    if ciphertext is None:
        return ""
    text_str = str(ciphertext)
    if not text_str:
        return ""
    if not is_encrypted_secret(text_str):
        return text_str

    raw_payload = text_str[len(SECRET_PREFIX):]
    parts = raw_payload.split(":")
    if len(parts) != 3:
        raise ValueError("Formato de secreto cifrado inválido (se esperaban 3 componentes base64)")

    try:
        salt = base64.urlsafe_b64decode(parts[0].encode("ascii"))
        nonce = base64.urlsafe_b64decode(parts[1].encode("ascii"))
        encrypted_bytes = base64.urlsafe_b64decode(parts[2].encode("ascii"))
    except Exception as e:
        raise ValueError(f"Error decodificando componentes base64 del secreto: {e}") from e

    passphrase = (
        key or
        getattr(settings, "DB_SECRET_KEY", "") or
        os.getenv("DB_SECRET_KEY", "") or
        settings.SESSION_SECRET_KEY
    )
    if not passphrase:
        raise ValueError("No se configuró clave de descifrado de secretos (DB_SECRET_KEY o SESSION_SECRET_KEY)")

    derived_key = _derive_backup_key(passphrase, salt)
    aesgcm = AESGCM(derived_key)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, encrypted_bytes, None)
        return decrypted_bytes.decode("utf-8")
    except InvalidTag as e:
        raise ValueError("Clave incorrecta o integridad de secreto alterada (tag AES-GCM inválido)") from e


