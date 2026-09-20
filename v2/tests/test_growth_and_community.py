"""
test_growth_and_community.py - Suite 10 del Test Harness de Calidad (StreamVault v2)
Valida exhaustivamente:
1. Programa de referidos: asignación de código, acumulación de saldo, anti-auto-referido y canjes.
2. Motor de cupones de descuento: porcentaje vs monto fijo, compra mínima, caducidad y límite de usos.
3. Gamificación comunitaria: puntos por mensaje, promoción de rangos (Bronce, Plata, Oro, Diamante), podio y privacidad telefónica.
4. Auto-respuestas a FAQs: normalización, coincidencia por palabras clave y frases compuestas.
5. Predictor de Churn: scoring de retención y alertas preventivas en el CRM.
"""

import time
from datetime import datetime, timedelta

from core.growth import (
    ReferralManager,
    CouponManager,
    GamificationManager,
    FAQEngine,
    ChurnPredictor
)
import database
from db.connection import get_connection


def test_referral_program():
    """1. Valida el programa de referidos, comisiones y canje de saldo."""
    print("  [1/5] Probando Programa de Referidos y Saldo a Favor...")

    # Crear dos clientes para la prueba
    c1 = database.find_or_create_client(name="Carlos Referidor", whatsapp="5491111111111")
    c2 = database.find_or_create_client(name="Ana Compradora", whatsapp="5491122222222")

    c1_id = c1["id"]
    c2_id = c2["id"]

    # 1. Obtener o crear código de referido
    ref_info = ReferralManager.get_or_create_code(c1_id)
    assert ref_info is not None
    code1 = ref_info["code"]
    assert code1.startswith("REF-")
    assert ref_info["reward_balance_ars"] == 0.0
    assert ref_info["total_referred"] == 0

    # Idempotencia: volver a pedir el código debe retornar el mismo
    ref_info_again = ReferralManager.get_or_create_code(c1_id)
    assert ref_info_again["code"] == code1

    # 2. Bloqueo de auto-referido
    ok_self, msg_self, comm_self = ReferralManager.process_referral_purchase(
        referrer_code=code1,
        referred_client_id=c1_id,
        purchase_amount=10000.0,
        commission_percent=10.0
    )
    assert not ok_self, "Un cliente no debe poder auto-referenciarse"
    assert comm_self == 0.0

    # 3. Procesar compra referida legítima de cliente 2
    ok_ref, msg_ref, comm = ReferralManager.process_referral_purchase(
        referrer_code=code1,
        referred_client_id=c2_id,
        purchase_amount=8500.0,
        commission_percent=10.0  # 10% de 8500 = 850.0 ARS
    )
    assert ok_ref, f"La compra referida debe ser exitosa: {msg_ref}"
    assert comm == 850.0

    # Verificar saldo actualizado de Carlos
    ref_updated = database.get_referral_by_client_id(c1_id)
    assert ref_updated["reward_balance_ars"] == 850.0
    assert ref_updated["total_referred"] == 1

    # 4. Canje de saldo parcial
    ok_redeem, msg_redeem, new_bal = ReferralManager.redeem_balance(c1_id, 500.0)
    assert ok_redeem, f"El canje debe ser exitoso: {msg_redeem}"
    assert new_bal == 350.0

    # Canje con saldo superior al disponible debe fallar
    ok_bad, msg_bad, _ = ReferralManager.redeem_balance(c1_id, 1000.0)
    assert not ok_bad, "No debe permitir canjear más saldo del acumulado"

    # Formateo de resumen para WhatsApp
    summary = ReferralManager.format_referral_summary(c1_id)
    assert code1 in summary
    assert "$350.00 ARS" in summary

    print("    ✅ Generación de códigos, comisiones, no-auto-referido y canjes de saldo validados.")


