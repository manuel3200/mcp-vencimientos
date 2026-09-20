"""
growth_repo.py - Repositorio de Crecimiento Comercial y Comunidad (StreamVault v2)
Gestiona persistencia para:
1. Programa de referidos y saldo a favor.
2. Motor de cupones promocionales y redenciones.
3. Gamificación y actividad de miembros en grupos.
4. FAQs automatizadas comunitarias.
5. Métricas de retención y churn.
"""

import re
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from db.connection import get_connection

logger = logging.getLogger("db.growth_repo")


# ==========================================
# 1. PROGRAMA DE REFERIDOS
# ==========================================

def get_or_create_client_referral_code(client_id: int, custom_code: Optional[str] = None) -> Dict[str, Any]:
    """Obtiene o genera el código único de referido para un cliente."""
    conn = get_connection()
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM referral_codes WHERE client_id = ?",
                (client_id,)
            ).fetchone()
            if row:
                return dict(row)

            # Si se solicita un código personalizado, verificar que no exista
            code = ""
            if custom_code:
                clean = re.sub(r'[^A-Za-z0-9_-]', '', custom_code).upper()
                exists = conn.execute("SELECT 1 FROM referral_codes WHERE code = ?", (clean,)).fetchone()
                if not exists and len(clean) >= 3:
                    code = clean

            if not code:
                # Obtener nombre o código del cliente para un slug legible
                cli = conn.execute("SELECT client_code, name FROM clients WHERE id = ?", (client_id,)).fetchone()
                slug = ""
                if cli:
                    first_name = re.sub(r'[^A-Za-z0-9]', '', (cli["name"] or "").split()[0]).upper()
                    if len(first_name) >= 3:
                        slug = first_name

                rnd = secrets.token_hex(2).upper()
                code = f"REF-{slug}-{rnd}" if slug else f"REF-{client_id}-{rnd}"

            conn.execute("""
                INSERT INTO referral_codes (client_id, code, reward_balance_ars, total_referred)
                VALUES (?, ?, 0.0, 0)
            """, (client_id, code))
            
            created = conn.execute("SELECT * FROM referral_codes WHERE client_id = ?", (client_id,)).fetchone()
            return dict(created)
    finally:
        conn.close()


def get_referral_by_code(code: str) -> Optional[Dict[str, Any]]:
    """Busca un código de referido exacto."""
    clean = code.strip().upper()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM referral_codes WHERE UPPER(code) = ?", (clean,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_referral_by_client_id(client_id: int) -> Optional[Dict[str, Any]]:
    """Obtiene los datos del programa de referidos de un cliente."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM referral_codes WHERE client_id = ?", (client_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def credit_referral_reward(referrer_client_id: int, referred_client_id: int, reward_amount: float) -> bool:
    """Acredita una comisión por referido y la registra en el historial."""
    if referrer_client_id == referred_client_id or reward_amount <= 0:
        return False

    conn = get_connection()
    try:
        with conn:
            # Incrementar saldo y total de referidos
            conn.execute("""
                UPDATE referral_codes
                SET reward_balance_ars = reward_balance_ars + ?,
                    total_referred = total_referred + 1
                WHERE client_id = ?
            """, (reward_amount, referrer_client_id))

            # Registrar en historial
            conn.execute("""
                INSERT INTO referral_history (referrer_client_id, referred_client_id, reward_amount, status)
                VALUES (?, ?, ?, 'credited')
            """, (referrer_client_id, referred_client_id, reward_amount))
            return True
    except Exception as e:
        logger.error(f"Error acreditando recompensa de referido: {e}")
        return False
    finally:
        conn.close()


def redeem_referral_balance(client_id: int, amount: float) -> Tuple[bool, str, float]:
    """Canjea parte o la totalidad del saldo a favor de referidos."""
    if amount <= 0:
        return False, "Monto inválido para canje.", 0.0

    conn = get_connection()
    try:
        with conn:
            row = conn.execute("SELECT reward_balance_ars FROM referral_codes WHERE client_id = ?", (client_id,)).fetchone()
            if not row:
                return False, "El cliente no cuenta con código de referidos activo.", 0.0

            current_balance = float(row["reward_balance_ars"] or 0.0)
            if current_balance < amount:
                return False, f"Saldo insuficiente (disponible: ${current_balance:.2f} ARS).", current_balance

            new_balance = current_balance - amount
            conn.execute("""
                UPDATE referral_codes
                SET reward_balance_ars = ?
                WHERE client_id = ?
            """, (new_balance, client_id))

            conn.execute("""
                INSERT INTO referral_history (referrer_client_id, referred_client_id, reward_amount, status)
                VALUES (?, ?, ?, 'redeemed')
            """, (client_id, client_id, -amount))

            return True, f"Canje exitoso por ${amount:.2f} ARS.", new_balance
    finally:
        conn.close()


def list_all_referral_codes() -> List[Dict[str, Any]]:
    """Lista todos los códigos de referidos con los datos del cliente asociado."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT rc.id, rc.client_id, rc.code, rc.reward_balance_ars, rc.total_referred, rc.created_at,
                   c.name as client_name, c.whatsapp as client_whatsapp, c.client_code, c.client_type
            FROM referral_codes rc
            JOIN clients c ON c.id = rc.client_id
            ORDER BY rc.reward_balance_ars DESC, rc.total_referred DESC, rc.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error listando códigos de referidos: {e}")
        return []
    finally:
        conn.close()


