"""
growth.py - Motor de Crecimiento Comercial, Gamificación y Fidelización (StreamVault v2)
Contiene la lógica de dominio para:
1. Programa de referidos y recompensas en saldo.
2. Motor de cupones de descuento con límites y caducidad.
3. Gamificación comunitaria en grupos de WhatsApp (insignias, puntos y podio).
4. Auto-respuestas inteligentes a FAQs mediante coincidencia por palabras clave.
5. Predictor analítico de riesgo de Churn en el CRM.
"""

import re
import math
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from infrastructure.persistence.repositories.growth_repo import (
    get_or_create_client_referral_code,
    get_referral_by_code,
    get_referral_by_client_id,
    credit_referral_reward,
    redeem_referral_balance,
    create_coupon as repo_create_coupon,
    get_coupon,
    record_coupon_redemption,
    list_active_coupons,
    increment_member_activity,
    get_group_leaderboard,
    list_active_faqs,
    get_client_churn_metrics
)

logger = logging.getLogger("core.growth")


# ==========================================
# 1. PROGRAMA DE REFERIDOS
# ==========================================

class ReferralManager:
    """Gestiona la generación de códigos, acreditación de comisiones y canjes."""

    @staticmethod
    def get_or_create_code(client_id: int, custom_code: Optional[str] = None) -> Dict[str, Any]:
        return get_or_create_client_referral_code(client_id, custom_code)

    @staticmethod
    def process_referral_purchase(
        referrer_code: str,
        referred_client_id: int,
        purchase_amount: float,
        commission_percent: float = 10.0
    ) -> Tuple[bool, str, float]:
        """Acredita comisión al referente por la compra de un nuevo cliente."""
        clean_code = referrer_code.strip().upper()
        ref_record = get_referral_by_code(clean_code)
        if not ref_record:
            return False, f"El código de referido '{clean_code}' no existe.", 0.0

        referrer_client_id = ref_record["client_id"]
        if referrer_client_id == referred_client_id:
            return False, "Un cliente no puede auto-referenciarse.", 0.0

        commission = round(purchase_amount * (commission_percent / 100.0), 2)
        if commission <= 0:
            return False, "Monto de comisión inválido.", 0.0

        ok = credit_referral_reward(referrer_client_id, referred_client_id, commission)
        if ok:
            return True, f"Comisión de ${commission:.2f} ARS acreditada exitosamente.", commission
        return False, "Error al acreditar la recompensa en la base de datos.", 0.0

    @staticmethod
    def redeem_balance(client_id: int, amount: float) -> Tuple[bool, str, float]:
        return redeem_referral_balance(client_id, amount)

    @staticmethod
    def format_referral_summary(client_id: int, bot_phone: str = "") -> str:
        """Genera un mensaje legible para el cliente sobre su estado de referidos."""
        record = get_referral_by_client_id(client_id)
        if not record:
            record = get_or_create_client_referral_code(client_id)

        code = record["code"]
        balance = float(record["reward_balance_ars"] or 0.0)
        total_refs = int(record["total_referred"] or 0)

        link_info = f"\n🔗 *Enlace de invitación:* https://wa.me/{bot_phone}?text=Hola!+Quiero+unirme+con+el+codigo+{code}" if bot_phone else ""

        return (
            f"🎁 *TU PROGRAMA DE REFERIDOS STREAMVAULT:*\n\n"
            f"🏷️ *Tu Código Único:* `{code}`\n"
            f"👥 *Amigos Invitados:* {total_refs}\n"
            f"💰 *Saldo a Favor Acumulado:* ${balance:.2f} ARS\n"
            f"{link_info}\n\n"
            f"💡 *¿Cómo funciona?*\n"
            f"Comparte tu código con amigos. Cuando adquieran su primera suscripción, recibirás saldo a favor que podrás descontar de tus renovaciones."
        )


# ==========================================
# 2. MOTOR DE CUPONES
# ==========================================

