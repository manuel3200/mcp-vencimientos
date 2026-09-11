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
    """Extrae el valor numérico flotante de cadenas como '$10 USD', '15000', '$7.50', etc."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    match = re.search(r'([0-9]+(?:[\.,][0-9]+)?)', str(val))
    if match:
        clean = match.group(1).replace(',', '.')
        try:
            return float(clean)
        except ValueError:
            return 0.0
    return 0.0

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

def search_client(query: str) -> Optional[Dict[str, Any]]:
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
            price_num = parse_money(price)
            cost_num = parse_money(cost)
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
        days_str = ""
        if days_rem is not None:
            if days_rem == 0:
                days_str = " (¡Vence HOY!)"
            elif days_rem > 0:
                days_str = f" (vence en {days_rem} días)"
            else:
                days_str = f" (vencida hace {abs(days_rem)} días)"

        default_pm = (
            "• Transferencia Bancaria / CVU / CBU\n"
            "• Mercado Pago\n"
            "• Binance USDT / Cripto"
        )
        pm_text = payment_methods.strip() if payment_methods else default_pm

        msg = (
            f"👋 *¡Hola {client_name}!* Esperamos que estés disfrutando tu suscripción.\n\n"
            f"Te recordamos que tu servicio está próximo a vencer:\n"
            f"📺 *Servicio:* {platform}\n"
            f"📧 *Cuenta:* `{email}`\n"
            f"📅 *Vencimiento:* {expiry}{days_str}\n"
            f"💰 *Monto a Renovar:* {price or 'Consultar valor'}\n\n"
            f"💳 *Métodos de Pago:*\n{pm_text}\n\n"
            f"Una vez abonado, por favor envíanos tu comprobante por aquí para renovarte inmediatamente sin cortes. ¡Muchas gracias! 🙌"
        )
    elif m_type in ("reemplazo", "soporte", "caida"):
        profile_line = f"\n👤 *Perfil:* {profile}" if profile else ""
        pin_line = f"\n🔒 *PIN de Perfil:* {pin}" if pin else ""
        msg = (
            f"🛠️ *¡Hola {client_name}!* Te informamos que hemos reactivado tu servicio.\n\n"
            f"✨ *Nuevos Datos de Acceso:*\n"
            f"📺 *Servicio:* {platform}\n"
            f"📧 *Nuevo Correo:* `{email}`\n"
            f"🔑 *Nueva Contraseña:* `{password}`"
            f"{profile_line}"
            f"{pin_line}\n"
            f"📅 *Mantiene Vencimiento:* {expiry}\n\n"
            f"📌 *Recomendación:* Recuerda utilizar únicamente el perfil asignado y no modificar la clave.\n\n"
            f"¡Ya puedes continuar disfrutando de tu contenido! 🍿🚀"
        )
    else:  # "entrega", "bienvenida", "activacion"
        profile_line = f"\n👤 *Perfil Asignado:* {profile}" if profile else ""
        pin_line = f"\n🔒 *PIN:* {pin}" if pin else ""
        price_line = f"\n💰 *Valor:* {price}" if price else ""
        msg = (
            f"🍿 *¡Hola {client_name}!* Aquí tienes los datos de acceso a tu suscripción:\n\n"
            f"📺 *Servicio:* {platform}\n"
            f"📧 *Usuario/Correo:* `{email}`\n"
            f"🔑 *Contraseña:* `{password}`"
            f"{profile_line}"
            f"{pin_line}\n"
            f"📅 *Vencimiento:* {expiry}"
            f"{price_line}\n\n"
            f"⚠️ *Reglas de Uso Importantes:*\n"
            f"• No cambiar correo ni contraseña.\n"
            f"• Utilizar únicamente el perfil asignado.\n"
            f"• No ingresar en más dispositivos de los permitidos.\n\n"
            f"¡Que lo disfrutes al máximo! Si tienes alguna duda, estamos a tu disposición ✨"
        )

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

            today_str = date.today().isoformat()
            conn.execute("""
                UPDATE streaming_accounts
                SET client_id = ?, status = 'ocupada', payment_status = 'pagado',
                    start_date = ?, expiry_date = ?, price = ?, notes = ?,
                    last_alert_sent = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (client["id"], today_str, expiry_date.strip(), price.strip(), notes.strip(), slot_id))

            price_num = parse_money(price)
            cost_num = parse_money(slot.get("cost"))
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
        "Monto_Cobrado_USD", "Costo_USD", "Ganancia_Neta_USD", "Metodo_Pago", "Notas"
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
        
        backup_data = {
            "backup_version": "2.7.0",
            "created_at": datetime.now().isoformat(),
            "stats": {
                "clients_count": len(clients),
                "accounts_count": len(accounts),
                "payments_count": len(payments)
            },
            "clients": clients,
            "streaming_accounts": accounts,
            "payments": payments,
            "stock_thresholds": thresholds
        }
        return json.dumps(backup_data, indent=2, ensure_ascii=False)
    finally:
        conn.close()

def get_csv_template_stock() -> str:
    """Plantilla CSV modelo para cargar inventario libre en Excel."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",")
    writer.writerow(["Plataforma", "Correo", "Contrasena", "Perfil", "PIN", "Costo", "Notas"])
    writer.writerow(["Netflix 4K", "cuenta1@ejemplo.com", "ClaveSegura123", "Perfil 1", "1234", "3.00", "Proveedor A"])
    writer.writerow(["Disney+", "cuenta2@ejemplo.com", "ClaveSegura456", "", "", "2.50", "Cuenta Completa"])
    writer.writerow(["Spotify Familiar", "cuenta3@ejemplo.com", "ClaveSegura789", "", "", "1.80", "Plan Familiar"])
    return output.getvalue()

def get_csv_template_sales() -> str:
    """Plantilla CSV modelo para migrar o cargar ventas con clientes en Excel."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "Cliente", "WhatsApp", "Telegram", "Tipo_Cliente", "Plataforma",
        "Correo", "Contrasena", "Perfil", "PIN", "Vencimiento", "Precio", "Costo", "Notas"
    ])
    writer.writerow([
        "Juan Perez", "+5491112345678", "@juanp", "consumidor_final", "Netflix 4K",
        "net@ejemplo.com", "Clave123", "Perfil 1", "1234", "2026-10-15", "6.00", "3.00", "Cliente puntual"
    ])
    writer.writerow([
        "Matias Revendedor", "+5491187654321", "@matias_reseller", "revendedor", "Disney+",
        "dis@ejemplo.com", "Pass456", "", "", "2026-10-20", "4.50", "2.50", "Lote mensual"
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


