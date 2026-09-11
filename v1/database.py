import os
import re
import csv
import io
import json
import sqlite3
import hashlib
import secrets
import time
import urllib.parse
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union

DB_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DB_DIR, "services.db")

def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# Helpers de Criptografía y Finanzas
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

def parse_money(val: Union[str, float, int, None]) -> float:
    """Extrae el valor numérico flotante soportando Pesos Argentinos (ARS) y formatos internacionales.
    Maneja: '15000', '$ 15.000', '$ 4.500,50', '4500.50', '$3,500', etc.
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0

    # Remover letras y caracteres que no sean dígitos, comas o puntos
    s = re.sub(r'[^\d\.,]', '', s)
    if not s:
        return 0.0

    # Caso 1: Tiene tanto punto como coma, ej: "15.000,50" o "15,000.50"
    if '.' in s and ',' in s:
        if s.rfind(',') > s.rfind('.'):
            # Notación argentina / hispana (punto de miles, coma decimal: 15.000,50)
            s = s.replace('.', '').replace(',', '.')
        else:
            # Notación anglosajona (coma de miles, punto decimal: 15,000.50)
            s = s.replace(',', '')
    # Caso 2: Solo tiene coma
    elif ',' in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # Decimal: 4500,50 -> 4500.50
            s = s.replace(',', '.')
        else:
            # Miles: 15,000 -> 15000
            s = s.replace(',', '')
    # Caso 3: Solo tiene punto
    elif '.' in s:
        parts = s.split('.')
        if len(parts) > 2:
            # Varios puntos (ej: 1.500.000) -> miles
            s = s.replace('.', '')
        elif len(parts) == 2:
            if len(parts[1]) == 3:
                # E.g. "15.000" o "4.500" en Argentina son miles
                s = s.replace('.', '')
            else:
                # E.g. "4500.50" o "12.5" es decimal
                pass

    try:
        return float(s)
    except ValueError:
        return 0.0

def format_ars(val: Union[float, int, str, None], include_symbol: bool = True) -> str:
    """Formatea un monto en Pesos Argentinos con separador de miles con punto.
    Ejemplos:
    - 15000 -> "$ 15.000 ARS"
    - 4500.50 -> "$ 4.500,50 ARS"
    """
    num = parse_money(val)
    if num.is_integer():
        formatted = f"{int(num):,}".replace(",", ".")
    else:
        int_part = int(num)
        cents = int(round((num - int_part) * 100))
        formatted = f"{int_part:,}".replace(",", ".") + f",{cents:02d}"

    if include_symbol:
        return f"${formatted} ARS"
    return formatted

# ==========================================
# Plantillas Predeterminadas de WhatsApp
# ==========================================
DEFAULT_WHATSAPP_TEMPLATES = {
    "cobro": {
        "title": "🔔 Cobro / Recordatorio Individual",
        "description": "Mensaje enviado para recordar el vencimiento de una suscripción individual.",
        "content": (
            "👋 *¡Hola {cliente}!* Esperamos que estés disfrutando tu suscripción.\n\n"
            "Te recordamos que tu servicio está próximo a vencer:\n"
            "📺 *Servicio:* {plataforma}\n"
            "📧 *Cuenta:* `{email}`\n"
            "📅 *Vencimiento:* {vencimiento}{dias_restantes}\n"
            "💰 *Monto a Renovar:* {monto}\n\n"
            "💳 *Métodos de Pago:*\n"
            "{metodos_pago}\n\n"
            "Una vez abonado, por favor envíanos tu comprobante por aquí para renovarte inmediatamente sin cortes. ¡Muchas gracias! 🙌"
        )
    },
    "cobro_consolidado": {
        "title": "🧾 Cobro Consolidado (Multicuentas)",
        "description": "Mensaje unificado para clientes con 2 o más servicios activos.",
        "content": (
            "👋 *¡Hola {cliente}!* Esperamos que estés muy bien.\n\n"
            "Te compartimos el resumen consolidado de tus suscripciones activas:\n\n"
            "📺 *Servicios Activos:*\n"
            "{servicios_lista}\n\n"
            "💰 *TOTAL CONSOLIDADO A RENOVAR:* {monto}\n\n"
            "💳 *Métodos de Pago:*\n"
            "{metodos_pago}\n\n"
            "Una vez realizado el abono, por favor envíanos tu comprobante por aquí para mantener tus perfiles 100% activos y sin cortes. ¡Muchas gracias! 🙌"
        )
    },
    "entrega": {
        "title": "🍿 Entrega de Credenciales y Perfil",
        "description": "Mensaje enviado al entregar una nueva cuenta o suscripción vendida.",
        "content": (
            "🍿 *¡Hola {cliente}!* Aquí tienes los datos de acceso a tu suscripción:\n\n"
            "📺 *Servicio:* {plataforma}\n"
            "📧 *Usuario/Correo:* `{email}`\n"
            "🔑 *Contraseña:* `{password}`\n"
            "👤 *Perfil Asignado:* {perfil}\n"
            "🔒 *PIN de Perfil:* {pin}\n"
            "📅 *Vencimiento:* {vencimiento}\n"
            "💰 *Valor:* {monto}\n\n"
            "⚠️ *Reglas de Uso Importantes:*\n"
            "• No cambiar correo ni contraseña.\n"
            "• Utilizar únicamente el perfil asignado.\n"
            "• No ingresar en más dispositivos de los permitidos.\n\n"
            "¡Que lo disfrutes al máximo! Si tienes alguna duda, estamos a tu disposición ✨"
        )
    },
    "reemplazo": {
        "title": "🛠️ Reemplazo por Reactivación o Caída",
        "description": "Mensaje para enviar nuevas credenciales cuando una cuenta reportada se reactiva.",
        "content": (
            "🛠️ *¡Hola {cliente}!* Te informamos que hemos reactivado tu servicio.\n\n"
            "✨ *Nuevos Datos de Acceso:*\n"
            "📺 *Servicio:* {plataforma}\n"
            "📧 *Nuevo Correo:* `{email}`\n"
            "🔑 *Nueva Contraseña:* `{password}`\n"
            "👤 *Perfil:* {perfil}\n"
            "🔒 *PIN de Perfil:* {pin}\n"
            "📅 *Mantiene Vencimiento:* {vencimiento}\n\n"
            "📌 *Recomendación:* Recuerda utilizar únicamente el perfil asignado y no modificar la clave.\n\n"
            "¡Ya puedes continuar disfrutando de tu contenido! 🍿🚀"
        )
    }
}

# ==========================================
# Inicialización y Esquema Relacional
# ==========================================
def init_db():
    """Inicializa la base de datos y crea las tablas necesarias."""
    conn = get_connection()
    try:
        with conn:
            # 1. Administradores y 2FA
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

            # 2. Clientes (CRM)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_code TEXT UNIQUE,
                    name TEXT NOT NULL,
                    whatsapp TEXT DEFAULT '',
                    telegram TEXT DEFAULT '',
                    client_type TEXT DEFAULT 'consumidor_final',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. Cuentas y Perfiles de Streaming
            conn.execute("""
                CREATE TABLE IF NOT EXISTS streaming_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    profile_name TEXT DEFAULT '',
                    profile_pin TEXT DEFAULT '',
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    status TEXT DEFAULT 'libre', -- 'libre', 'ocupada', 'caida', 'reemplazada_caida', 'vencida'
                    payment_status TEXT DEFAULT 'pagado', -- 'pagado', 'pendiente'
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

            # 4. Tabla de Transacciones y Pagos (Finanzas)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER REFERENCES streaming_accounts(id) ON DELETE SET NULL,
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    amount REAL NOT NULL,
                    cost REAL DEFAULT 0.0,
                    profit REAL DEFAULT 0.0,
                    payment_method TEXT DEFAULT 'Transferencia',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migración: Agregar columna payment_status si no existe en bases de datos previas
            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN payment_status TEXT DEFAULT 'pagado'")
            except Exception:
                pass

            # 5. Tabla de Umbrales Mínimos de Stock
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_thresholds (
                    platform TEXT PRIMARY KEY,
                    min_stock INTEGER NOT NULL DEFAULT 2,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 6. Catálogo de Precios Oficiales (ARS)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    service_type TEXT NOT NULL DEFAULT 'pantalla',
                    cost_price REAL DEFAULT 0.0,
                    price_final REAL DEFAULT 0.0,
                    price_reseller REAL DEFAULT 0.0,
                    notes TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(platform, service_type)
                )
            """)

            # 7. Tabla de Combos / Packs Promocionales
            conn.execute("""
                CREATE TABLE IF NOT EXISTS combos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT DEFAULT '',
                    price_final REAL NOT NULL DEFAULT 0.0,
                    price_reseller REAL NOT NULL DEFAULT 0.0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 8. Elementos de cada Combo
            conn.execute("""
                CREATE TABLE IF NOT EXISTS combo_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    combo_id INTEGER NOT NULL REFERENCES combos(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    service_type TEXT NOT NULL DEFAULT 'pantalla'
                )
            """)

            # Sembrado de catálogo base si está vacío
            cat_row = conn.execute("SELECT COUNT(*) as count FROM price_catalog").fetchone()
            if cat_row and cat_row["count"] == 0:
                initial_prices = [
                    ("Netflix 4K", "pantalla", 3200.0, 5500.0, 4200.0, "Perfil 4K UHD individual"),
                    ("Disney+ Premium", "pantalla", 2000.0, 4000.0, 3000.0, "Perfil con deportes ESPN"),
                    ("Max (HBO)", "pantalla", 1800.0, 3800.0, 2800.0, "Perfil Platino 4K"),
                    ("Amazon Prime Video", "pantalla", 1500.0, 3500.0, 2500.0, "Perfil individual"),
                    ("Paramount+", "pantalla", 1400.0, 3000.0, 2200.0, "Perfil individual"),
                    ("Spotify Premium", "cuenta_completa", 2500.0, 5000.0, 3800.0, "Cuenta completa individual"),
                    ("YouTube Premium", "cuenta_completa", 2500.0, 5000.0, 3800.0, "Cuenta sin anuncios"),
                    ("Crunchyroll Mega Fan", "pantalla", 1500.0, 3200.0, 2400.0, "Perfil anime HD")
                ]
                for p, stype, c_price, p_fin, p_res, notes in initial_prices:
                    conn.execute("""
                        INSERT INTO price_catalog (platform, service_type, cost_price, price_final, price_reseller, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (p, stype, c_price, p_fin, p_res, notes))

            # Sembrado de combos modelo si está vacío
            combos_row = conn.execute("SELECT COUNT(*) as count FROM combos").fetchone()
            if combos_row and combos_row["count"] == 0:
                initial_combos = [
                    (
                        "Combo Dúo Cine (Netflix + Disney+)",
                        "1 Pantalla Netflix 4K + 1 Pantalla Disney+ con ESPN",
                        8500.0, 6800.0,
                        [("Netflix 4K", "pantalla"), ("Disney+ Premium", "pantalla")]
                    ),
                    (
                        "Mega Pack Familiar (Netflix + Disney+ + Max)",
                        "Pack de 3 pantallas con todo el entretenimiento",
                        11900.0, 9400.0,
                        [("Netflix 4K", "pantalla"), ("Disney+ Premium", "pantalla"), ("Max (HBO)", "pantalla")]
                    )
                ]
                for c_name, c_desc, p_fin, p_res, items in initial_combos:
                    cur = conn.execute("""
                        INSERT INTO combos (name, description, price_final, price_reseller)
                        VALUES (?, ?, ?, ?)
                    """, (c_name, c_desc, p_fin, p_res))
                    cid = cur.lastrowid
                    for it_plat, it_stype in items:
                        conn.execute("""
                            INSERT INTO combo_items (combo_id, platform, service_type)
                            VALUES (?, ?, ?)
                        """, (cid, it_plat, it_stype))

            # 9. Configuración de Métodos de Cobro y CBU
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payment_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    alias_mp TEXT DEFAULT '',
                    cvu_cbu TEXT DEFAULT '',
                    account_holder TEXT DEFAULT '',
                    bank_name TEXT DEFAULT 'Mercado Pago / Transferencia Bancaria',
                    usdt_address TEXT DEFAULT '',
                    extra_instructions TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT OR IGNORE INTO payment_settings (id) VALUES (1)")

            # 10. Plantillas Personalizables de WhatsApp
            conn.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_templates (
                    template_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Sembrado de plantillas base si faltan
            for t_key, t_data in DEFAULT_WHATSAPP_TEMPLATES.items():
                conn.execute("""
                    INSERT OR IGNORE INTO whatsapp_templates (template_key, title, content, description)
                    VALUES (?, ?, ?, ?)
                """, (t_key, t_data["title"], t_data["content"], t_data["description"]))
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
    conn = get_connection()
    clean_name = name.strip()
    clean_wa = whatsapp.strip().replace(" ", "").replace("-", "")
    clean_tg = telegram.strip()
    if clean_tg and not clean_tg.startswith("@"):
        clean_tg = "@" + clean_tg
    c_type = "revendedor" if "revend" in client_type.lower() else "consumidor_final"

    try:
        with conn:
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

def search_client(query: Union[str, int]) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    q = str(query).strip()
    clean_q = q.replace(" ", "").replace("-", "")
    client_id = int(q) if q.isdigit() else -1
    try:
        with conn:
            row = conn.execute("""
                SELECT * FROM clients 
                WHERE id = ?
                OR lower(name) LIKE lower(?)
                OR lower(client_code) = lower(?)
                OR lower(telegram) = lower(?)
                OR replace(replace(whatsapp, ' ', ''), '-', '') LIKE ?
                ORDER BY id ASC LIMIT 1
            """, (client_id, f"%{q}%", q, f"@{q.lstrip('@')}", f"%{clean_q}%")).fetchone()
            
            if not row:
                return None
            
            client_data = dict(row)
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
# Gestión de Cuentas y Ventas
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
    cost: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    client = find_or_create_client(
        name=client_name, 
        whatsapp=whatsapp, 
        telegram=telegram, 
        client_type=client_type,
        notes=notes
    )
    clean_platform = platform.strip().title()
    s_date = start_date.strip() if start_date else date.today().isoformat()
    
    price_num = parse_money(price)
    cost_num = parse_money(cost)
    stype = "pantalla" if profile_name else "cuenta_completa"
    if price_num == 0.0 or cost_num == 0.0:
        s_price, s_cost = get_suggested_price(clean_platform, stype, client_type)
        if price_num == 0.0 and s_price > 0.0:
            price_num = s_price
            price = format_ars(price_num)
        if cost_num == 0.0 and s_cost > 0.0:
            cost_num = s_cost
            cost = format_ars(cost_num)

    conn = get_connection()
    try:
        with conn:
            existing_acc = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE lower(email) = lower(?) AND status = 'libre'
                LIMIT 1
            """, (email.strip(),)).fetchone()

            if existing_acc:
                acc_id = existing_acc["id"]
                conn.execute("""
                    UPDATE streaming_accounts
                    SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                        platform = ?, password = ?, profile_name = ?, profile_pin = ?,
                        start_date = ?, expiry_date = ?, recurrence = ?, price = ?,
                        cost = CASE WHEN length(?) > 0 THEN ? ELSE cost END,
                        notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (client["id"], clean_platform, password.strip(), profile_name.strip(), 
                      profile_pin.strip(), s_date, expiry_date.strip(), recurrence.strip(), 
                      price.strip(), cost.strip(), cost.strip(), notes.strip(), acc_id))
            else:
                cursor = conn.execute("""
                    INSERT INTO streaming_accounts (
                        platform, email, password, profile_name, profile_pin,
                        client_id, status, payment_status, start_date, expiry_date, recurrence,
                        price, cost, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ocupada', 'pagado', ?, ?, ?, ?, ?, ?)
                """, (clean_platform, email.strip(), password.strip(), profile_name.strip(),
                      profile_pin.strip(), client["id"], s_date, expiry_date.strip(),
                      recurrence.strip(), price.strip(), cost.strip(), notes.strip()))
                acc_id = cursor.lastrowid

            # Registrar la venta en la tabla de pagos / balance
            profit_num = price_num - cost_num
            if price_num > 0:
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'Inicial', 'Venta registrada')
                """, (acc_id, client["id"], price_num, cost_num, profit_num))

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
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
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

            free_row = conn.execute("""
                SELECT * FROM streaming_accounts 
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (platform,)).fetchone()

            if not free_row:
                return {
                    "success": False,
                    "error": f"No hay cuentas libres disponibles en inventario para la plataforma '{platform}'",
                    "old_account": old_acc
                }

            new_acc = dict(free_row)

            conn.execute("UPDATE streaming_accounts SET status = 'reemplazada_caida', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (old_acc["id"],))

            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', expiry_date = ?, 
                    price = ?, recurrence = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client_id, expiry, price, recurrence, f"Reemplazo de {old_acc['email']}", new_acc["id"]))

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
                SET expiry_date = ?, status = 'ocupada', payment_status = 'pagado', 
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
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
            conn.execute("UPDATE streaming_accounts SET last_alert_sent = ? WHERE id = ?", (alert_date, account_id))
    finally:
        conn.close()

# ==========================================
# 5. Módulo de Finanzas, Pagos y Balance
# ==========================================
def register_customer_payment(
    email_or_id: str,
    amount: Optional[float] = None,
    payment_method: str = "Transferencia",
    new_expiry_date: Optional[str] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Registra el cobro de una mensualidad o renovación a un cliente y actualiza el balance."""
    conn = get_connection()
    q = email_or_id.strip()
    try:
        with conn:
            row = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) OR a.id = ?
                LIMIT 1
            """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()

            if not row:
                return {"success": False, "error": f"No se encontró la cuenta '{email_or_id}'"}

            acc = dict(row)
            acc_id = acc["id"]
            client_id = acc.get("client_id")
            
            # Monto cobrado
            final_amount = amount if amount is not None else parse_money(acc.get("price"))
            cost_num = parse_money(acc.get("cost"))
            profit_num = final_amount - cost_num

            # Nueva fecha si se renueva
            if new_expiry_date:
                final_expiry = new_expiry_date.strip()
            else:
                try:
                    curr_exp = datetime.strptime(acc["expiry_date"], "%Y-%m-%d").date()
                    # Si ya estaba vencida, renueva 30 días desde hoy; si no, 30 días desde el vencimiento
                    base_date = max(curr_exp, date.today())
                    final_expiry = (base_date + timedelta(days=30)).isoformat()
                except Exception:
                    final_expiry = (date.today() + timedelta(days=30)).isoformat()

            # Insertar en tabla de pagos
            conn.execute("""
                INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (acc_id, client_id, final_amount, cost_num, profit_num, payment_method.strip(), notes.strip() or "Renovación pagada"))

            # Actualizar cuenta como pagada con nuevo vencimiento
            conn.execute("""
                UPDATE streaming_accounts 
                SET expiry_date = ?, payment_status = 'pagado', status = 'ocupada',
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (final_expiry, acc_id))

            return {
                "success": True,
                "client_name": acc.get("client_name"),
                "platform": acc["platform"],
                "email": acc["email"],
                "amount": final_amount,
                "profit": profit_num,
                "new_expiry": final_expiry,
                "payment_method": payment_method
            }
    finally:
        conn.close()

def get_financial_balance(period: str = "mes_actual") -> Dict[str, Any]:
    """Calcula el balance completo: ingresos brutos, costos de proveedores, ganancia neta y proyecciones."""
    conn = get_connection()
    today = date.today()
    try:
        with conn:
            # 1. Pagos ya cobrados en el mes actual
            month_str = f"{today.year}-{today.month:02d}%"
            res = conn.execute("""
                SELECT 
                    COALESCE(SUM(amount), 0.0) as total_income,
                    COALESCE(SUM(cost), 0.0) as total_costs,
                    COALESCE(SUM(profit), 0.0) as net_profit,
                    COUNT(*) as total_transactions
                FROM payments
                WHERE created_at LIKE ?
            """, (month_str,)).fetchone()

            total_income = float(res["total_income"])
            total_costs = float(res["total_costs"])
            net_profit = float(res["net_profit"])
            tx_count = int(res["total_transactions"])

            # 2. Dinero por cobrar esta semana (cuentas activas que vencen en los próximos 7 días)
            active_accounts = get_active_accounts()
            pending_receivables_7d = 0.0
            pending_accounts_count = 0
            pending_list = []

            for a in active_accounts:
                days = a.get("days_remaining")
                if days is not None and -5 <= days <= 7:
                    price_val = parse_money(a.get("price"))
                    pending_receivables_7d += price_val
                    pending_accounts_count += 1
                    pending_list.append({
                        "client": a.get("client_name"),
                        "platform": a.get("platform"),
                        "email": a.get("email"),
                        "days_remaining": days,
                        "price": price_val,
                        "whatsapp": a.get("whatsapp"),
                        "telegram": a.get("telegram")
                    })

            # 3. Ganancia estimada mensual total si todos pagan
            monthly_projected_gross = sum(parse_money(a.get("price")) for a in active_accounts)
            monthly_projected_costs = sum(parse_money(a.get("cost")) for a in active_accounts)
            monthly_projected_profit = monthly_projected_gross - monthly_projected_costs

            return {
                "period": f"{today.strftime('%B %Y')}",
                "collected_income": total_income,
                "collected_costs": total_costs,
                "collected_profit": net_profit,
                "transactions_count": tx_count,
                "pending_receivables_7d": pending_receivables_7d,
                "pending_accounts_count": pending_accounts_count,
                "pending_accounts": pending_list,
                "projected_monthly_gross": monthly_projected_gross,
                "projected_monthly_profit": monthly_projected_profit,
                "active_subscriptions_total": len(active_accounts)
            }
    finally:
        conn.close()

def get_recent_transactions(limit: int = 15) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT p.*, a.platform, a.email, c.name as client_name, c.client_type
            FROM payments p
            LEFT JOIN streaming_accounts a ON p.account_id = a.id
            LEFT JOIN clients c ON p.client_id = c.id
            ORDER BY p.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ==========================================
# 6. Módulo de Mensajes y Enlaces de WhatsApp (Paso 2)
# ==========================================
def get_account_detail(email_or_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Obtiene el detalle completo de una cuenta y su cliente asociado."""
    conn = get_connection()
    q = str(email_or_id).strip()
    try:
        row = conn.execute("""
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
            WHERE lower(a.email) LIKE lower(?) OR a.id = ?
            ORDER BY a.id DESC LIMIT 1
        """, (f"%{q}%", int(q) if q.isdigit() else -1)).fetchone()
        if not row:
            return None
        d = dict(row)
        today = date.today()
        try:
            exp = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
            d["days_remaining"] = (exp - today).days
        except Exception:
            d["days_remaining"] = None
        return d
    finally:
        conn.close()

def clean_whatsapp_phone(phone: str) -> str:
    """Limpia y estandariza un número telefónico para el protocolo wa.me."""
    if not phone:
        return ""
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return ""
    # Manejo de números de Argentina si vienen en formato local (10 dígitos)
    if len(digits) == 10:
        if digits.startswith("15"):
            digits = "11" + digits[2:]
        digits = "549" + digits
    elif len(digits) == 12 and digits.startswith("54") and not digits.startswith("549"):
        digits = "549" + digits[2:]
    return digits

# ==========================================
# 6. Gestión de Plantillas de WhatsApp y Datos de Pago (Paso 3)
# ==========================================
def get_payment_settings() -> Dict[str, Any]:
    """Obtiene la configuración actual de medios de cobro y datos bancarios."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM payment_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute("INSERT OR IGNORE INTO payment_settings (id) VALUES (1)")
            conn.commit()
            row = conn.execute("SELECT * FROM payment_settings WHERE id = 1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()

def save_payment_settings(
    alias_mp: str = "",
    cvu_cbu: str = "",
    account_holder: str = "",
    bank_name: str = "",
    usdt_address: str = "",
    extra_instructions: str = ""
) -> bool:
    """Guarda o actualiza los datos bancarios y métodos de cobro."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO payment_settings (id, alias_mp, cvu_cbu, account_holder, bank_name, usdt_address, extra_instructions, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    alias_mp = excluded.alias_mp,
                    cvu_cbu = excluded.cvu_cbu,
                    account_holder = excluded.account_holder,
                    bank_name = excluded.bank_name,
                    usdt_address = excluded.usdt_address,
                    extra_instructions = excluded.extra_instructions,
                    updated_at = CURRENT_TIMESTAMP
            """, (alias_mp.strip(), cvu_cbu.strip(), account_holder.strip(), bank_name.strip(), usdt_address.strip(), extra_instructions.strip()))
            return True
    finally:
        conn.close()

def get_formatted_payment_methods(settings: Optional[Dict[str, Any]] = None) -> str:
    """Devuelve el bloque de métodos de pago con los datos bancarios formateados para WhatsApp."""
    s = settings or get_payment_settings()
    alias = s.get("alias_mp", "").strip()
    cbu = s.get("cvu_cbu", "").strip()
    holder = s.get("account_holder", "").strip()
    bank = s.get("bank_name", "").strip() or "Mercado Pago / Transferencia"
    usdt = s.get("usdt_address", "").strip()
    extra = s.get("extra_instructions", "").strip()

    if alias or cbu or holder:
        lines = [f"• *{bank}*"]
        if alias:
            lines.append(f"  👉 *Alias:* `{alias}`")
        if cbu:
            lines.append(f"  👉 *CVU/CBU:* `{cbu}`")
        if holder:
            lines.append(f"  👤 *Titular:* {holder}")
        if usdt:
            lines.append(f"• *Cripto / USDT:* `{usdt}`")
        if extra:
            lines.append(f"• {extra}")
        return "\n".join(lines)

    return (
        "• Transferencia Bancaria / CVU / CBU\n"
        "• Mercado Pago\n"
        "• Binance USDT / Cripto"
    )

def get_whatsapp_templates() -> Dict[str, Dict[str, Any]]:
    """Devuelve todas las plantillas registradas combinando base de datos y predeterminadas."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM whatsapp_templates ORDER BY template_key ASC").fetchall()
        result = {}
        for r in rows:
            result[r["template_key"]] = dict(r)
        
        for k, def_data in DEFAULT_WHATSAPP_TEMPLATES.items():
            if k not in result:
                result[k] = {
                    "template_key": k,
                    "title": def_data["title"],
                    "content": def_data["content"],
                    "description": def_data["description"]
                }
        return result
    finally:
        conn.close()

