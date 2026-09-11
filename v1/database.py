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
# Inicialización y Esquema Relacional
# ==========================================
def init_db():
    """Inicializa la base de datos y crea las tablas necesarias."""
    conn = get_connection()
    try:
        with conn:
            # 1. Tabla de administradores
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

            # 2. Tabla de clientes (CRM Streaming)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_code TEXT UNIQUE,
                    name TEXT NOT NULL,
                    whatsapp TEXT DEFAULT '',
                    telegram TEXT DEFAULT '',
                    client_type TEXT DEFAULT 'consumidor_final', -- 'consumidor_final' o 'revendedor'
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. Tabla de cuentas / perfiles de streaming
            conn.execute("""
                CREATE TABLE IF NOT EXISTS streaming_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, -- Netflix, Disney+, Max, Prime, Spotify, etc.
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    profile_name TEXT DEFAULT '',
                    profile_pin TEXT DEFAULT '',
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    status TEXT DEFAULT 'libre', -- 'libre', 'ocupada', 'caida', 'reemplazada_caida', 'vencida'
                    start_date TEXT DEFAULT '',
                    expiry_date TEXT DEFAULT '',
                    recurrence TEXT DEFAULT 'mensual',
                    price TEXT DEFAULT '',
                    cost TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    last_alert_sent TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. Tabla de servicios generales (para compatibilidad previa)
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
    finally:
        conn.close()

# ==========================================
# Gestión de Usuarios y 2FA
# ==========================================
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

# ==========================================
# CRM de Clientes
# ==========================================
def find_or_create_client(
    name: str, 
    whatsapp: str = "", 
    telegram: str = "", 
    client_type: str = "consumidor_final", 
    notes: str = ""
) -> Dict[str, Any]:
    """Busca un cliente existente por nombre/alias, whatsapp o telegram, o lo crea si no existe."""
    conn = get_connection()
    clean_name = name.strip()
    clean_wa = whatsapp.strip().replace(" ", "").replace("-", "")
    clean_tg = telegram.strip()
    if clean_tg and not clean_tg.startswith("@"):
        clean_tg = "@" + clean_tg
    c_type = "revendedor" if "revend" in client_type.lower() else "consumidor_final"

    try:
        with conn:
            # Buscar por nombre, whatsapp o telegram
            query = """
                SELECT * FROM clients 
                WHERE lower(name) = lower(?)
                OR (length(?) > 4 AND replace(replace(whatsapp, ' ', ''), '-', '') = ?)
                OR (length(?) > 2 AND lower(telegram) = lower(?))
                LIMIT 1
            """
            existing = conn.execute(query, (clean_name, clean_wa, clean_wa, clean_tg, clean_tg)).fetchone()

            if existing:
                client_id = existing["id"]
                # Actualizar datos si se proporcionaron nuevos
                conn.execute("""
                    UPDATE clients 
                    SET whatsapp = CASE WHEN length(?) > 0 THEN ? ELSE whatsapp END,
                        telegram = CASE WHEN length(?) > 0 THEN ? ELSE telegram END,
                        client_type = ?,
                        notes = CASE WHEN length(?) > 0 THEN ? ELSE notes END
                    WHERE id = ?
                """, (clean_wa, clean_wa, clean_tg, clean_tg, c_type, notes.strip(), notes.strip(), client_id))
                row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
                return dict(row)
            else:
                # Crear nuevo cliente
                cursor = conn.execute("SELECT count(*) as total FROM clients")
                total = cursor.fetchone()["total"] + 1
                client_code = f"CLI-{total:03d}"

                cursor = conn.execute("""
                    INSERT INTO clients (client_code, name, whatsapp, telegram, client_type, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (client_code, clean_name, clean_wa, clean_tg, c_type, notes.strip()))
                client_id = cursor.lastrowid
                row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
                return dict(row)
    finally:
        conn.close()

def search_client(query: str) -> Optional[Dict[str, Any]]:
    """Busca un cliente por ID, código, nombre, whatsapp o usuario de telegram."""
    conn = get_connection()
    q = query.strip()
    clean_q = q.replace(" ", "").replace("-", "")
    try:
        with conn:
            row = conn.execute("""
                SELECT * FROM clients 
                WHERE lower(name) LIKE lower(?)
                OR lower(client_code) = lower(?)
                OR lower(telegram) = lower(?)
                OR replace(replace(whatsapp, ' ', ''), '-', '') LIKE ?
                ORDER BY id ASC LIMIT 1
            """, (f"%{q}%", q, f"@{q.lstrip('@')}", f"%{clean_q}%")).fetchone()
            
            if not row:
                return None
            
            client_data = dict(row)
            # Buscar sus cuentas activas
            accounts = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE client_id = ? AND status != 'reemplazada_caida'
                ORDER BY expiry_date ASC
            """, (client_data["id"],)).fetchall()
            
            client_data["accounts"] = [dict(a) for a in accounts]
            return client_data
    finally:
        conn.close()

def list_all_clients() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM clients ORDER BY name ASC").fetchall()
        result = []
        for r in rows:
            c = dict(r)
            accts = conn.execute("SELECT count(*) as count FROM streaming_accounts WHERE client_id = ? AND status = 'ocupada'", (c["id"],)).fetchone()
            c["active_accounts_count"] = accts["count"]
            result.append(c)
        return result
    finally:
        conn.close()

# ==========================================
# Gestión de Cuentas y Perfiles de Streaming
# ==========================================
def add_free_account(
    platform: str, 
    email: str, 
    password: str, 
    profile_name: str = "", 
    profile_pin: str = "", 
    cost: str = "", 
    notes: str = ""
) -> Dict[str, Any]:
    """Agrega una cuenta o perfil disponible (stock libre) para la venta."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO streaming_accounts (platform, email, password, profile_name, profile_pin, status, cost, notes)
                VALUES (?, ?, ?, ?, ?, 'libre', ?, ?)
            """, (clean_platform, email.strip(), password.strip(), profile_name.strip(), profile_pin.strip(), cost.strip(), notes.strip()))
            acc_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM streaming_accounts WHERE id = ?", (acc_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def assign_or_sell_account(
    client_name: str,
    platform: str,
    email: str,
    password: str,
    expiry_date: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    profile_name: str = "",
    profile_pin: str = "",
    start_date: str = "",
    recurrence: str = "mensual",
    price: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    """Registra una venta o asignación de cuenta/perfil a un cliente."""
    client = find_or_create_client(
        name=client_name, 
        whatsapp=whatsapp, 
        telegram=telegram, 
        client_type=client_type,
        notes=notes
    )
    clean_platform = platform.strip().title()
    s_date = start_date.strip() if start_date else date.today().isoformat()
    
    conn = get_connection()
    try:
        with conn:
            # Verificar si la cuenta ya existía como 'libre' para asignarla
            existing_acc = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE lower(email) = lower(?) AND status = 'libre'
                LIMIT 1
            """, (email.strip(),)).fetchone()

            if existing_acc:
                acc_id = existing_acc["id"]
                conn.execute("""
                    UPDATE streaming_accounts
                    SET client_id = ?, status = 'ocupada', platform = ?, password = ?,
                        profile_name = ?, profile_pin = ?, start_date = ?, expiry_date = ?,
                        recurrence = ?, price = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (client["id"], clean_platform, password.strip(), profile_name.strip(), 
                      profile_pin.strip(), s_date, expiry_date.strip(), recurrence.strip(), 
                      price.strip(), notes.strip(), acc_id))
            else:
                cursor = conn.execute("""
                    INSERT INTO streaming_accounts (
                        platform, email, password, profile_name, profile_pin,
                        client_id, status, start_date, expiry_date, recurrence,
                        price, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ocupada', ?, ?, ?, ?, ?)
                """, (clean_platform, email.strip(), password.strip(), profile_name.strip(),
                      profile_pin.strip(), client["id"], s_date, expiry_date.strip(),
                      recurrence.strip(), price.strip(), notes.strip()))
                acc_id = cursor.lastrowid

            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (acc_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def mark_account_fallen(email_or_query: str, reason: str = "Suscripción caída") -> Optional[Dict[str, Any]]:
    """Marca una cuenta o correo en estado 'caida'."""
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram 
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) OR a.id = ?
                ORDER BY a.id DESC LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()
            
            if not row:
                return None
            
            acc_id = row["id"]
            existing_notes = row["notes"] or ""
            updated_notes = f"{existing_notes} | CAÍDA: {reason} ({date.today().isoformat()})".strip(" |")
            
            conn.execute("""
                UPDATE streaming_accounts 
                SET status = 'caida', notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (updated_notes, acc_id))
            
            updated = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (acc_id,)).fetchone()
            return dict(updated)
    finally:
        conn.close()

