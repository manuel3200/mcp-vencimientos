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
                    price_reseller_vip REAL DEFAULT 0.0,
                    notes TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(platform, service_type)
                )
            """)

            try:
                conn.execute("ALTER TABLE price_catalog ADD COLUMN price_reseller_vip REAL DEFAULT 0.0")
            except Exception:
                pass

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
                ("Crunchyroll Mega Fan", "pantalla", 1500.0, 3200.0, 2400.0, "Perfil anime HD"),
                ("HTTP Custom", "hwid", 0.0, 8000.0, 4500.0, "Servidor VPN/SSH por HWID")
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

            # Asegurar precio VIP para HTTP Custom
            conn.execute("""
                UPDATE price_catalog
                SET price_reseller_vip = 3500.0, price_final = 8000.0, price_reseller = 4500.0
                WHERE platform = 'HTTP Custom' AND (price_reseller_vip IS NULL OR price_reseller_vip = 0.0)
            """)


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
                    admin_whatsapp TEXT DEFAULT '',
                    gemini_api_key TEXT DEFAULT '',
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
                    phash TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP DEFAULT NULL
                )
            """)

            # 16. Tabla de Reportes e Incidencias de Cuentas Caídas (Autorización Admin)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fallen_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    account_id INTEGER REFERENCES streaming_accounts(id) ON DELETE SET NULL,
                    sender_phone TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    platform TEXT DEFAULT '',
                    account_email TEXT DEFAULT '',
                    profile_name TEXT DEFAULT '',
                    issue_type TEXT DEFAULT 'caida',
                    raw_message TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending', -- 'pending', 'waiting', 'resolved', 'dismissed'
                    admin_notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP DEFAULT NULL
                )
            """)

            # Migración: Agregar columna admin_whatsapp y gemini_api_key a whatsapp_api_settings si no existen
            try:
                conn.execute("ALTER TABLE whatsapp_api_settings ADD COLUMN admin_whatsapp TEXT DEFAULT ''")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE whatsapp_api_settings ADD COLUMN gemini_api_key TEXT DEFAULT ''")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE whatsapp_api_settings ADD COLUMN expiry_cutoff_hour INTEGER DEFAULT 17")
            except Exception:
                pass

            # Migración: Columnas para Rollback, Pagos Parciales y Deuda en streaming_accounts
            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN previous_expiry_date TEXT DEFAULT ''")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE streaming_accounts ADD COLUMN debt_balance REAL DEFAULT 0.0")
            except Exception:
                pass

            # Migración: Columnas para Pagos Parciales y Reversión en payments
            try:
                conn.execute("ALTER TABLE payments ADD COLUMN is_partial INTEGER DEFAULT 0")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE payments ADD COLUMN status TEXT DEFAULT 'completed'")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE payments ADD COLUMN remaining_balance REAL DEFAULT 0.0")
            except Exception:
                pass

            # Migración: Columnas para Seguimiento y Rollback en fallen_reports
            try:
                conn.execute("ALTER TABLE fallen_reports ADD COLUMN reassigned_account_id INTEGER DEFAULT NULL")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE fallen_reports ADD COLUMN last_followup_at TIMESTAMP DEFAULT NULL")
            except Exception:
                pass

            try:
                conn.execute("ALTER TABLE fallen_reports ADD COLUMN followup_count INTEGER DEFAULT 0")
            except Exception:
                pass

            # Migración: Agregar columna phash a pending_payments para detección perceptual de comprobantes reciclados
            try:
                conn.execute("ALTER TABLE pending_payments ADD COLUMN phash TEXT DEFAULT ''")
            except Exception:
                pass

            # Saneamiento automático de comprobantes duplicados y corrección de montos OCR
            try:
                conn.execute("""
                    UPDATE pending_payments
                    SET amount = 8000.0, amount_formatted = '$8.000', bank = 'Naranja X'
                    WHERE amount = 58000.0 AND status = 'pending'
                """)
            except Exception:
                pass

            try:
                conn.execute("""
                    UPDATE pending_payments
                    SET status = 'rejected', notes = 'Auto-descartado: clon duplicado de webhook'
                    WHERE status = 'pending'
                      AND id NOT IN (
                          SELECT MAX(id)
                          FROM pending_payments
                          WHERE status = 'pending'
                          GROUP BY sender_phone
                      )
                """)
            except Exception:
                pass

            # 19. Configuración del Modo del Bot de WhatsApp (Atlas-MD: public / private / self)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_bot_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    bot_mode TEXT DEFAULT 'public',
                    anti_link_global INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("INSERT OR IGNORE INTO whatsapp_bot_settings (id, bot_mode) VALUES (1, 'public')")

            # 20. Configuración Individual por Grupo de WhatsApp (Whitelist & Automatizaciones)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_groups_config (
                    group_jid TEXT PRIMARY KEY,
                    group_name TEXT DEFAULT '',
                    bot_enabled INTEGER DEFAULT 1,
                    antilink_enabled INTEGER DEFAULT 0,
                    antilink_action TEXT DEFAULT 'delete',
                    welcome_enabled INTEGER DEFAULT 0,
                    welcome_message TEXT DEFAULT '',
                    goodbye_enabled INTEGER DEFAULT 0,
                    goodbye_message TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 21. Baneo Silencioso (Silent Ban para usuarios o grupos tóxicos/spammers)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_silent_bans (
                    target_id TEXT PRIMARY KEY,
                    target_type TEXT DEFAULT 'user',
                    reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 22. Log de Auditoría Inmutable (Append-Only con encadenamiento HMAC estilo blockchain)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    old_value TEXT DEFAULT '',
                    new_value TEXT DEFAULT '',
                    ip_or_source TEXT DEFAULT '',
                    prev_hash TEXT DEFAULT '',
                    signature_hmac TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target_type, target_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")

            # 23. Enlaces Efímeros de Credenciales (Anti-SIM Swap / One-Time Secrets)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ephemeral_secrets (
                    token TEXT PRIMARY KEY,
                    ciphertext TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    max_views INTEGER DEFAULT 1,
                    view_count INTEGER DEFAULT 0,
                    burned_at TIMESTAMP DEFAULT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ephemeral_secrets_token ON ephemeral_secrets(token)")

            # 24. Programa de Referidos (Códigos y Saldo a Favor)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS referral_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER UNIQUE REFERENCES clients(id) ON DELETE CASCADE,
                    code TEXT UNIQUE NOT NULL,
                    reward_balance_ars REAL DEFAULT 0.0,
                    total_referred INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON referral_codes(code)")

            # 25. Historial de Recompensas de Referidos
            conn.execute("""
                CREATE TABLE IF NOT EXISTS referral_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_client_id INTEGER NOT NULL REFERENCES clients(id),
                    referred_client_id INTEGER NOT NULL REFERENCES clients(id),
                    reward_amount REAL NOT NULL,
                    status TEXT DEFAULT 'credited',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_history_referrer ON referral_history(referrer_client_id)")

            # 26. Motor de Cupones de Descuento
            conn.execute("""
                CREATE TABLE IF NOT EXISTS coupons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    discount_type TEXT NOT NULL DEFAULT 'percent', -- 'percent', 'fixed_ars'
                    discount_value REAL NOT NULL,
                    min_purchase REAL DEFAULT 0.0,
                    max_uses INTEGER DEFAULT 100,
                    times_used INTEGER DEFAULT 0,
                    expires_at TIMESTAMP NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_coupons_code ON coupons(code)")

            # 27. Redenciones de Cupones
            conn.execute("""
                CREATE TABLE IF NOT EXISTS coupon_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coupon_id INTEGER NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
                    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                    discount_applied REAL NOT NULL,
                    order_amount REAL NOT NULL,
                    redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon ON coupon_redemptions(coupon_id)")

            # 28. Gamificación y Actividad de Miembros en Grupos de WhatsApp
            conn.execute("""
                CREATE TABLE IF NOT EXISTS group_member_activity (
                    group_jid TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    push_name TEXT DEFAULT '',
                    message_count INTEGER DEFAULT 0,
                    points INTEGER DEFAULT 0,
                    level_tier TEXT DEFAULT 'Bronce',
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_jid, phone)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_member_activity_points ON group_member_activity(group_jid, points DESC)")

            # 29. Auto-Respuesta a Preguntas Frecuentes (Community FAQs)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS community_faqs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword_triggers TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Sembrar FAQs esenciales predeterminadas si la tabla está vacía
            faqs_row = conn.execute("SELECT COUNT(*) as count FROM community_faqs").fetchone()
            if faqs_row and faqs_row["count"] == 0:
                initial_faqs = [
                    (
                        "pagar,pago,transferencia,alias,cbu,cuenta,banco,metodos de pago,como pago",
                        "¿Cómo pagar y qué medios de pago aceptan?",
                        "💳 *MEDIOS DE PAGO DISPONIBLES:*\n• Transferencia bancaria (CBU/CVU y Alias al instante)\n• Mercado Pago\n• Cripto / Binance USDT\n\n📌 *Importante:* Una vez realizado el pago, envía tu comprobante (foto o PDF) por este chat para acreditarlo automáticamente.",
                        "pagos"
                    ),
                    (
                        "caida,falla,no anda,se cayo,soporte,pantalla ocupada,error contraseña",
                        "¿Qué hacer si una cuenta tiene problemas o cae?",
                        "🛡️ *GARANTÍA Y SOPORTE STREAMVAULT:*\nSi tienes algún inconveniente con una cuenta, escribe en privado `/caida` seguido de tu correo para generar un ticket prioritario. Nuestro sistema verificará y reemplazará tus credenciales en minutos.",
                        "soporte"
                    ),
                    (
                        "catalogo,precios,planes,tarifas,cuanto sale,costo,combos",
                        "¿Dónde puedo ver el catálogo de precios y servicios?",
                        "🍿 *CATÁLOGO DE SERVICIOS Y COMBOS:*\nPuedes consultar nuestra lista oficial de servicios y promociones escribiendo `/catalogo` o `/precios` en el chat.",
                        "ventas"
                    ),
                    (
                        "http custom,vpn,hwid,internet ilimitado,como conectar",
                        "¿Cómo funciona el servicio HTTP Custom?",
                        "🌐 *HTTP CUSTOM / VPN:*\nPara activar o renovar tu servidor, ingresa a la app HTTP Custom, copia tu HWID y envíanoslo por privado para vincular tu acceso de alta velocidad.",
                        "vpn"
                    )
                ]
                for triggers, q, a, cat in initial_faqs:
                    conn.execute("""
                        INSERT INTO community_faqs (keyword_triggers, question, answer, category)
                        VALUES (?, ?, ?, ?)
                    """, (triggers, q, a, cat))
    finally:
        conn.close()