def get_whatsapp_template(template_key: str) -> Dict[str, Any]:
    """Obtiene una plantilla individual por su clave."""
    conn = get_connection()
    k = template_key.strip().lower()
    try:
        row = conn.execute("SELECT * FROM whatsapp_templates WHERE template_key = ?", (k,)).fetchone()
        if row:
            return dict(row)
        if k in DEFAULT_WHATSAPP_TEMPLATES:
            d = DEFAULT_WHATSAPP_TEMPLATES[k]
            return {"template_key": k, "title": d["title"], "content": d["content"], "description": d["description"]}
        return {"template_key": k, "title": k.title(), "content": "", "description": ""}
    finally:
        conn.close()

def save_whatsapp_template(template_key: str, content: str, title: str = "", description: str = "") -> bool:
    """Guarda o actualiza una plantilla de WhatsApp."""
    conn = get_connection()
    k = template_key.strip().lower()
    final_title = title.strip() or DEFAULT_WHATSAPP_TEMPLATES.get(k, {}).get("title", k.title())
    final_desc = description.strip() or DEFAULT_WHATSAPP_TEMPLATES.get(k, {}).get("description", "")
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_templates (template_key, title, content, description, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_key) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    description = excluded.description,
                    updated_at = CURRENT_TIMESTAMP
            """, (k, final_title, content.strip(), final_desc))
            return True
    finally:
        conn.close()

def reset_whatsapp_template(template_key: str) -> bool:
    """Restaura una plantilla a su valor predeterminado del sistema."""
    k = template_key.strip().lower()
    if k not in DEFAULT_WHATSAPP_TEMPLATES:
        return False
    d = DEFAULT_WHATSAPP_TEMPLATES[k]
    return save_whatsapp_template(k, d["content"], d["title"], d["description"])

def render_dynamic_template(template_str: str, context: Dict[str, Any]) -> str:
    """Reemplaza etiquetas dinámicas {tag} en la plantilla de WhatsApp sin fallar si faltan variables."""
    out = template_str
    for key, val in context.items():
        placeholder = f"{{{key}}}"
        out = out.replace(placeholder, str(val) if val is not None else "")
    
    out_lines = []
    for line in out.split("\n"):
        if line.strip() in ("👤 *Perfil:*", "👤 *Perfil Asignado:*", "🔒 *PIN:*", "🔒 *PIN de Perfil:*", "💰 *Valor:*", "💰 *Monto a Renovar:*"):
            continue
        out_lines.append(line)
    
    return "\n".join(out_lines).strip()

def generate_whatsapp_message(
    account_or_id: Union[int, str, Dict[str, Any]],
    message_type: str = "entrega",
    payment_methods: str = ""
) -> Dict[str, Any]:
    """Genera plantillas profesionales y enlaces directos con 1 clic para WhatsApp (wa.me)."""
    if isinstance(account_or_id, dict):
        acc = account_or_id
    else:
        acc = get_account_detail(account_or_id)
        if not acc:
            return {"success": False, "error": f"No se encontró la cuenta o servicio '{account_or_id}'"}

    client_name = acc.get("client_name") or "Estimado/a"
    platform = acc.get("platform") or "Streaming"
    email = acc.get("email") or ""
    password = acc.get("password") or ""
    profile = acc.get("profile_name") or ""
    pin = acc.get("profile_pin") or ""
    expiry = acc.get("expiry_date") or ""
    price = acc.get("price") or ""
    raw_phone = acc.get("whatsapp") or ""
    clean_phone = clean_whatsapp_phone(raw_phone)
    days_rem = acc.get("days_remaining")

    m_type = message_type.strip().lower()

    if m_type in ("cobro", "recordatorio", "vencimiento"):
        tpl_key = "cobro"
    elif m_type in ("reemplazo", "soporte", "caida"):
        tpl_key = "reemplazo"
    else:
        tpl_key = "entrega"

    tpl = get_whatsapp_template(tpl_key)
    tpl_content = tpl.get("content") or DEFAULT_WHATSAPP_TEMPLATES.get(tpl_key, {}).get("content", "")

    # Días restantes formato
    days_str = ""
    if days_rem is not None:
        if days_rem == 0:
            days_str = " (¡Vence HOY!)"
        elif days_rem == 1:
            days_str = " (vence mañana)"
        elif days_rem > 0:
            days_str = f" (vence en {days_rem} días)"
        else:
            days_str = f" (vencida hace {abs(days_rem)} días)"

    pm_text = payment_methods.strip() if payment_methods else get_formatted_payment_methods()
    p_settings = get_payment_settings()
    price_str = price or format_ars(acc.get("price")) or "Consultar valor"

    context = {
        "cliente": client_name,
        "plataforma": platform,
        "email": email,
        "password": password,
        "perfil": profile,
        "pin": pin,
        "vencimiento": expiry,
        "dias_restantes": days_str,
        "monto": price_str,
        "metodos_pago": pm_text,
        "alias_mp": p_settings.get("alias_mp", ""),
        "cbu": p_settings.get("cvu_cbu", ""),
        "titular": p_settings.get("account_holder", ""),
        "banco": p_settings.get("bank_name", ""),
        "usdt": p_settings.get("usdt_address", "")
    }

    msg = render_dynamic_template(tpl_content, context)

    encoded_text = urllib.parse.quote(msg)
    if clean_phone:
        wa_url = f"https://wa.me/{clean_phone}?text={encoded_text}"
    else:
        wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"

    return {
        "success": True,
        "client_name": client_name,
        "platform": platform,
        "email": email,
        "whatsapp": raw_phone,
        "clean_phone": clean_phone,
        "message_type": m_type,
        "message_text": msg,
        "wa_link": wa_url
    }

# ==========================================
# 7. Ficha 360° del Cliente y Cobro Consolidado
# ==========================================
def generate_consolidated_billing_whatsapp(
    client_id_or_dict: Union[str, int, Dict[str, Any]],
    payment_methods: str = ""
) -> Dict[str, Any]:
    """Genera un mensaje agrupado de cobro y enlace de 1 clic (wa.me) para clientes con 1 o más servicios."""
    if isinstance(client_id_or_dict, dict) and "client" in client_id_or_dict:
        profile = client_id_or_dict
    else:
        profile = get_client_360_profile(client_id_or_dict)

    if not profile or not profile.get("client"):
        return {"success": False, "error": "No se encontró el cliente especificado."}

    client = profile["client"]
    client_name = client.get("name") or "Estimado/a"
    raw_phone = client.get("whatsapp") or ""
    clean_phone = client.get("clean_whatsapp") or clean_whatsapp_phone(raw_phone)
    active_accounts = profile.get("active_accounts", [])

    if not active_accounts:
        return {
            "success": False,
            "client_name": client_name,
            "whatsapp": raw_phone,
            "clean_phone": clean_phone,
            "error": "El cliente no tiene suscripciones activas registradas actualmente."
        }

    total_amount = sum(a.get("price_num", 0.0) for a in active_accounts)

    services_lines = []
    for a in active_accounts:
        plat = a.get("platform", "Servicio")
        perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
        pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
        vence = a.get("expiry_date") or "-"
        d_lbl = a.get("days_label", "")
        d_str = f" ({d_lbl})" if d_lbl else ""
        price_str = a.get("price_formatted") or format_ars(a.get("price"))
        services_lines.append(f"• *{plat}*{perf}: `{a.get('email')}`\n  Vence: {vence}{d_str} | Valor: {price_str}")

    services_text = "\n".join(services_lines)

    pm_text = payment_methods.strip() if payment_methods else get_formatted_payment_methods()
    p_settings = get_payment_settings()

    tpl = get_whatsapp_template("cobro_consolidado")
    tpl_content = tpl.get("content") or DEFAULT_WHATSAPP_TEMPLATES.get("cobro_consolidado", {}).get("content", "")

    context = {
        "cliente": client_name,
        "servicios_lista": services_text,
        "monto": format_ars(total_amount),
        "metodos_pago": pm_text,
        "cuentas_cantidad": len(active_accounts),
        "alias_mp": p_settings.get("alias_mp", ""),
        "cbu": p_settings.get("cvu_cbu", ""),
        "titular": p_settings.get("account_holder", ""),
        "banco": p_settings.get("bank_name", ""),
        "usdt": p_settings.get("usdt_address", "")
    }

    msg = render_dynamic_template(tpl_content, context)

    encoded_text = urllib.parse.quote(msg)
    if clean_phone:
        wa_url = f"https://wa.me/{clean_phone}?text={encoded_text}"
    else:
        wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"

    return {
        "success": True,
        "client_name": client_name,
        "whatsapp": raw_phone,
        "clean_phone": clean_phone,
        "accounts_count": len(active_accounts),
        "total_amount": total_amount,
        "total_amount_formatted": format_ars(total_amount),
        "message_text": msg,
        "wa_link": wa_url
    }

def get_client_360_profile(query_or_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Obtiene la Ficha 360° completa de un cliente: salud, LTV en ARS, cuentas activas, historial de pagos y cobro consolidado."""
    conn = get_connection()
    try:
        with conn:
            client_row = None
            q = str(query_or_id).strip()
            if isinstance(query_or_id, int) or q.isdigit():
                client_row = conn.execute("SELECT * FROM clients WHERE id = ?", (int(q),)).fetchone()

            if not client_row:
                clean_q = q.replace(" ", "").replace("-", "")
                client_row = conn.execute("""
                    SELECT * FROM clients
                    WHERE lower(name) LIKE lower(?)
                    OR lower(client_code) = lower(?)
                    OR lower(telegram) = lower(?)
                    OR replace(replace(whatsapp, ' ', ''), '-', '') LIKE ?
                    ORDER BY id ASC LIMIT 1
                """, (f"%{q}%", q, f"@{q.lstrip('@')}", f"%{clean_q}%")).fetchone()

            if not client_row:
                return None

            client = dict(client_row)
            client_id = client["id"]
            client["clean_whatsapp"] = clean_whatsapp_phone(client.get("whatsapp", ""))
            client["client_type_label"] = "👔 Revendedor" if "revend" in (client.get("client_type") or "").lower() else "👤 Consumidor Final"

            # 2. Cuentas asociadas
            acc_rows = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE client_id = ?
                ORDER BY expiry_date ASC
            """, (client_id,)).fetchall()

            active_accounts = []
            past_accounts = []
            has_debt = False
            has_expiring_soon = False

            for r in acc_rows:
                a = dict(r)
                price_num = parse_money(a.get("price"))
                cost_num = parse_money(a.get("cost"))
                a["price_num"] = price_num
                a["cost_num"] = cost_num
                a["price_formatted"] = format_ars(price_num)
                a["cost_formatted"] = format_ars(cost_num)

                days_rem = None
                days_lbl = "Sin fecha"
                badge_class = "badge-ok"
                if a.get("expiry_date"):
                    try:
                        exp = datetime.strptime(a["expiry_date"], "%Y-%m-%d").date()
                        diff = (exp - date.today()).days
                        days_rem = diff
                        if diff < 0:
                            days_lbl = f"Vencida hace {abs(diff)}d"
                            badge_class = "badge-danger"
                            has_debt = True
                        elif diff == 0:
                            days_lbl = "¡Vence HOY!"
                            badge_class = "badge-warn"
                            has_expiring_soon = True
                        elif diff == 1:
                            days_lbl = "Vence mañana"
                            badge_class = "badge-warn"
                            has_expiring_soon = True
                        elif diff == 2:
                            days_lbl = "Vence en 2 días"
                            badge_class = "badge-warn"
                            has_expiring_soon = True
                        else:
                            days_lbl = f"Vence en {diff} días"
                            badge_class = "badge-ok"
                    except Exception:
                        pass

                a["days_remaining"] = days_rem
                a["days_label"] = days_lbl
                a["badge_class"] = badge_class

                if a.get("payment_status") == "pendiente":
                    has_debt = True

                # Generar links individuales
                wa_cobro = generate_whatsapp_message(a, message_type="cobro")
                a["wa_cobro_link"] = wa_cobro.get("wa_link", "")
                wa_entrega = generate_whatsapp_message(a, message_type="entrega")
                a["wa_entrega_link"] = wa_entrega.get("wa_link", "")

                if a.get("status") in ("ocupada", "vencida", "caida"):
                    active_accounts.append(a)
                else:
                    past_accounts.append(a)

            # 3. Historial de Pagos y LTV
            pay_rows = conn.execute("""
                SELECT p.*, a.platform as account_platform, a.email as account_email
                FROM payments p
                LEFT JOIN streaming_accounts a ON p.account_id = a.id
                WHERE p.client_id = ?
                ORDER BY p.created_at DESC
            """, (client_id,)).fetchall()

            payments = []
            ltv_amount = 0.0
            total_profit = 0.0
            total_cost = 0.0

            for pr in pay_rows:
                p = dict(pr)
                amt = float(p.get("amount") or 0.0)
                prof = float(p.get("profit") or 0.0)
                cst = float(p.get("cost") or 0.0)
                ltv_amount += amt
                total_profit += prof
                total_cost += cst

                p["amount_formatted"] = format_ars(amt)
                p["profit_formatted"] = f"+{format_ars(prof)}" if prof >= 0 else format_ars(prof)
                p["cost_formatted"] = format_ars(cst)
                payments.append(p)

            monthly_spend = sum(a["price_num"] for a in active_accounts)

            # 4. Semáforo de Salud
            if has_debt:
                health = {
                    "code": "moroso",
                    "label": "🔴 Con Deuda / Vencido",
                    "badge_class": "badge-danger",
                    "summary": "Tiene servicios vencidos o cobros pendientes."
                }
            elif has_expiring_soon:
                health = {
                    "code": "por_vencer",
                    "label": "🟡 Por Vencer (Próximas 48hs)",
                    "badge_class": "badge-warn",
                    "summary": "Tiene servicios próximos a vencer."
                }
            elif active_accounts:
                health = {
                    "code": "al_dia",
                    "label": "🟢 Al Día",
                    "badge_class": "badge-ok",
                    "summary": "Todas sus suscripciones activas están al día."
                }
            else:
                health = {
                    "code": "sin_servicios",
                    "label": "⚪ Sin Servicios Activos",
                    "badge_class": "badge-secondary",
                    "summary": "No tiene servicios activos en este momento."
                }

            profile_data = {
                "client": client,
                "health_status": health,
                "financial_kpis": {
                    "ltv_amount": ltv_amount,
                    "ltv_formatted": format_ars(ltv_amount),
                    "total_profit": total_profit,
                    "total_profit_formatted": f"+{format_ars(total_profit)}" if total_profit >= 0 else format_ars(total_profit),
                    "total_cost": total_cost,
                    "total_cost_formatted": format_ars(total_cost),
                    "payments_count": len(payments),
                    "monthly_committed_spend": monthly_spend,
                    "monthly_committed_spend_formatted": format_ars(monthly_spend),
                    "last_payment": payments[0] if payments else None
                },
                "active_accounts": active_accounts,
                "past_accounts": past_accounts,
                "payments_history": payments[:20]
            }

            # 5. Cobro consolidado
            billing = generate_consolidated_billing_whatsapp(profile_data)
            profile_data["consolidated_billing"] = billing

            return profile_data
    finally:
        conn.close()

# ==========================================
# 7. Gestión de Pantallas Compartidas (Paso 3)
# ==========================================
def create_master_account_with_profiles(
    platform: str,
    email: str,
    password: str,
    profile_count: int = 4,
    pins: Union[str, List[str]] = "",
    cost: str = "",
    notes: str = ""
) -> List[Dict[str, Any]]:
    """Crea una cuenta madre en stock y genera automáticamente sus N casilleros/perfiles libres."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    clean_email = email.strip()
    clean_password = password.strip()
    
    pin_list = []
    if isinstance(pins, list):
        pin_list = [str(p).strip() for p in pins]
    elif isinstance(pins, str) and pins.strip():
        parts = re.split(r'[,;\s]+', pins.strip())
        pin_list = [p for p in parts if p]

    created = []
    try:
        with conn:
            for i in range(profile_count):
                prof_name = f"Perfil {i + 1}"
                prof_pin = pin_list[i] if i < len(pin_list) else (pin_list[0] if len(pin_list) == 1 else "")
                prof_cost = cost if i == 0 else ""

                cursor = conn.execute("""
                    INSERT INTO streaming_accounts (
                        platform, email, password, profile_name, profile_pin,
                        status, payment_status, cost, notes
                    ) VALUES (?, ?, ?, ?, ?, 'libre', 'pagado', ?, ?)
                """, (clean_platform, clean_email, clean_password, prof_name, prof_pin, prof_cost, notes.strip()))
                
                acc_id = cursor.lastrowid
                row = conn.execute("SELECT * FROM streaming_accounts WHERE id = ?", (acc_id,)).fetchone()
                created.append(dict(row))
        return created
    finally:
        conn.close()