class CouponManager:
    """Gestiona y valida cupones de descuento con caducidad y límites de uso."""

    @staticmethod
    def create(
        code: str,
        discount_type: str,
        discount_value: float,
        min_purchase: float = 0.0,
        max_uses: int = 100,
        expires_in_days: int = 30
    ) -> Dict[str, Any]:
        exp_date = (datetime.utcnow() + timedelta(days=expires_in_days)).strftime("%Y-%m-%d %H:%M:%S")
        return repo_create_coupon(
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            min_purchase=min_purchase,
            max_uses=max_uses,
            expires_at=exp_date
        )

    @staticmethod
    def validate_and_calculate(
        code: str,
        order_amount: float,
        client_id: Optional[int] = None
    ) -> Tuple[bool, str, float, float]:
        """Valida si un cupón es aplicable a una orden y calcula el descuento.
        
        Retorna: (is_valid, mensaje, monto_descuento, monto_final)
        """
        clean_code = code.strip().upper()
        coupon = get_coupon(clean_code)
        if not coupon:
            return False, f"El cupón '{clean_code}' no existe.", 0.0, order_amount

        if not coupon.get("is_active"):
            return False, f"El cupón '{clean_code}' está inactivo.", 0.0, order_amount

        now_utc = datetime.utcnow()
        try:
            exp_dt = datetime.strptime(coupon["expires_at"], "%Y-%m-%d %H:%M:%S")
            if now_utc > exp_dt:
                return False, f"El cupón '{clean_code}' ha expirado el {coupon['expires_at'][:10]}.", 0.0, order_amount
        except Exception:
            pass

        times_used = int(coupon.get("times_used") or 0)
        max_uses = int(coupon.get("max_uses") or 1)
        if times_used >= max_uses:
            return False, f"El cupón '{clean_code}' ha agotado su cupo de canjes ({max_uses}/{max_uses}).", 0.0, order_amount

        min_purchase = float(coupon.get("min_purchase") or 0.0)
        if order_amount < min_purchase:
            return False, f"El cupón requiere una compra mínima de ${min_purchase:.2f} ARS.", 0.0, order_amount

        disc_type = coupon["discount_type"]
        disc_val = float(coupon["discount_value"])
        if disc_type == "percent":
            discount = round(order_amount * (disc_val / 100.0), 2)
        else:
            discount = min(disc_val, order_amount)

        final_amount = max(0.0, round(order_amount - discount, 2))
        return True, f"Cupón '{clean_code}' aplicado con éxito.", discount, final_amount

    @staticmethod
    def redeem(
        code: str,
        order_amount: float,
        client_id: Optional[int] = None
    ) -> Tuple[bool, str, float, float]:
        """Valida y aplica efectivamente el cupón, consumiendo 1 uso."""
        is_val, msg, discount, final_amt = CouponManager.validate_and_calculate(code, order_amount, client_id)
        if not is_val:
            return False, msg, 0.0, order_amount

        coupon = get_coupon(code)
        if coupon:
            record_coupon_redemption(coupon["id"], client_id, discount, order_amount)
        return True, msg, discount, final_amt


# ==========================================
# 3. GAMIFICACIÓN Y RANKING DE COMUNIDAD
# ==========================================

class GamificationManager:
    """Gestiona puntajes, rangos y tablas de clasificación en grupos."""

    TIERS = {
        "Diamante": {"min_pts": 500, "icon": "💎", "badge": "Leyenda VIP"},
        "Oro": {"min_pts": 200, "icon": "🥇", "badge": "Miembro Dorado"},
        "Plata": {"min_pts": 50, "icon": "🥈", "badge": "Miembro Activo"},
        "Bronce": {"min_pts": 0, "icon": "🥉", "badge": "Novato"}
    }

    @staticmethod
    def obfuscate_phone(phone: str) -> str:
        """Ofusca los dígitos centrales de un teléfono para proteger la privacidad."""
        clean = re.sub(r'[^0-9]', '', phone)
        if len(clean) >= 10:
            return f"+{clean[:4]} *** {clean[-4:]}"
        elif len(clean) >= 6:
            return f"{clean[:2]}***{clean[-2:]}"
        return clean

    @staticmethod
    def record_activity(group_jid: str, phone: str, push_name: str = "") -> Dict[str, Any]:
        return increment_member_activity(group_jid, phone, push_name)

    @staticmethod
    def get_leaderboard(group_jid: str, limit: int = 5) -> List[Dict[str, Any]]:
        return get_group_leaderboard(group_jid, limit)

    @classmethod
    def format_leaderboard(cls, records: List[Dict[str, Any]], group_name: str = "") -> str:
        if not records:
            return "🏆 *RANKING DE LA COMUNIDAD:*\n\nAún no hay suficiente actividad registrada en este grupo. ¡Sé el primero en participar!"

        title = f"🏆 *TOP MIEMBROS MÁS ACTIVOS* ({group_name}):\n" if group_name else "🏆 *TOP MIEMBROS MÁS ACTIVOS DE LA COMUNIDAD:*\n"
        lines = [title]

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for idx, row in enumerate(records):
            medal = medals[idx] if idx < len(medals) else f"#{idx+1}"
            name = row.get("push_name") or cls.obfuscate_phone(row.get("phone", ""))
            pts = row.get("points", 0)
            msgs = row.get("message_count", 0)
            tier = row.get("level_tier", "Bronce")
            tier_info = cls.TIERS.get(tier, {"icon": "🥉", "badge": "Miembro"})
            tier_badge = f"{tier_info['icon']} {tier}"

            lines.append(f"{medal} *{name}* — {tier_badge}\n   💬 {msgs} mensajes | ⭐ {pts} pts")

        lines.append("\n🎁 *Premio:* Los miembros Oro y Diamante acceden a sorteos mensuales y descuentos exclusivos en renovaciones.")
        return "\n".join(lines)


# ==========================================
# 4. AUTO-RESPUESTA A FAQS
# ==========================================