def test_coupon_engine():
    """2. Valida el motor de cupones de descuento, caducidad y límites."""
    print("  [2/5] Probando Motor de Cupones de Descuento (Límites, Expiración y Redención)...")

    # 1. Cupón porcentual válido (15% OFF, compra mínima $5000, máx 2 usos)
    c_pct = CouponManager.create(
        code="PROMO15_TEST",
        discount_type="percent",
        discount_value=15.0,
        min_purchase=5000.0,
        max_uses=2,
        expires_in_days=10
    )
    assert c_pct["code"] == "PROMO15_TEST"
    assert c_pct["discount_type"] == "percent"

    # Validar cálculo: orden de $10000 -> 15% desc = $1500, final = $8500
    is_val, msg, disc, final_amt = CouponManager.validate_and_calculate("PROMO15_TEST", 10000.0)
    assert is_val, f"El cupón debe ser válido: {msg}"
    assert disc == 1500.0
    assert final_amt == 8500.0

    # Compra inferior al mínimo ($4000 < $5000) debe ser rechazada
    is_min_val, msg_min, _, _ = CouponManager.validate_and_calculate("PROMO15_TEST", 4000.0)
    assert not is_min_val, "No debe aplicar cupón si el monto no supera la compra mínima"
    assert "compra mínima" in msg_min.lower()

    # 2. Cupón de monto fijo ($2000 ARS de descuento)
    c_fix = CouponManager.create(
        code="BIENVENIDA2000",
        discount_type="fixed_ars",
        discount_value=2000.0,
        min_purchase=3000.0,
        max_uses=10,
        expires_in_days=5
    )
    is_fix_val, _, disc_fix, final_fix = CouponManager.validate_and_calculate("BIENVENIDA2000", 6000.0)
    assert is_fix_val
    assert disc_fix == 2000.0
    assert final_fix == 4000.0

    # 3. Cupón expirado (creado con fecha pasada)
    past_date = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    database.create_coupon(
        code="EXPIRADO_TEST",
        discount_type="percent",
        discount_value=20.0,
        expires_at=past_date
    )
    is_exp, msg_exp, _, _ = CouponManager.validate_and_calculate("EXPIRADO_TEST", 8000.0)
    assert not is_exp, "El cupón expirado debe ser rechazado"
    assert "expirado" in msg_exp.lower()

    # 4. Redención y límite de usos
    c_limit = CouponManager.create(
        code="UNICO_USO",
        discount_type="percent",
        discount_value=50.0,
        max_uses=1,
        expires_in_days=3
    )
    # Primer uso legítimo
    ok_u1, _, _, _ = CouponManager.redeem("UNICO_USO", 5000.0)
    assert ok_u1, "Primer uso debe ser exitoso"

    # Segundo uso debe fallar por cupo agotado
    ok_u2, msg_u2, _, _ = CouponManager.redeem("UNICO_USO", 5000.0)
    assert not ok_u2, "Segundo uso debe ser rechazado por agotar cupos"
    assert "agotado su cupo" in msg_u2.lower()

    print("    ✅ Descuentos porcentuales, fijos, compra mínima, expiración y límite de usos validados.")


def test_community_gamification():
    """3. Valida la gamificación comunitaria, tiers y ranking de grupos."""
    print("  [3/5] Probando Gamificación de Comunidad (Actividad, Rangos y Ranking)...")

    group_jid = "120363001122334455@g.us"
    phone_lead = "5491166099952"
    phone_silver = "5491144445555"

    # 1. Registrar actividad de mensajes
    # Miembro 1 envía 10 mensajes (10 * 5 = 50 puntos -> alcanza Plata)
    for _ in range(10):
        act1 = GamificationManager.record_activity(group_jid, phone_lead, push_name="Manu VIP")
    assert act1["message_count"] == 10
    assert act1["points"] == 50
    assert act1["level_tier"] == "Plata"

    # Miembro 1 continúa hasta 40 mensajes (40 * 5 = 200 puntos -> alcanza Oro)
    for _ in range(30):
        act1 = GamificationManager.record_activity(group_jid, phone_lead, push_name="Manu VIP")
    assert act1["points"] == 200
    assert act1["level_tier"] == "Oro"

    # Miembro 2 envía 2 mensajes (10 puntos -> Bronce)
    for _ in range(2):
        act2 = GamificationManager.record_activity(group_jid, phone_silver, push_name="Pedro")
    assert act2["points"] == 10
    assert act2["level_tier"] == "Bronce"

    # 2. Consultar leaderboard del grupo
    leaderboard = GamificationManager.get_leaderboard(group_jid, limit=5)
    assert len(leaderboard) >= 2
    assert leaderboard[0]["phone"] == phone_lead
    assert leaderboard[0]["points"] == 200
    assert leaderboard[0]["level_tier"] == "Oro"

    # 3. Formatear podio para WhatsApp
    podium_text = GamificationManager.format_leaderboard(leaderboard, group_name="Comunidad VIP")
    assert "TOP MIEMBROS MÁS ACTIVOS" in podium_text
    assert "Manu VIP" in podium_text
    assert "Oro" in podium_text

    # 4. Privacidad telefónica (ofuscación)
    obf = GamificationManager.obfuscate_phone("5491166099952")
    assert "***" in obf
    assert "5491" in obf
    assert "9952" in obf

    print("    ✅ Puntos por actividad, promociones de rango (Bronce/Plata/Oro), podio y privacidad validados.")