def assign_next_free_profile(
    client_name: str,
    platform: str,
    expiry_date: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    price: str = "",
    notes: str = ""
) -> Optional[Dict[str, Any]]:
    """Busca el primer perfil libre disponible de una plataforma y lo asigna a un cliente."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    try:
        with conn:
            free_slot = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (clean_platform,)).fetchone()

            if not free_slot:
                return None

            slot = dict(free_slot)
            slot_id = slot["id"]

            client = find_or_create_client(
                name=client_name,
                whatsapp=whatsapp,
                telegram=telegram,
                client_type=client_type,
                notes=notes
            )

            price_num = parse_money(price)
            cost_num = parse_money(slot.get("cost"))
            if price_num == 0.0 or cost_num == 0.0:
                s_price, s_cost = get_suggested_price(clean_platform, "pantalla", client_type)
                if price_num == 0.0 and s_price > 0.0:
                    price_num = s_price
                    price = format_ars(price_num)
                if cost_num == 0.0 and s_cost > 0.0:
                    cost_num = s_cost

            today_str = date.today().isoformat()
            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                    start_date = ?, expiry_date = ?, price = ?, notes = ?,
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client["id"], today_str, expiry_date.strip(), price.strip(), notes.strip(), slot_id))

            profit_num = price_num - cost_num
            if price_num > 0:
                conn.execute("""
                    INSERT INTO payments (account_id, client_id, amount, cost, profit, payment_method, notes)
                    VALUES (?, ?, ?, ?, ?, 'Inicial', 'Venta perfil compartido')
                """, (slot_id, client["id"], price_num, cost_num, profit_num))

            fresh = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE a.id = ?
            """, (slot_id,)).fetchone()
            return dict(fresh)
    finally:
        conn.close()