class FAQEngine:
    """Motor de coincidencia de preguntas frecuentes basado en palabras clave."""

    @staticmethod
    def normalize_text(text: str) -> str:
        """Limpia tildes, signos de puntuación y pasa a minúsculas."""
        s = text.lower()
        replacements = {
            'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
            'ü': 'u', 'ñ': 'n'
        }
        for orig, rep in replacements.items():
            s = s.replace(orig, rep)
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        return ' '.join(s.split())

    @classmethod
    def find_match(cls, message_text: str) -> Optional[Dict[str, Any]]:
        """Comprueba si el texto coincide con alguna FAQ activa."""
        clean_msg = cls.normalize_text(message_text)
        if len(clean_msg) < 3:
            return None

        words_in_msg = set(clean_msg.split())
        faqs = list_active_faqs()

        best_match = None
        highest_hits = 0

        for faq in faqs:
            triggers_raw = faq.get("keyword_triggers", "")
            triggers = [t.strip() for t in triggers_raw.split(",") if t.strip()]

            hits = 0
            for t in triggers:
                norm_t = cls.normalize_text(t)
                if " " in norm_t:
                    # Coincidencia de frase compuesta
                    if norm_t in clean_msg:
                        hits += 2
                else:
                    if norm_t in words_in_msg:
                        hits += 1

            if hits > highest_hits and hits >= 1:
                highest_hits = hits
                best_match = faq

        return best_match if highest_hits >= 1 else None


# ==========================================
# 5. DETECCIÓN PREDICTIVA DE CHURN
# ==========================================

class ChurnPredictor:
    """Calcula el riesgo de abandono de clientes según historial de pagos e incidentes."""

    @classmethod
    def evaluate_client(cls, client_row: Dict[str, Any]) -> Dict[str, Any]:
        """Calcula un puntaje de riesgo de 0 a 100 y sus factores de riesgo."""
        score = 0
        factors = []

        unpaid = int(client_row.get("unpaid_accounts") or 0)
        fallen = int(client_row.get("fallen_accounts") or 0)
        total = int(client_row.get("total_accounts") or 0)
        last_pay = client_row.get("last_payment_date")

        # Factor 1: Pagos pendientes o vencidos
        if unpaid > 0:
            score += 40
            factors.append(f"{unpaid} cuenta(s) con pago pendiente")

        # Factor 2: Cuentas caídas no resueltas
        if fallen > 0:
            score += 30
            factors.append(f"{fallen} cuenta(s) reportada(s) como caida")

        # Factor 3: Sin cuentas activas
        if total == 0:
            score += 25
            factors.append("No posee cuentas activas en el CRM")

        # Factor 4: Inactividad prolongada desde el último pago
        if last_pay:
            try:
                last_dt = datetime.strptime(last_pay[:19], "%Y-%m-%d %H:%M:%S")
                days_since = (datetime.utcnow() - last_dt).days
                if days_since > 45:
                    score += 20
                    factors.append(f"Sin pagos desde hace {days_since} dias")
            except Exception:
                pass
        else:
            score += 15
            factors.append("Nunca ha registrado un pago formal")

        score = min(score, 100)

        if score >= 70:
            risk_level = "ALTO"
            icon = "🔴"
        elif score >= 40:
            risk_level = "MEDIO"
            icon = "🟡"
        else:
            risk_level = "BAJO"
            icon = "🟢"

        return {
            "client_id": client_row["id"],
            "client_code": client_row.get("client_code") or f"CLI-{client_row['id']}",
            "name": client_row.get("name") or "Sin Nombre",
            "whatsapp": client_row.get("whatsapp") or "",
            "churn_score": score,
            "risk_level": risk_level,
            "icon": icon,
            "factors": factors
        }

    @classmethod
    def get_risk_report(cls, min_risk_level: str = "MEDIO") -> List[Dict[str, Any]]:
        """Genera el reporte de todos los clientes con riesgo mayor o igual al filtro."""
        metrics = get_client_churn_metrics()
        results = []
        for m in metrics:
            eval_res = cls.evaluate_client(m)
            if min_risk_level == "ALTO" and eval_res["risk_level"] != "ALTO":
                continue
            if min_risk_level == "MEDIO" and eval_res["risk_level"] == "BAJO":
                continue
            results.append(eval_res)

        results.sort(key=lambda x: x["churn_score"], reverse=True)
        return results

    @classmethod
    def format_report(cls, records: List[Dict[str, Any]]) -> str:
        if not records:
            return "✅ *AUDITORÍA DE CHURN Y RETENCIÓN:*\n\n¡Excelente! No hay clientes con riesgo medio o alto de abandono actualmente."

        lines = [
            "⚠️ *REPORTE PREDICTIVO DE RIESGO DE CHURN (RETENCIÓN):*\n"
            f"Clientes en alerta: {len(records)}\n"
        ]

        for r in records[:15]:
            factors_str = ", ".join(r["factors"]) if r["factors"] else "Comportamiento normal"
            lines.append(
                f"{r['icon']} *[{r['risk_level']}]* {r['name']} (`{r['client_code']}`)\n"
                f"   • Score: {r['churn_score']}/100 | WhatsApp: {r['whatsapp']}\n"
                f"   • Factores: {factors_str}"
            )

        lines.append("\n💡 *Sugerencia:* Ofrecer bonificación o cupón de descuento a los clientes en alerta roja.")
        return "\n".join(lines)
