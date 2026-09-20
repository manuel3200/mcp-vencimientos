import logging
from typing import Dict, Any, Optional, List, Tuple
from db.connection import get_connection

logger = logging.getLogger("database.groups_moderation")

# ==========================================
# Modo de Operación del Bot (Public / Private / Self)
# ==========================================

def get_bot_mode() -> str:
    """Devuelve el modo actual de operación del bot ('public', 'private', 'self')."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT bot_mode FROM whatsapp_bot_settings WHERE id = 1").fetchone()
        if row and row["bot_mode"]:
            return row["bot_mode"].strip().lower()
        return "public"
    except Exception as e:
        logger.warning(f"Error consultando bot_mode: {e}")
        return "public"
    finally:
        conn.close()


def set_bot_mode(mode: str) -> str:
    """Establece el modo de operación del bot ('public', 'private', 'self')."""
    clean_mode = mode.strip().lower()
    if clean_mode not in ("public", "private", "self"):
        clean_mode = "public"

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_bot_settings (id, bot_mode, updated_at)
                VALUES (1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    bot_mode = excluded.bot_mode,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_mode,))
        return clean_mode
    finally:
        conn.close()


# ==========================================
# Configuración Individual por Grupo (Whitelist)
# ==========================================

def list_groups_config() -> List[Dict[str, Any]]:
    """Lista todos los grupos configurados y su estado de autorización."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM whatsapp_groups_config ORDER BY group_name ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_group_config(group_jid: str) -> Optional[Dict[str, Any]]:
    """Obtiene la configuración registrada para un JID de grupo específico."""
    clean_jid = group_jid.strip()
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM whatsapp_groups_config WHERE group_jid = ?
        """, (clean_jid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_group_config(
    group_jid: str,
    group_name: str = "",
    bot_enabled: Optional[int] = None,
    antilink_enabled: Optional[int] = None,
    antilink_action: Optional[str] = None,
    welcome_enabled: Optional[int] = None,
    welcome_message: Optional[str] = None,
    goodbye_enabled: Optional[int] = None,
    goodbye_message: Optional[str] = None
) -> Dict[str, Any]:
    """Crea o actualiza la configuración de un grupo de WhatsApp."""
    clean_jid = group_jid.strip()
    if not clean_jid:
        return {}

    conn = get_connection()
    try:
        with conn:
            existing = conn.execute("SELECT * FROM whatsapp_groups_config WHERE group_jid = ?", (clean_jid,)).fetchone()
            if existing:
                ex = dict(existing)
                new_name = group_name.strip() if group_name else ex.get("group_name", "")
                new_bot = bot_enabled if bot_enabled is not None else ex.get("bot_enabled", 1)
                new_anti = antilink_enabled if antilink_enabled is not None else ex.get("antilink_enabled", 0)
                new_anti_act = antilink_action.strip() if antilink_action else ex.get("antilink_action", "delete")
                new_wel = welcome_enabled if welcome_enabled is not None else ex.get("welcome_enabled", 0)
                new_wel_msg = welcome_message if welcome_message is not None else ex.get("welcome_message", "")
                new_good = goodbye_enabled if goodbye_enabled is not None else ex.get("goodbye_enabled", 0)
                new_good_msg = goodbye_message if goodbye_message is not None else ex.get("goodbye_message", "")

                conn.execute("""
                    UPDATE whatsapp_groups_config
                    SET group_name = ?, bot_enabled = ?, antilink_enabled = ?, antilink_action = ?,
                        welcome_enabled = ?, welcome_message = ?, goodbye_enabled = ?, goodbye_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE group_jid = ?
                """, (new_name, new_bot, new_anti, new_anti_act, new_wel, new_wel_msg, new_good, new_good_msg, clean_jid))
            else:
                new_name = group_name.strip()
                new_bot = 1 if bot_enabled is None else bot_enabled
                new_anti = 0 if antilink_enabled is None else antilink_enabled
                new_anti_act = antilink_action.strip() if antilink_action else "delete"
                new_wel = 0 if welcome_enabled is None else welcome_enabled
                new_wel_msg = welcome_message or ""
                new_good = 0 if goodbye_enabled is None else goodbye_enabled
                new_good_msg = goodbye_message or ""

                conn.execute("""
                    INSERT INTO whatsapp_groups_config (
                        group_jid, group_name, bot_enabled, antilink_enabled, antilink_action,
                        welcome_enabled, welcome_message, goodbye_enabled, goodbye_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (clean_jid, new_name, new_bot, new_anti, new_anti_act, new_wel, new_wel_msg, new_good, new_good_msg))

            row = conn.execute("SELECT * FROM whatsapp_groups_config WHERE group_jid = ?", (clean_jid,)).fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def set_group_bot_enabled(group_jid: str, enabled: bool) -> bool:
    """Habilita o deshabilita la atención del bot en un grupo específico."""
    clean_jid = group_jid.strip()
    val = 1 if enabled else 0
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_groups_config (group_jid, bot_enabled)
                VALUES (?, ?)
                ON CONFLICT(group_jid) DO UPDATE SET
                    bot_enabled = excluded.bot_enabled,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_jid, val))
        return True
    finally:
        conn.close()