def get_shared_screens_overview(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    """Agrupa las cuentas por correo madre y muestra la ocupación de cada una."""
    conn = get_connection()
    today = date.today()
    try:
        query = """
            SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
            FROM streaming_accounts a
            LEFT JOIN clients c ON a.client_id = c.id
        """
        params = []
        if platform:
            query += " WHERE lower(a.platform) = lower(?)"
            params.append(platform.strip())
        query += " ORDER BY a.platform ASC, a.email ASC, a.id ASC"

        rows = conn.execute(query, params).fetchall()

        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in rows:
            d = dict(r)
            try:
                exp = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
                d["days_remaining"] = (exp - today).days
            except Exception:
                d["days_remaining"] = None
            key = (d["platform"], d["email"])
            grouped.setdefault(key, []).append(d)

        overview = []
        for (plat, mail), profs in grouped.items():
            occupied = [p for p in profs if p["status"] == "ocupada"]
            free = [p for p in profs if p["status"] == "libre"]
            fallen = [p for p in profs if p["status"] == "caida"]
            passw = profs[0]["password"] if profs else ""
            
            overview.append({
                "platform": plat,
                "email": mail,
                "password": passw,
                "total_profiles": len(profs),
                "occupied_count": len(occupied),
                "free_count": len(free),
                "fallen_count": len(fallen),
                "occupancy_rate": round((len(occupied) / len(profs)) * 100, 1) if profs else 0.0,
                "profiles": profs
            })
        return overview
    finally:
        conn.close()

def mark_entire_master_account_fallen(email_or_query: str, reason: str = "Caída de cuenta completa") -> Dict[str, Any]:
    """Marca como caídas todas las pantallas asociadas a un correo madre y lista los clientes afectados."""
    conn = get_connection()
    q = email_or_query.strip()
    try:
        with conn:
            rows = conn.execute("""
                SELECT a.*, c.name as client_name, c.whatsapp, c.telegram
                FROM streaming_accounts a
                LEFT JOIN clients c ON a.client_id = c.id
                WHERE lower(a.email) LIKE lower(?) AND a.status IN ('ocupada', 'libre')
            """, (f"%{q}%",)).fetchall()

            if not rows:
                return {"success": False, "error": f"No se encontraron cuentas activas con el correo '{q}'"}

            affected_clients = []
            for r in rows:
                d = dict(r)
                if d["status"] == "ocupada":
                    affected_clients.append({
                        "account_id": d["id"],
                        "client_name": d.get("client_name"),
                        "profile_name": d.get("profile_name"),
                        "whatsapp": d.get("whatsapp"),
                        "telegram": d.get("telegram"),
                        "platform": d.get("platform")
                    })
                conn.execute("""
                    UPDATE streaming_accounts 
                    SET status = 'caida', notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (reason.strip(), d["id"]))

            return {
                "success": True,
                "email": q,
                "total_profiles_affected": len(rows),
                "affected_clients": affected_clients
            }
    finally:
        conn.close()

# ==========================================
# 7. Módulo de Alerta Temprana de Stock Bajo
# ==========================================
def set_platform_min_stock(platform: str, min_stock: int) -> bool:
    """Establece o actualiza el umbral mínimo de stock para una plataforma."""
    conn = get_connection()
    plat = platform.strip()
    try:
        with conn:
            conn.execute("""
                INSERT INTO stock_thresholds (platform, min_stock, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform) DO UPDATE SET min_stock = excluded.min_stock, updated_at = CURRENT_TIMESTAMP
            """, (plat, max(0, min_stock)))
            return True
    except Exception as e:
        logger.error(f"Error estableciendo umbral de stock: {e}")
        return False
    finally:
        conn.close()

def get_stock_thresholds() -> Dict[str, int]:
    """Obtiene el diccionario de umbrales mínimos configurados por plataforma."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT platform, min_stock FROM stock_thresholds").fetchall()
        return {r["platform"]: r["min_stock"] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()

def get_stock_health_summary(default_min_stock: int = 2) -> Dict[str, Any]:
    """
    Analiza todo el catálogo y devuelve un diagnóstico de salud del stock por plataforma:
    - Agotadas (0 disponibles) -> 🔴
    - Stock Bajo (<= umbral mínimo) -> 🟡
    - Stock Óptimo (> umbral mínimo) -> 🟢
    """
    conn = get_connection()
    try:
        thresholds_map = {}
        try:
            th_rows = conn.execute("SELECT platform, min_stock FROM stock_thresholds").fetchall()
            thresholds_map = {r["platform"].strip().lower(): r["min_stock"] for r in th_rows}
        except Exception:
            pass

        plat_rows = conn.execute("""
            SELECT DISTINCT platform FROM streaming_accounts
            WHERE platform != ''
            ORDER BY platform ASC
        """).fetchall()

        all_platforms = [r["platform"].strip() for r in plat_rows if r["platform"].strip()]

        free_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'libre'
            GROUP BY platform
        """).fetchall()
        free_map = {r["platform"].strip().lower(): r["count"] for r in free_rows}

        occupied_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'ocupada'
            GROUP BY platform
        """).fetchall()
        occupied_map = {r["platform"].strip().lower(): r["count"] for r in occupied_rows}

        fallen_rows = conn.execute("""
            SELECT platform, COUNT(*) as count 
            FROM streaming_accounts 
            WHERE status = 'caida'
            GROUP BY platform
        """).fetchall()
        fallen_map = {r["platform"].strip().lower(): r["count"] for r in fallen_rows}

        platforms_summary = []
        out_of_stock_count = 0
        low_stock_count = 0
        optimal_count = 0
        total_free_units = 0

        for p_name in all_platforms:
            key = p_name.lower()
            free_cnt = free_map.get(key, 0)
            occupied_cnt = occupied_map.get(key, 0)
            fallen_cnt = fallen_map.get(key, 0)
            min_thresh = thresholds_map.get(key, thresholds_map.get("default", default_min_stock))
            total_free_units += free_cnt

            if free_cnt == 0:
                status = "agotado"
                badge = "🔴 Agotado"
                out_of_stock_count += 1
            elif free_cnt <= min_thresh:
                status = "bajo"
                badge = "🟡 Stock Bajo"
                low_stock_count += 1
            else:
                status = "optimo"
                badge = "🟢 Óptimo"
                optimal_count += 1

            platforms_summary.append({
                "platform": p_name,
                "free_count": free_cnt,
                "occupied_count": occupied_cnt,
                "fallen_count": fallen_cnt,
                "min_threshold": min_thresh,
                "status": status,
                "badge": badge,
                "needs_alert": status in ("agotado", "bajo")
            })

        status_order = {"agotado": 0, "bajo": 1, "optimo": 2}
        platforms_summary.sort(key=lambda x: (status_order.get(x["status"], 3), -x["occupied_count"], x["platform"]))

        alert_platforms = [p for p in platforms_summary if p["needs_alert"]]

        return {
            "total_platforms": len(all_platforms),
            "total_free_units": total_free_units,
            "out_of_stock_count": out_of_stock_count,
            "low_stock_count": low_stock_count,
            "optimal_count": optimal_count,
            "has_alerts": len(alert_platforms) > 0,
            "alert_platforms": alert_platforms,
            "platforms": platforms_summary
        }
    finally:
        conn.close()

# ==========================================
# 8. Módulo de Importación y Exportación Masiva (Excel / CSV)
# ==========================================
def _detect_csv_delimiter(content: str) -> str:
    """Detecta inteligentemente si el archivo viene separado por coma, punto y coma o tabulación."""
    first_line = content.strip().split("\n")[0] if content else ""
    if ";" in first_line and first_line.count(";") >= first_line.count(","):
        return ";"
    elif "\t" in first_line:
        return "\t"
    return ","

def export_active_accounts_csv() -> str:
    """Exporta todas las cuentas y clientes activos a formato CSV con UTF-8 BOM para Excel."""
    accounts = get_active_accounts()
    output = io.StringIO()
    output.write("\ufeff")
    
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "ID", "Cliente", "Codigo_Cliente", "Tipo_Cliente", "WhatsApp", "Telegram",
        "Plataforma", "Perfil", "PIN", "Correo", "Contrasena", "Fecha_Inicio",
        "Fecha_Vence", "Dias_Restantes", "Precio_Cobrado", "Costo_Proveedor",
        "Ganancia_Estimada", "Estado_Pago", "Notas"
    ])

    for a in accounts:
        price_num = parse_money(a.get("price"))
        cost_num = parse_money(a.get("cost"))
        profit_num = price_num - cost_num
        writer.writerow([
            a.get("id", ""),
            a.get("client_name", ""),
            a.get("client_code", ""),
            a.get("client_type", "consumidor_final"),
            a.get("whatsapp", ""),
            a.get("telegram", ""),
            a.get("platform", ""),
            a.get("profile_name", ""),
            a.get("profile_pin", ""),
            a.get("email", ""),
            a.get("password", ""),
            a.get("start_date", ""),
            a.get("expiry_date", ""),
            a.get("days_remaining", ""),
            f"{price_num:.2f}",
            f"{cost_num:.2f}",
            f"{profit_num:.2f}",
            a.get("payment_status", "pagado"),
            a.get("notes", "")
        ])

    return output.getvalue()