def get_referrals_overview_stats() -> Dict[str, Any]:
    """Retorna métricas globales del programa de referidos para el dashboard."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT 
                COUNT(*) as total_codes,
                COALESCE(SUM(reward_balance_ars), 0.0) as total_balance_ars,
                COALESCE(SUM(total_referred), 0) as total_referred_clients
            FROM referral_codes
        """).fetchone()
        if not row:
            return {"total_codes": 0, "total_balance_ars": 0.0, "total_referred_clients": 0}
        return {
            "total_codes": row["total_codes"] or 0,
            "total_balance_ars": float(row["total_balance_ars"] or 0.0),
            "total_referred_clients": row["total_referred_clients"] or 0
        }
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas de referidos: {e}")
        return {"total_codes": 0, "total_balance_ars": 0.0, "total_referred_clients": 0}
    finally:
        conn.close()




# ==========================================
# 2. MOTOR DE CUPONES DE DESCUENTO
# ==========================================

def create_coupon(
    code: str,
    discount_type: str,
    discount_value: float,
    min_purchase: float = 0.0,
    max_uses: int = 100,
    expires_at: Optional[str] = None
) -> Dict[str, Any]:
    """Crea un cupón de descuento con límite y caducidad."""
    clean_code = re.sub(r'[^A-Za-z0-9_-]', '', code).upper()
    if not clean_code:
        raise ValueError("Código de cupón no válido.")

    if discount_type not in ("percent", "fixed_ars"):
        raise ValueError("El tipo de descuento debe ser 'percent' o 'fixed_ars'.")

    if discount_type == "percent" and not (0 < discount_value <= 100):
        raise ValueError("El descuento porcentual debe estar entre 1 y 100%.")

    if not expires_at:
        # Por defecto 30 días de vigencia
        expires_at = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO coupons (code, discount_type, discount_value, min_purchase, max_uses, times_used, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?, 0, ?, 1)
                ON CONFLICT(code) DO UPDATE SET
                    discount_type = excluded.discount_type,
                    discount_value = excluded.discount_value,
                    min_purchase = excluded.min_purchase,
                    max_uses = excluded.max_uses,
                    expires_at = excluded.expires_at,
                    is_active = 1
            """, (clean_code, discount_type, discount_value, min_purchase, max_uses, expires_at))

            row = conn.execute("SELECT * FROM coupons WHERE code = ?", (clean_code,)).fetchone()
            return dict(row)
    finally:
        conn.close()


def get_coupon(code: str) -> Optional[Dict[str, Any]]:
    """Obtiene un cupón por su código."""
    clean = code.strip().upper()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM coupons WHERE UPPER(code) = ?", (clean,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def record_coupon_redemption(coupon_id: int, client_id: Optional[int], discount_applied: float, order_amount: float) -> bool:
    """Registra la aplicación de un cupón y suma 1 a su contador de usos."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE coupons
                SET times_used = times_used + 1
                WHERE id = ?
            """, (coupon_id,))

            conn.execute("""
                INSERT INTO coupon_redemptions (coupon_id, client_id, discount_applied, order_amount)
                VALUES (?, ?, ?, ?)
            """, (coupon_id, client_id, discount_applied, order_amount))
            return True
    except Exception as e:
        logger.error(f"Error registrando redención de cupón: {e}")
        return False
    finally:
        conn.close()


def list_active_coupons() -> List[Dict[str, Any]]:
    """Lista todos los cupones activos que no hayan vencido."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM coupons
            WHERE is_active = 1 AND expires_at > ? AND times_used < max_uses
            ORDER BY created_at DESC
        """, (now_str,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==========================================
# 3. GAMIFICACIÓN EN GRUPOS
# ==========================================

def _compute_level_tier(points: int) -> str:
    """Calcula el rango del miembro según sus puntos acumulados."""
    if points >= 500:
        return "Diamante"
    elif points >= 200:
        return "Oro"
    elif points >= 50:
        return "Plata"
    return "Bronce"


def increment_member_activity(group_jid: str, phone: str, push_name: str = "") -> Dict[str, Any]:
    """Registra un mensaje enviado por un miembro en un grupo y suma puntos."""
    clean_phone = re.sub(r'[^0-9]', '', phone)
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("""
                SELECT message_count, points, push_name
                FROM group_member_activity
                WHERE group_jid = ? AND phone = ?
            """, (group_jid, clean_phone)).fetchone()

            if row:
                new_count = row["message_count"] + 1
                new_points = row["points"] + 5  # 5 puntos por mensaje
                new_tier = _compute_level_tier(new_points)
                saved_name = push_name if push_name else row["push_name"]

                conn.execute("""
                    UPDATE group_member_activity
                    SET message_count = ?,
                        points = ?,
                        level_tier = ?,
                        push_name = ?,
                        last_active_at = CURRENT_TIMESTAMP
                    WHERE group_jid = ? AND phone = ?
                """, (new_count, new_points, new_tier, saved_name, group_jid, clean_phone))
            else:
                new_count = 1
                new_points = 5
                new_tier = "Bronce"
                conn.execute("""
                    INSERT INTO group_member_activity (group_jid, phone, push_name, message_count, points, level_tier)
                    VALUES (?, ?, ?, 1, 5, 'Bronce')
                """, (group_jid, clean_phone, push_name))

            return {
                "group_jid": group_jid,
                "phone": clean_phone,
                "message_count": new_count,
                "points": new_points,
                "level_tier": new_tier
            }
    finally:
        conn.close()


def get_group_leaderboard(group_jid: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Obtiene el ranking de miembros más activos de un grupo."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT phone, push_name, message_count, points, level_tier, last_active_at
            FROM group_member_activity
            WHERE group_jid = ?
            ORDER BY points DESC, message_count DESC
            LIMIT ?
        """, (group_jid, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==========================================
# 4. AUTO-RESPUESTA A PREGUNTAS FRECUENTES (FAQS)
# ==========================================

def list_active_faqs() -> List[Dict[str, Any]]:
    """Obtiene la lista de todas las FAQs activas."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM community_faqs WHERE is_active = 1").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_faq(keyword_triggers: str, question: str, answer: str, category: str = "general") -> int:
    """Crea o actualiza una FAQ comunitaria."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO community_faqs (keyword_triggers, question, answer, category, is_active)
                VALUES (?, ?, ?, ?, 1)
            """, (keyword_triggers.strip(), question.strip(), answer.strip(), category.strip()))
            return cursor.lastrowid
    finally:
        conn.close()


def delete_faq(faq_id: int) -> bool:
    """Elimina o desactiva una FAQ."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM community_faqs WHERE id = ?", (faq_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()


# ==========================================
# 5. RETENCIÓN Y CHURN
# ==========================================

def get_client_churn_metrics() -> List[Dict[str, Any]]:
    """Extrae datos de clientes para evaluación predictiva de churn."""
    conn = get_connection()
    try:
        # Obtenemos clientes con sus cuentas y últimos pagos
        query = """
            SELECT 
                c.id, c.client_code, c.name, c.whatsapp, c.client_type, c.created_at,
                COUNT(sa.id) as total_accounts,
                SUM(CASE WHEN sa.status = 'ocupada' THEN 1 ELSE 0 END) as active_accounts,
                SUM(CASE WHEN sa.status = 'caida' THEN 1 ELSE 0 END) as fallen_accounts,
                SUM(CASE WHEN sa.payment_status = 'pendiente' THEN 1 ELSE 0 END) as unpaid_accounts,
                MAX(p.created_at) as last_payment_date
            FROM clients c
            LEFT JOIN streaming_accounts sa ON sa.client_id = c.id
            LEFT JOIN payments p ON p.client_id = c.id
            GROUP BY c.id
        """
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
