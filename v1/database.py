import os
import sqlite3
import hashlib
import secrets
import time
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple

DB_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DB_DIR, "services.db")

def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# Criptografía de contraseñas (PBKDF2-SHA256)
# ==========================================
def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
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
    computed_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(computed_hash, stored_hash)

# ==========================================
# Inicialización y Esquema
# ==========================================
def init_db():
    """Inicializa la base de datos y crea las tablas necesarias."""
    conn = get_connection()
    try:
        with conn:
            # Tabla de servicios
            conn.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT 'Servicio',
                    expiry_date TEXT NOT NULL,
                    recurrence TEXT DEFAULT 'mensual',
                    cost TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    last_alert_sent TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Tabla de usuarios administradores
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admin_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    totp_secret TEXT DEFAULT '',
                    telegram_otp TEXT DEFAULT '',
                    telegram_otp_expiry REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    finally:
        conn.close()

# ==========================================
# Gestión de Usuarios y 2FA
# ==========================================
def create_or_update_admin(username: str, password: str, totp_secret: str = ""):
    """Crea o actualiza la contraseña de un usuario administrador."""
    p_hash, salt = hash_password(password)
    conn = get_connection()
    try:
        with conn:
            existing = conn.execute("SELECT id, totp_secret FROM admin_users WHERE username = ?", (username,)).fetchone()
            if existing:
                secret_to_use = totp_secret if totp_secret else existing["totp_secret"]
                conn.execute("""
                    UPDATE admin_users 
                    SET password_hash = ?, salt = ?, totp_secret = ?
                    WHERE username = ?
                """, (p_hash, salt, secret_to_use, username))
            else:
                conn.execute("""
                    INSERT INTO admin_users (username, password_hash, salt, totp_secret)
                    VALUES (?, ?, ?, ?)
                """, (username, p_hash, salt, totp_secret))
    finally:
        conn.close()

def get_admin_user(username: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def verify_admin_credentials(username: str, password: str) -> bool:
    user = get_admin_user(username)
    if not user:
        return False
    return verify_password(password, user["password_hash"], user["salt"])

def set_telegram_otp(username: str, otp: str, duration_seconds: int = 300):
    """Guarda un OTP temporal de Telegram con expiración (por defecto 5 minutos)."""
    expiry = time.time() + duration_seconds
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE admin_users 
                SET telegram_otp = ?, telegram_otp_expiry = ?
                WHERE username = ?
            """, (otp, expiry, username))
    finally:
        conn.close()

def verify_telegram_otp(username: str, otp: str) -> bool:
    """Verifica si el OTP de Telegram es correcto y no ha expirado."""
    user = get_admin_user(username)
    if not user:
        return False
    stored_otp = user.get("telegram_otp")
    expiry = user.get("telegram_otp_expiry", 0)
    
    if not stored_otp or time.time() > expiry:
        return False
        
    if secrets.compare_digest(stored_otp, otp.strip()):
        # Limpiar el OTP usado para que sea de un solo uso
        conn = get_connection()
        try:
            with conn:
                conn.execute("UPDATE admin_users SET telegram_otp = '', telegram_otp_expiry = 0 WHERE username = ?", (username,))
        finally:
            conn.close()
        return True
    return False

# ==========================================
# Gestión de Servicios
# ==========================================
def add_service(name: str, expiry_date: str, category: str = "Servicio", 
                recurrence: str = "mensual", cost: str = "", notes: str = "") -> Dict[str, Any]:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO services (name, category, expiry_date, recurrence, cost, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name.strip(), category.strip(), expiry_date.strip(), recurrence.strip(), cost.strip(), notes.strip()))
            service_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def list_services() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM services ORDER BY expiry_date ASC").fetchall()
        today = date.today()
        result = []
        for row in rows:
            data = dict(row)
            try:
                exp_date = datetime.strptime(data["expiry_date"], "%Y-%m-%d").date()
                diff_days = (exp_date - today).days
                data["days_remaining"] = diff_days
                if diff_days < 0:
                    data["status"] = "vencido"
                elif diff_days <= 2:
                    data["status"] = "por_vencer"
                else:
                    data["status"] = "activo"
            except Exception:
                data["days_remaining"] = None
                data["status"] = "fecha_invalida"
            result.append(data)
        return result
    finally:
        conn.close()

def get_expiring_services(days_window: int = 2) -> List[Dict[str, Any]]:
    all_svcs = list_services()
    expiring = []
    for s in all_svcs:
        days = s.get("days_remaining")
        if days is not None and days <= days_window:
            expiring.append(s)
    return expiring

def delete_service(service_id: int) -> bool:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def update_service_date(service_id: int, new_expiry_date: str) -> bool:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                UPDATE services 
                SET expiry_date = ?, last_alert_sent = ''
                WHERE id = ?
            """, (new_expiry_date.strip(), service_id))
            return cursor.rowcount > 0
    finally:
        conn.close()

def mark_alert_sent(service_id: int, alert_date: str):
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE services 
                SET last_alert_sent = ?
                WHERE id = ?
            """, (alert_date, service_id))
    finally:
        conn.close()