def set_group_antilink(group_jid: str, enabled: bool, action: str = "delete") -> bool:
    """Configura el estado de antilink para un grupo."""
    clean_jid = group_jid.strip()
    val = 1 if enabled else 0
    act = action.strip().lower() if action in ("delete", "kick", "warn") else "delete"
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_groups_config (group_jid, antilink_enabled, antilink_action)
                VALUES (?, ?, ?)
                ON CONFLICT(group_jid) DO UPDATE SET
                    antilink_enabled = excluded.antilink_enabled,
                    antilink_action = excluded.antilink_action,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_jid, val, act))
        return True
    finally:
        conn.close()


def is_group_bot_enabled(group_jid: str) -> bool:
    """Verifica si el bot tiene permitido responder en un grupo específico.
    Por defecto, si no existe registro previo, se asume False (requiere habilitación explícita si el modo no es global).
    """
    clean_jid = group_jid.strip()
    if not clean_jid:
        return False
    conn = get_connection()
    try:
        row = conn.execute("SELECT bot_enabled FROM whatsapp_groups_config WHERE group_jid = ?", (clean_jid,)).fetchone()
        if row is not None:
            return bool(row["bot_enabled"])
        return False
    finally:
        conn.close()


def is_group_antilink_enabled(group_jid: str) -> Tuple[bool, str]:
    """Retorna (activo: bool, accion: str) para antilink en un grupo."""
    clean_jid = group_jid.strip()
    if not clean_jid:
        return False, "delete"
    conn = get_connection()
    try:
        row = conn.execute("SELECT antilink_enabled, antilink_action FROM whatsapp_groups_config WHERE group_jid = ?", (clean_jid,)).fetchone()
        if row and row["antilink_enabled"]:
            return True, row["antilink_action"] or "delete"
        return False, "delete"
    finally:
        conn.close()


# ==========================================
# Baneo Silencioso (Silent Ban - Atlas-MD)
# ==========================================

def list_silent_bans() -> List[Dict[str, Any]]:
    """Lista todos los usuarios y grupos con baneo silencioso activo."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM whatsapp_silent_bans ORDER BY created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_silent_ban(target_id: str, target_type: str = "user", reason: str = "") -> bool:
    """Agrega un número o grupo a la lista de baneo silencioso."""
    import re
    clean_target = target_id.strip()
    if "@g.us" in clean_target:
        t_type = "group"
    else:
        clean_target = re.sub(r'[^0-9]', '', clean_target)
        t_type = "user"

    if not clean_target:
        return False

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO whatsapp_silent_bans (target_id, target_type, reason, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(target_id) DO UPDATE SET
                    target_type = excluded.target_type,
                    reason = excluded.reason
            """, (clean_target, t_type, reason.strip()))
        return True
    finally:
        conn.close()


def remove_silent_ban(target_id: str) -> bool:
    """Remueve un objetivo del baneo silencioso."""
    import re
    clean_target = target_id.strip()
    if "@g.us" not in clean_target:
        clean_target = re.sub(r'[^0-9]', '', clean_target)

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM whatsapp_silent_bans WHERE target_id = ?", (clean_target,))
            return cursor.rowcount > 0
    finally:
        conn.close()


def is_silent_banned(target_id: str) -> bool:
    """Comprueba si un número o grupo está registrado bajo baneo silencioso."""
    import re
    clean_target = target_id.strip()
    if "@g.us" in clean_target:
        lookup_ids = [clean_target]
    else:
        digits = re.sub(r'[^0-9]', '', clean_target)
        if not digits:
            return False
        lookup_ids = [digits]
        if len(digits) >= 8:
            lookup_ids.append(digits[-8:])

    conn = get_connection()
    try:
        for tid in lookup_ids:
            row = conn.execute("SELECT 1 FROM whatsapp_silent_bans WHERE target_id = ? OR target_id LIKE ?", (tid, f"%{tid}")).fetchone()
            if row:
                return True
        return False
    finally:
        conn.close()


def delete_group_config(group_jid: str) -> bool:
    """Elimina la configuración y registro de un grupo de la base de datos."""
    clean_jid = group_jid.strip()
    if not clean_jid:
        return False
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM whatsapp_groups_config WHERE group_jid = ?", (clean_jid,))
            return cursor.rowcount > 0
    finally:
        conn.close()