def export_free_stock_csv() -> str:
    """Exporta el inventario de cuentas y perfiles libres a CSV con UTF-8 BOM."""
    stock = get_free_stock()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["ID", "Plataforma", "Perfil", "PIN", "Correo", "Contrasena", "Costo", "Notas"])

    for s in stock:
        writer.writerow([
            s.get("id", ""),
            s.get("platform", ""),
            s.get("profile_name", ""),
            s.get("profile_pin", ""),
            s.get("email", ""),
            s.get("password", ""),
            s.get("cost", ""),
            s.get("notes", "")
        ])

    return output.getvalue()

def export_transactions_csv() -> str:
    """Exporta el historial de pagos y cobros registrados a CSV con UTF-8 BOM."""
    conn = get_connection()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "ID_Transaccion", "Fecha_Hora", "Cliente", "Tipo_Cliente", "Plataforma",
        "Monto_Cobrado_ARS", "Costo_ARS", "Ganancia_Neta_ARS", "Metodo_Pago", "Notas"
    ])

    try:
        rows = conn.execute("""
            SELECT p.*, c.name as client_name, c.client_type, a.platform
            FROM payments p
            LEFT JOIN clients c ON p.client_id = c.id
            LEFT JOIN streaming_accounts a ON p.account_id = a.id
            ORDER BY p.id DESC
        """).fetchall()

        for r in rows:
            writer.writerow([
                r["id"],
                r["created_at"],
                r["client_name"] or "Venta General",
                r["client_type"] or "",
                r["platform"] or "",
                f"{r['amount']:.2f}",
                f"{r['cost']:.2f}",
                f"{r['profit']:.2f}",
                r["payment_method"] or "Transferencia",
                r["notes"] or ""
            ])
    finally:
        conn.close()

    return output.getvalue()

