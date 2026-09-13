import secrets
import logging
from db.connection import get_connection

logger = logging.getLogger("database.schema")

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

def init_db():
    """Inicializa la base de datos y crea las tablas necesarias de forma idempotente."""
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

            # Sembrado y actualización de catálogo base
            netflix_defaults = [
                ("Netflix (Casa Extra)", "pantalla", 5800.0, 8500.0, 6500.0, "1 Pantalla / Casa Extra individual"),
                ("Netflix (Cuenta Completa)", "cuenta_completa", 20000.0, 25000.0, 23000.0, "Cuenta Completa Full HD / 4K (4 Pantallas)"),
                ("Disney+ Premium", "pantalla", 2000.0, 4000.0, 3000.0, "Perfil con deportes ESPN"),
                ("Max (HBO)", "pantalla", 1800.0, 3800.0, 2800.0, "Perfil Platino 4K"),
                ("Amazon Prime Video", "pantalla", 1500.0, 3500.0, 2500.0, "Perfil individual"),
                ("Paramount+", "pantalla", 1400.0, 3000.0, 2200.0, "Perfil individual"),
                ("Spotify Premium", "cuenta_completa", 2500.0, 5000.0, 3800.0, "Cuenta completa individual"),
                ("YouTube Premium", "cuenta_completa", 2500.0, 5000.0, 3800.0, "Cuenta sin anuncios"),
                ("Crunchyroll Mega Fan", "pantalla", 1500.0, 3200.0, 2400.0, "Perfil anime HD")
            ]
            for p, stype, c_price, p_fin, p_res, notes in netflix_defaults:
                conn.execute("""
                    INSERT INTO price_catalog (platform, service_type, cost_price, price_final, price_reseller, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, service_type) DO UPDATE SET
                        cost_price = CASE WHEN excluded.platform LIKE 'Netflix%' THEN excluded.cost_price ELSE cost_price END,
                        price_final = CASE WHEN excluded.platform LIKE 'Netflix%' THEN excluded.price_final ELSE price_final END,
                        price_reseller = CASE WHEN excluded.platform LIKE 'Netflix%' THEN excluded.price_reseller ELSE price_reseller END,
                        notes = CASE WHEN excluded.platform LIKE 'Netflix%' THEN excluded.notes ELSE notes END,
                        updated_at = CURRENT_TIMESTAMP
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

            # 11. Proveedores Mayoristas
            conn.execute("""
                CREATE TABLE IF NOT EXISTS suppliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    contact TEXT DEFAULT '',
                    payment_info TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 12. Egresos y Pagos a Proveedores
            conn.execute("""
                CREATE TABLE IF NOT EXISTS supplier_expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
                    account_email TEXT DEFAULT '',
                    platform TEXT DEFAULT '',
                    amount REAL NOT NULL,
                    payment_method TEXT DEFAULT 'Transferencia',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migraciones en streaming_accounts para vinculación con proveedores mayoristas
            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN supplier_id INTEGER DEFAULT NULL")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN supplier_expiry_date TEXT DEFAULT ''")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN supplier_cost REAL DEFAULT 0.0")
            except Exception:
                pass

            # 13. Configuración de Evolution API WhatsApp
            conn.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_api_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    api_url TEXT DEFAULT 'http://evolution-api:8080',
                    api_key TEXT DEFAULT 'mcp-evolution-key-2026',
                    instance_name TEXT DEFAULT 'streaming-bot',
                    auto_send_expiry INTEGER DEFAULT 0,
                    auto_send_sales INTEGER DEFAULT 0,
                    auto_reply_enabled INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT OR IGNORE INTO whatsapp_api_settings (id) VALUES (1)")

            # 13b. Configuración de Integración Chatwoot
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chatwoot_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    url TEXT DEFAULT 'https://chat.joif.net',
                    token TEXT DEFAULT '',
                    account_id TEXT DEFAULT '1',
                    enabled INTEGER DEFAULT 1,
                    auto_sync INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT OR IGNORE INTO chatwoot_settings (id, url, token, account_id) VALUES (1, 'https://chat.joif.net', 'ZRzCpt75vxkyiUC7H1otEoog', '1')")
            conn.execute("UPDATE chatwoot_settings SET url = 'https://chat.joif.net' WHERE url = 'http://chatwoot-rails:3000'")
            conn.execute("UPDATE chatwoot_settings SET token = 'ZRzCpt75vxkyiUC7H1otEoog' WHERE token IS NULL OR token = ''")

            # 14. Configuración y Tokens OAuth 2.0 (Gemini Spark)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    client_id TEXT UNIQUE NOT NULL,
                    client_secret TEXT NOT NULL,
                    client_name TEXT DEFAULT 'Gemini Spark',
                    redirect_uris TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_auth_codes (
                    code TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    code_challenge TEXT DEFAULT '',
                    code_challenge_method TEXT DEFAULT '',
                    user_id TEXT DEFAULT 'admin',
                    expires_at REAL NOT NULL,
                    used INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    access_token TEXT PRIMARY KEY,
                    refresh_token TEXT UNIQUE,
                    client_id TEXT NOT NULL,
                    user_id TEXT DEFAULT 'admin',
                    expires_at REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            oauth_row = conn.execute("SELECT COUNT(*) as count FROM oauth_clients").fetchone()
            if oauth_row and oauth_row["count"] == 0:
                def_client_id = "gemini-spark-joif"
                def_client_secret = f"sec_{secrets.token_hex(20)}"
                conn.execute("""
                    INSERT INTO oauth_clients (id, client_id, client_secret, client_name, redirect_uris)
                    VALUES (1, ?, ?, 'Gemini Spark Connected App', 'https://gemini.google.com')
                """, (def_client_id, def_client_secret))

            # 15. Tabla de Pagos y Comprobantes Pendientes de Aprobación
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    account_id INTEGER REFERENCES streaming_accounts(id) ON DELETE SET NULL,
                    sender_phone TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    platform TEXT DEFAULT '',
                    amount REAL DEFAULT 0.0,
                    amount_formatted TEXT DEFAULT '',
                    bank TEXT DEFAULT '',
                    operation_id TEXT DEFAULT '',
                    date_detected TEXT DEFAULT '',
                    receipt_filename TEXT DEFAULT '',
                    receipt_mimetype TEXT DEFAULT '',
                    receipt_base64 TEXT DEFAULT '',
                    raw_text TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP DEFAULT NULL
                )
            """)

            # Migración: Agregar columna admin_whatsapp a whatsapp_api_settings si no existe
            try:
                conn.execute("ALTER TABLE whatsapp_api_settings ADD COLUMN admin_whatsapp TEXT DEFAULT ''")
            except Exception:
                pass
    finally:
        conn.close()