def replace_fallen_account(email_or_query: str) -> Optional[Dict[str, Any]]:
    """
    Reemplazo inteligente:
    1. Identifica la cuenta caída y su plataforma.
    2. Busca una cuenta en estado 'libre' de la MISMA plataforma.
    3. Asigna la cuenta libre al cliente manteniendo su fecha de vencimiento.
    4. Pasa la cuenta vieja a 'reemplazada_caida'.
    """
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            # 1. Obtener la cuenta con problema
            old_row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type 
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE (lower(a.email) LIKE lower(?) OR a.id = ?)
                AND a.status IN ('caida', 'ocupada')
                ORDER BY a.id DESC LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()

            if not old_row:
                return None
            
            old_acc = dict(old_row)
            platform = old_acc["platform"]
            client_id = old_acc["client_id"]
            expiry = old_acc["expiry_date"]
            price = old_acc["price"]
            recurrence = old_acc["recurrence"]

            # 2. Buscar una cuenta libre de la MISMA plataforma
            free_row = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (platform,)).fetchone()

            if not free_row:
                # No hay stock de esa plataforma
                return {
                    "success": False,
                    "error": f"No hay cuentas libres disponibles en inventario para la plataforma '{platform}'",
                    "old_account": old_acc
                }

            new_acc = dict(free_row)

            # 3. Marcar la cuenta vieja como reemplazada
            conn.execute("""
                UPDATE streaming_accounts 
                SET status = 'reemplazada_caida', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (old_acc["id"],))

            # 4. Asignar la nueva cuenta libre al cliente con la fecha de vencimiento original
            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', expiry_date = ?, 
                    price = ?, recurrence = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client_id, expiry, price, recurrence, f"Reemplazo de {old_acc['email']}", new_acc["id"]))

            # 5. Obtener los datos actualizados
            fresh_new = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (new_acc["id"],)).fetchone()

            return {
                "success": True,
                "platform": platform,
                "old_account": old_acc,
                "new_account": dict(fresh_new)
            }
    finally:
        conn.close()

def get_free_stock(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        if platform:
            rows = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE status = 'libre' AND lower(platform) = lower(?)
                ORDER BY platform ASC, id ASC
            """, (platform.strip(),)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE status = 'libre'
                ORDER BY platform ASC, id ASC
            """,).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_fallen_accounts() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.status = 'caida'
            ORDER BY a.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_active_accounts() -> List[Dict[str, Any]]:
    conn = get_connection()
    today = date.today()
    try:
        rows = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE a.status = 'ocupada'
            ORDER BY a.expiry_date ASC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                exp = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
                diff = (exp - today).days
                d["days_remaining"] = diff
            except Exception:
                d["days_remaining"] = None
            result.append(d)
        return result
    finally:
        conn.close()

def get_expiring_streaming_accounts(days_window: int = 2) -> List[Dict[str, Any]]:
    all_active = get_active_accounts()
    expiring = []
    for a in all_active:
        d = a.get("days_remaining")
        if d is not None and d <= days_window:
            expiring.append(a)
    return expiring

def renew_account(account_id_or_email: str, new_expiry_date: str) -> bool:
    conn = get_connection()
    q = account_id_or_email.strip()
    try:
        with conn:
            cursor = conn.execute("""
                UPDATE streaming_accounts 
                SET expiry_date = ?, status = 'ocupada', last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE (lower(email) = lower(?) OR id = ?)
            """, (new_expiry_date.strip(), q, int(q) if q.isdigit() else -1))
            return cursor.rowcount > 0
    finally:
        conn.close()

def delete_account(account_id: int) -> bool:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM streaming_accounts WHERE id = ?", (account_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def mark_streaming_alert_sent(account_id: int, alert_date: str):
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE streaming_accounts 
                SET last_alert_sent = ?
                WHERE id = ?
            """, (alert_date, account_id))
    finally:
        conn.close()

# Métodos heredados para servicios generales
def list_services() -> List[Dict[str, Any]]:
    # Retorna tanto servicios generales como cuentas de streaming activas
    return get_active_accounts()

def add_service(name: str, expiry_date: str, category: str = "Servicio", recurrence: str = "mensual", cost: str = "", notes: str = ""):
    return assign_or_sell_account(
        client_name="General",
        platform=category or "General",
        email=name,
        password="",
        expiry_date=expiry_date,
        recurrence=recurrence,
        price=cost,
        notes=notes
    )

def delete_service(service_id: int) -> bool:
    return delete_account(service_id)

def get_expiring_services(days_window: int = 2) -> List[Dict[str, Any]]:
    return get_expiring_streaming_accounts(days_window)

def mark_alert_sent(service_id: int, alert_date: str):
    mark_streaming_alert_sent(service_id, alert_date)