def export_full_backup_json() -> str:
    """Genera una copia de seguridad íntegra en formato JSON de todas las tablas."""
    conn = get_connection()
    try:
        clients = [dict(r) for r in conn.execute("SELECT * FROM clients").fetchall()]
        accounts = [dict(r) for r in conn.execute("SELECT * FROM streaming_accounts").fetchall()]
        payments = [dict(r) for r in conn.execute("SELECT * FROM payments").fetchall()]
        thresholds = [dict(r) for r in conn.execute("SELECT * FROM stock_thresholds").fetchall()]
        catalog = [dict(r) for r in conn.execute("SELECT * FROM price_catalog").fetchall()]
        combos = get_combos()
        
        backup_data = {
            "backup_version": "2.8.0",
            "currency": "ARS",
            "created_at": datetime.now().isoformat(),
            "stats": {
                "clients_count": len(clients),
                "accounts_count": len(accounts),
                "payments_count": len(payments),
                "catalog_count": len(catalog),
                "combos_count": len(combos)
            },
            "clients": clients,
            "streaming_accounts": accounts,
            "payments": payments,
            "stock_thresholds": thresholds,
            "price_catalog": catalog,
            "combos": combos
        }
        return json.dumps(backup_data, indent=2, ensure_ascii=False)
    finally:
        conn.close()

def get_csv_template_stock() -> str:
    """Plantilla CSV modelo para cargar inventario libre en Excel (montos en ARS)."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",")
    writer.writerow(["Plataforma", "Correo", "Contrasena", "Perfil", "PIN", "Costo", "Notas"])
    writer.writerow(["Netflix 4K", "cuenta1@ejemplo.com", "ClaveSegura123", "Perfil 1", "1234", "3200", "Proveedor Central"])
    writer.writerow(["Disney+", "cuenta2@ejemplo.com", "ClaveSegura456", "", "", "2000", "Cuenta Completa"])
    writer.writerow(["Spotify Familiar", "cuenta3@ejemplo.com", "ClaveSegura789", "", "", "2500", "Plan Familiar"])
    return output.getvalue()

def get_csv_template_sales() -> str:
    """Plantilla CSV modelo para migrar o cargar ventas con clientes en Excel (montos en ARS)."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "Cliente", "WhatsApp", "Telegram", "Tipo_Cliente", "Plataforma",
        "Correo", "Contrasena", "Perfil", "PIN", "Vencimiento", "Precio", "Costo", "Notas"
    ])
    writer.writerow([
        "Juan Perez", "+5491112345678", "@juanp", "consumidor_final", "Netflix 4K",
        "net@ejemplo.com", "Clave123", "Perfil 1", "1234", "2026-10-15", "5500", "3200", "Cliente puntual"
    ])
    writer.writerow([
        "Matias Revendedor", "+5491187654321", "@matias_reseller", "revendedor", "Disney+",
        "dis@ejemplo.com", "Pass456", "", "", "2026-10-20", "3000", "2000", "Lote mensual"
    ])
    return output.getvalue()