def test_community_faqs():
    """4. Valida el motor de auto-respuestas inteligentes a FAQs."""
    print("  [4/5] Probando Motor de Auto-Respuestas a Preguntas Frecuentes (FAQs)...")

    # 1. Consulta con intención de medios de pago
    match_pay = FAQEngine.find_match("Hola buenas tardes, ¿cómo puedo pagar mi cuenta?")
    assert match_pay is not None, "Debe coincidir con la FAQ de pagos"
    assert "transferencia" in match_pay["answer"].lower() or "mercado pago" in match_pay["answer"].lower()

    # 2. Consulta con alias o CBU
    match_cbu = FAQEngine.find_match("me pasas el alias o cbu por favor?")
    assert match_cbu is not None
    assert match_cbu["category"] == "pagos"

    # 3. Consulta de soporte o caída
    match_soporte = FAQEngine.find_match("se me cayo la cuenta de netflix no anda")
    assert match_soporte is not None
    assert match_soporte["category"] == "soporte"

    # 4. Consulta de catálogo o precios
    match_cat = FAQEngine.find_match("hola tienen lista de precios o catalogo disponible?")
    assert match_cat is not None
    assert match_cat["category"] == "ventas"

    # 5. Mensaje casual sin relación a ninguna FAQ
    match_none = FAQEngine.find_match("jajaja que buena pelicula esa")
    assert match_none is None, "Mensajes casuales no deben disparar auto-respuestas FAQ"

    print("    ✅ Coincidencias semánticas, descarte de falsos positivos y respuestas contextuales validadas.")


def test_churn_prediction_scoring():
    """5. Valida el algoritmo predictivo de riesgo de abandono (Churn)."""
    print("  [5/5] Probando Predictor Analítico de Riesgo de Churn (Retención CRM)...")

    # Caso A: Cliente fiel, al día, sin caídas
    cli_fiel = {
        "id": 901,
        "client_code": "CLI-901",
        "name": "Cliente Fiel",
        "whatsapp": "5491199990001",
        "total_accounts": 2,
        "active_accounts": 2,
        "fallen_accounts": 0,
        "unpaid_accounts": 0,
        "last_payment_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }
    eval_fiel = ChurnPredictor.evaluate_client(cli_fiel)
    assert eval_fiel["risk_level"] == "BAJO"
    assert eval_fiel["churn_score"] < 40
    assert eval_fiel["icon"] == "🟢"

    # Caso B: Cliente con pagos pendientes y cuentas caídas (Alerta Roja)
    cli_riesgo = {
        "id": 902,
        "client_code": "CLI-902",
        "name": "Cliente Descontento",
        "whatsapp": "5491199990002",
        "total_accounts": 2,
        "active_accounts": 0,
        "fallen_accounts": 1,
        "unpaid_accounts": 1,
        "last_payment_date": (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    }
    eval_riesgo = ChurnPredictor.evaluate_client(cli_riesgo)
    assert eval_riesgo["risk_level"] == "ALTO"
    assert eval_riesgo["churn_score"] >= 70
    assert eval_riesgo["icon"] == "🔴"
    assert any("pago pendiente" in f for f in eval_riesgo["factors"])
    assert any("reportada como caída" in f for f in eval_riesgo["factors"])

    # Formateo de reporte para consola de administración
    report_text = ChurnPredictor.format_report([eval_riesgo, eval_fiel])
    assert "REPORTE PREDICTIVO DE RIESGO DE CHURN" in report_text
    assert "Cliente Descontento" in report_text
    assert "🔴" in report_text

    print("    ✅ Scoring preventivo de Churn, categorización por riesgo y reporte CRM validados.")


def run_tests():
    """Ejecutor de la Suite 10 para el Harness."""
    print("Iniciando Suite 10: Crecimiento Comercial, Cupones, Referidos y Comunidad (Fase 4)...")
    test_referral_program()
    test_coupon_engine()
    test_community_gamification()
    test_community_faqs()
    test_churn_prediction_scoring()
    print("✅ Suite 10 completada exitosamente sin incidencias.")


if __name__ == "__main__":
    run_tests()