def _parse_date_flexible(val: str) -> str:
    """Convierte formatos como DD/MM/YYYY, DD-MM-YYYY a YYYY-MM-DD."""
    clean = val.strip()
    if not clean:
        return (date.today() + timedelta(days=30)).isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(clean, fmt).date().isoformat()
        except ValueError:
            pass
    return (date.today() + timedelta(days=30)).isoformat()

def import_free_stock_csv(csv_content: str) -> Dict[str, Any]:
    """
    Importa masivamente cuentas al stock libre desde contenido CSV o TSV copiado de Excel.
    Reconoce encabezados flexibles en español e inglés.
    """
    content = csv_content.lstrip("\ufeff").strip()
    if not content:
        return {"success": False, "error": "El contenido CSV está vacío."}

    delim = _detect_csv_delimiter(content)
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    
    rows = list(reader)
    if not rows:
        return {"success": False, "error": "No se encontraron filas de datos."}

    headers = [h.strip().lower() for h in rows[0]]
    
    def find_col_idx(aliases: List[str]) -> int:
        for idx, h in enumerate(headers):
            for a in aliases:
                if a in h:
                    return idx
        return -1

    col_plat = find_col_idx(["plat", "serv", "service"])
    col_mail = find_col_idx(["corr", "email", "mail", "user", "cuenta"])
    col_pass = find_col_idx(["contra", "pass", "clave"])
    col_perf = find_col_idx(["perf", "pantalla", "screen"])
    col_pin = find_col_idx(["pin"])
    col_cost = find_col_idx(["cost", "prov"])
    col_notes = find_col_idx(["not", "obs", "coment"])

    if col_plat == -1 or col_mail == -1:
        return {
            "success": False,
            "error": "No se encontraron columnas obligatorias (Plataforma y Correo/Email). Verifica los encabezados."
        }

    imported = 0
    skipped = 0
    errors = []

    conn = get_connection()
    try:
        with conn:
            for row_num, r in enumerate(rows[1:], start=2):
                if not any(r):
                    continue
                try:
                    plat = r[col_plat].strip() if col_plat < len(r) else ""
                    mail = r[col_mail].strip() if col_mail < len(r) else ""
                    pwd = r[col_pass].strip() if col_pass != -1 and col_pass < len(r) else "123456"
                    perf = r[col_perf].strip() if col_perf != -1 and col_perf < len(r) else ""
                    pin = r[col_pin].strip() if col_pin != -1 and col_pin < len(r) else ""
                    cost = r[col_cost].strip() if col_cost != -1 and col_cost < len(r) else ""
                    notes = r[col_notes].strip() if col_notes != -1 and col_notes < len(r) else "Carga masiva CSV"

                    if not plat or not mail:
                        skipped += 1
                        continue

                    conn.execute("""
                        INSERT INTO streaming_accounts (
                            platform, email, password, profile_name, profile_pin,
                            status, payment_status, cost, notes
                        ) VALUES (?, ?, ?, ?, ?, 'libre', 'pagado', ?, ?)
                    """, (plat, mail, pwd, perf, pin, cost, notes))
                    imported += 1
                except Exception as ex:
                    errors.append(f"Línea {row_num}: {str(ex)}")

        return {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10]
        }
    finally:
        conn.close()

def import_sales_csv(csv_content: str) -> Dict[str, Any]:
    """
    Importa masivamente clientes y suscripciones vendidas desde un archivo CSV o planilla.
    Crea automáticamente los clientes, asigna las cuentas con su fecha de vencimiento y registra los pagos.
    """
    content = csv_content.lstrip("\ufeff").strip()
    if not content:
        return {"success": False, "error": "El contenido CSV está vacío."}

    delim = _detect_csv_delimiter(content)
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    rows = list(reader)
    if not rows:
        return {"success": False, "error": "No se encontraron filas de datos."}

    headers = [h.strip().lower() for h in rows[0]]

    def find_col(aliases: List[str]) -> int:
        for idx, h in enumerate(headers):
            for a in aliases:
                if a in h:
                    return idx
        return -1

    c_client = find_col(["client", "nom", "user"])
    c_wa = find_col(["whats", "tel", "cel", "phone", "movil"])
    c_tg = find_col(["teleg", "tg"])
    c_type = find_col(["tipo", "type"])
    c_plat = find_col(["plat", "serv"])
    c_mail = find_col(["corr", "email", "mail", "cuenta"])
    c_pass = find_col(["contra", "pass", "clave"])
    c_perf = find_col(["perf", "pantalla"])
    c_pin = find_col(["pin"])
    c_venc = find_col(["venc", "expir", "fecha"])
    c_price = find_col(["prec", "price", "monto", "cobro"])
    c_cost = find_col(["cost", "prov"])
    c_notes = find_col(["not", "obs"])

    if c_client == -1 or c_plat == -1 or c_mail == -1:
        return {
            "success": False,
            "error": "Faltan columnas esenciales (Cliente, Plataforma, Correo). Verifica la estructura del archivo."
        }

    imported = 0
    skipped = 0
    errors = []

    for row_num, r in enumerate(rows[1:], start=2):
        if not any(r):
            continue
        try:
            client_name = r[c_client].strip() if c_client < len(r) else ""
            plat = r[c_plat].strip() if c_plat < len(r) else ""
            mail = r[c_mail].strip() if c_mail < len(r) else ""

            if not client_name or not plat or not mail:
                skipped += 1
                continue

            wa = r[c_wa].strip() if c_wa != -1 and c_wa < len(r) else ""
            tg = r[c_tg].strip() if c_tg != -1 and c_tg < len(r) else ""
            ctype = r[c_type].strip().lower() if c_type != -1 and c_type < len(r) else "consumidor_final"
            if "revend" in ctype:
                ctype = "revendedor"
            else:
                ctype = "consumidor_final"

            pwd = r[c_pass].strip() if c_pass != -1 and c_pass < len(r) else "123456"
            perf = r[c_perf].strip() if c_perf != -1 and c_perf < len(r) else ""
            pin = r[c_pin].strip() if c_pin != -1 and c_pin < len(r) else ""
            raw_venc = r[c_venc].strip() if c_venc != -1 and c_venc < len(r) else ""
            venc = _parse_date_flexible(raw_venc)
            price = r[c_price].strip() if c_price != -1 and c_price < len(r) else "$5.00"
            cost = r[c_cost].strip() if c_cost != -1 and c_cost < len(r) else "$2.50"
            notes = r[c_notes].strip() if c_notes != -1 and c_notes < len(r) else "Importación masiva"

            res = register_streaming_sale(
                client_name=client_name,
                whatsapp=wa,
                telegram=tg,
                client_type=ctype,
                platform=plat,
                email=mail,
                password=pwd,
                profile_name=perf,
                profile_pin=pin,
                expiry_date=venc,
                price=price,
                cost=cost,
                notes=notes
            )
            if res:
                imported += 1
            else:
                skipped += 1
        except Exception as ex:
            errors.append(f"Línea {row_num}: {str(ex)}")

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:10]
    }

# ==========================================
# 10. Catálogo de Precios Oficiales y Combos (Paso 7 - v2.8.0)
# ==========================================
def get_price_catalog() -> List[Dict[str, Any]]:
    """Devuelve la lista completa de precios oficiales por plataforma en Pesos Argentinos (ARS)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM price_catalog
            ORDER BY platform ASC, service_type ASC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            profit_final = d["price_final"] - d["cost_price"]
            profit_reseller = d["price_reseller"] - d["cost_price"]
            d["profit_final"] = profit_final
            d["profit_reseller"] = profit_reseller
            d["cost_price_formatted"] = format_ars(d["cost_price"])
            d["price_final_formatted"] = format_ars(d["price_final"])
            d["price_reseller_formatted"] = format_ars(d["price_reseller"])
            result.append(d)
        return result
    finally:
        conn.close()

def upsert_catalog_price(
    platform: str,
    service_type: str = "pantalla",
    cost_price: Union[float, str] = 0.0,
    price_final: Union[float, str] = 0.0,
    price_reseller: Union[float, str] = 0.0,
    notes: str = ""
) -> Dict[str, Any]:
    """Crea o actualiza un precio sugerido en el catálogo para una plataforma y tipo de servicio."""
    conn = get_connection()
    clean_platform = platform.strip().title()
    clean_stype = service_type.strip().lower()
    if clean_stype not in ("pantalla", "cuenta_completa"):
        clean_stype = "pantalla"
    
    cost_val = parse_money(cost_price)
    final_val = parse_money(price_final)
    reseller_val = parse_money(price_reseller)
    
    try:
        with conn:
            conn.execute("""
                INSERT INTO price_catalog (platform, service_type, cost_price, price_final, price_reseller, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, service_type) DO UPDATE SET
                    cost_price = excluded.cost_price,
                    price_final = excluded.price_final,
                    price_reseller = excluded.price_reseller,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_platform, clean_stype, cost_val, final_val, reseller_val, notes.strip()))
            
            row = conn.execute("""
                SELECT * FROM price_catalog
                WHERE platform = ? AND service_type = ?
            """, (clean_platform, clean_stype)).fetchone()
            return dict(row)
    finally:
        conn.close()

def delete_catalog_price(price_id: int) -> bool:
    """Elimina una entrada del catálogo de precios."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM price_catalog WHERE id = ?", (price_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def get_suggested_price(
    platform: str,
    service_type: str = "pantalla",
    client_type: str = "consumidor_final"
) -> Tuple[float, float]:
    """Obtiene el precio de venta sugerido y el costo del catálogo para una plataforma.
    Retorna (precio_venta, costo) en Pesos Argentinos (ARS)."""
    conn = get_connection()
    clean_plat = platform.strip().lower()
    clean_stype = service_type.strip().lower()
    is_reseller = "revend" in client_type.lower()
    try:
        row = conn.execute("""
            SELECT cost_price, price_final, price_reseller
            FROM price_catalog
            WHERE lower(platform) = ? AND service_type = ?
            LIMIT 1
        """, (clean_plat, clean_stype)).fetchone()
        
        if not row:
            row = conn.execute("""
                SELECT cost_price, price_final, price_reseller
                FROM price_catalog
                WHERE lower(platform) = ?
                LIMIT 1
            """, (clean_plat,)).fetchone()
            
        if row:
            sale_price = row["price_reseller"] if is_reseller else row["price_final"]
            cost = row["cost_price"]
            return float(sale_price), float(cost)
        return 0.0, 0.0
    finally:
        conn.close()

def get_combos(only_active: bool = False) -> List[Dict[str, Any]]:
    """Lista todos los combos configurados con sus plataformas asociadas y precios en ARS."""
    conn = get_connection()
    try:
        query = "SELECT * FROM combos"
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY name ASC"
        
        combos_rows = conn.execute(query).fetchall()
        result = []
        for c in combos_rows:
            combo = dict(c)
            items_rows = conn.execute("""
                SELECT * FROM combo_items WHERE combo_id = ?
            """, (combo["id"],)).fetchall()
            combo["items"] = [dict(it) for it in items_rows]
            combo["platforms_list"] = [it["platform"] for it in combo["items"]]
            combo["platforms_str"] = " + ".join(combo["platforms_list"])
            combo["price_final_formatted"] = format_ars(combo["price_final"])
            combo["price_reseller_formatted"] = format_ars(combo["price_reseller"])
            result.append(combo)
        return result
    finally:
        conn.close()

def create_or_update_combo(
    name: str,
    description: str,
    price_final: Union[float, str],
    price_reseller: Union[float, str],
    platforms: List[Union[str, Dict[str, str]]],
    combo_id: Optional[int] = None
) -> Dict[str, Any]:
    """Crea o actualiza un combo promocional compuesto por varias plataformas."""
    conn = get_connection()
    clean_name = name.strip()
    p_final = parse_money(price_final)
    p_reseller = parse_money(price_reseller)
    
    try:
        with conn:
            if combo_id:
                conn.execute("""
                    UPDATE combos
                    SET name = ?, description = ?, price_final = ?, price_reseller = ?
                    WHERE id = ?
                """, (clean_name, description.strip(), p_final, p_reseller, combo_id))
                cid = combo_id
                conn.execute("DELETE FROM combo_items WHERE combo_id = ?", (cid,))
            else:
                cursor = conn.execute("""
                    INSERT INTO combos (name, description, price_final, price_reseller)
                    VALUES (?, ?, ?, ?)
                """, (clean_name, description.strip(), p_final, p_reseller))
                cid = cursor.lastrowid
                
            for p in platforms:
                if isinstance(p, dict):
                    plat_name = p.get("platform", "").strip().title()
                    stype = p.get("service_type", "pantalla").strip().lower()
                else:
                    plat_name = str(p).strip().title()
                    stype = "pantalla"
                
                if plat_name:
                    conn.execute("""
                        INSERT INTO combo_items (combo_id, platform, service_type)
                        VALUES (?, ?, ?)
                    """, (cid, plat_name, stype))
                    
            return {"success": True, "combo_id": cid, "name": clean_name}
    finally:
        conn.close()

def delete_combo(combo_id: int) -> bool:
    """Elimina un combo y sus elementos asociados."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM combo_items WHERE combo_id = ?", (combo_id,))
            cursor = conn.execute("DELETE FROM combos WHERE id = ?", (combo_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def sell_combo(
    combo_name_or_id: Union[str, int],
    client_name: str,
    whatsapp: str = "",
    telegram: str = "",
    client_type: str = "consumidor_final",
    payment_method: str = "Transferencia",
    duration_days: int = 30,
    notes: str = ""
) -> Dict[str, Any]:
    """Vende un combo completo en un solo paso:
    1. Verifica stock libre disponible para todas las plataformas del combo.
    2. Asigna un perfil libre de cada plataforma al cliente con vencimiento sincronizado.
    3. Registra el cobro consolidado en la tabla de pagos en ARS.
    4. Genera el mensaje de entrega de WhatsApp consolidado con todos los accesos listos.
    """
    conn = get_connection()
    try:
        c_query = str(combo_name_or_id).strip()
        if c_query.isdigit():
            combo_row = conn.execute("SELECT * FROM combos WHERE id = ?", (int(c_query),)).fetchone()
        else:
            combo_row = conn.execute("SELECT * FROM combos WHERE lower(name) = lower(?)", (c_query,)).fetchone()
            
        if not combo_row:
            return {"success": False, "error": f"No se encontró el combo '{combo_name_or_id}'"}
            
        combo = dict(combo_row)
        combo_id = combo["id"]
        
        items = conn.execute("SELECT * FROM combo_items WHERE combo_id = ?", (combo_id,)).fetchall()
        if not items:
            return {"success": False, "error": f"El combo '{combo['name']}' no tiene plataformas asociadas."}
            
        missing_stock = []
        needed_slots = []
        for it in items:
            p_name = it["platform"]
            slot = conn.execute("""
                SELECT * FROM streaming_accounts
                WHERE lower(platform) = lower(?) AND status = 'libre'
                ORDER BY id ASC LIMIT 1
            """, (p_name,)).fetchone()
            
            if not slot:
                missing_stock.append(p_name)
            else:
                needed_slots.append((it, dict(slot)))
                
        if missing_stock:
            return {
                "success": False,
                "error": f"Stock insuficiente para completar el combo '{combo['name']}'. Cuentas faltantes: {', '.join(missing_stock)}"
            }
            
        client = find_or_create_client(
            name=client_name,
            whatsapp=whatsapp,
            telegram=telegram,
            client_type=client_type,
            notes=notes
        )
        
        today = date.today()
        expiry = today + timedelta(days=duration_days)
        expiry_str = expiry.isoformat()
        today_str = today.isoformat()
        
        is_reseller = "revend" in client_type.lower()
        combo_sale_price = combo["price_reseller"] if is_reseller else combo["price_final"]
        
        assigned_accounts = []
        total_costs = 0.0
        
        with conn:
            for item, slot in needed_slots:
                slot_id = slot["id"]
                slot_cost = parse_money(slot.get("cost"))
                if slot_cost == 0.0:
                    _, cat_cost = get_suggested_price(item["platform"], item.get("service_type", "pantalla"), client_type)
                    slot_cost = cat_cost
                total_costs += slot_cost
                
                conn.execute("""
                    UPDATE streaming_accounts
                    SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                        start_date = ?, expiry_date = ?, price = ?, notes = ?,
                        last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    client["id"],
                    today_str,
                    expiry_str,
                    format_ars(combo_sale_price / len(needed_slots)),
                    f"Venta Combo: {combo['name']}. {notes}".strip(),
                    slot_id
                ))
                
                fresh = conn.execute("""
                    SELECT a.*, c.name as client_name, c.whatsapp, c.telegram, c.client_type, c.client_code
                    FROM streaming_accounts a
                    LEFT JOIN clients c ON a.client_id = c.id
                    WHERE a.id = ?
                """, (slot_id,)).fetchone()
                assigned_accounts.append(dict(fresh))
                
            profit = combo_sale_price - total_costs
            cursor = conn.execute("""
                INSERT INTO payments (client_id, amount, cost, profit, payment_method, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                client["id"],
                combo_sale_price,
                total_costs,
                profit,
                payment_method,
                f"Combo {combo['name']} ({len(assigned_accounts)} servicios)"
            ))
            payment_id = cursor.lastrowid

        clean_phone = clean_whatsapp_phone(whatsapp)
        
        msg_lines = [
            f"🍿 *¡Hola {client['name']}!* ¡Gracias por tu compra!",
            f"Aquí tienes los accesos a tu *{combo['name']}*:\n"
        ]
        
        for idx, acc in enumerate(assigned_accounts, 1):
            prof = f" | 👤 *Perfil:* {acc['profile_name']}" if acc.get('profile_name') else ""
            pin = f" | 🔒 *PIN:* {acc['profile_pin']}" if acc.get('profile_pin') else ""
            msg_lines.append(
                f"📺 *{idx}. {acc['platform']}*\n"
                f"📧 *Correo:* `{acc['email']}`\n"
                f"🔑 *Clave:* `{acc['password']}`"
                f"{prof}{pin}\n"
            )
            
        msg_lines.append(f"📅 *Vencimiento del Combo:* {expiry_str}")
        msg_lines.append(f"💰 *Total Abonado:* {format_ars(combo_sale_price)}")
        msg_lines.append(f"💳 *Medio de Pago:* {payment_method}\n")
        msg_lines.append("⚠️ *Reglas:* No modificar contraseñas ni perfiles ajenos para conservar la garantía activa.\n")
        msg_lines.append("¡Que disfrutes de tus series y películas! 🚀✨")
        
        full_msg = "\n".join(msg_lines)
        encoded_text = urllib.parse.quote(full_msg)
        wa_link = f"https://wa.me/{clean_phone}?text={encoded_text}" if clean_phone else f"https://wa.me/?text={encoded_text}"
        
        return {
            "success": True,
            "combo_name": combo["name"],
            "client_name": client["name"],
            "accounts": assigned_accounts,
            "amount": combo_sale_price,
            "cost": total_costs,
            "profit": profit,
            "expiry_date": expiry_str,
            "whatsapp_message": full_msg,
            "wa_link": wa_link,
            "payment_id": payment_id
        }
    finally:
        conn.close()


