"""
test_community_automation_p3.py - Suite 13 del Test Harness (StreamVault v2)
Valida la arquitectura y reglas de negocio para la Prioridad 3:
1. Encuestas Interactivas de Demanda (WhatsApp & Telegram Native Polls).
2. Mensajes Periódicos Programados (Lunes Normas y Canales Oficiales, Viernes Liquidación de Casilleros y Combos).
3. Moderación Avanzada Anti-Estafas en Grupos Comunitarios (Heurística Anti-Scam, Anti-Link y Toxicidad).
4. Herramientas FastMCP y Endpoints REST de Comunidad.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import database
from db.connection import get_connection
from infrastructure.external.evolution_whatsapp.client import send_poll
from infrastructure.external.telegram.bot_app import send_telegram_poll
from application.community.polls_service import create_and_dispatch_poll
from application.community.scheduled_broadcast_service import (
    run_monday_rules_broadcast,
    run_friday_weekend_promo_broadcast,
    get_active_community_groups,
    MONDAY_RULES_MESSAGE,
    FRIDAY_PROMO_MESSAGE
)
from application.community.word_filter_service import inspect_community_message


def run_tests():
    print("  [Suite 13] Prioridad 3: Fidelización, Automatización Comunitaria y Sondeos...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCommunityAutomationP3)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        for failure in result.failures + result.errors:
            print(f"    ❌ {failure[0]}: {failure[1]}")
        raise AssertionError("Fallaron pruebas en Suite 13 (Prioridad 3).")
    print(f"    ✅ Suite 13: {result.testsRun}/{result.testsRun} pruebas ejecutadas exitosamente.")


class TestCommunityAutomationP3(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    # =========================================================================
    # 1. ENCUESTAS NATIVAS (EVOLUTION API WHATSAPP & TELEGRAM BOT)
    # =========================================================================

    def test_whatsapp_send_poll_payload_and_validation(self):
        """Valida que send_poll exija al menos 2 opciones y preserve grupos @g.us sin mutilar el JID."""
        # Validación: Menos de 2 opciones
        res_invalid = self.loop.run_until_complete(
            send_poll(
                recipient="120363099999999999@g.us",
                question="¿Qué opinan de Max Platino?",
                options=["Solo una opción"]
            )
        )
        self.assertFalse(res_invalid["success"])
        self.assertIn("al menos 2 opciones", res_invalid["error"])

        # Validación: Destino de grupo @g.us válido con mock de Evolution API
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": {"id": "POLL_MSG_001"}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            res_valid = self.loop.run_until_complete(
                send_poll(
                    recipient="120363099999999999@g.us",
                    question="¿Qué servidor HTTP Custom prefieren para el fin de semana?",
                    options=["Argentina Gamer", "USA Streaming", "Chile Low Ping"],
                    selectable_count=1
                )
            )

            self.assertTrue(res_valid["success"])
            self.assertEqual(res_valid["target"], "120363099999999999@g.us")
            self.assertEqual(res_valid["message_id"], "POLL_MSG_001")

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            json_payload = call_kwargs["json"]
            self.assertEqual(json_payload["number"], "120363099999999999@g.us")
            self.assertEqual(json_payload["name"], "¿Qué servidor HTTP Custom prefieren para el fin de semana?")
            self.assertEqual(json_payload["selectableCount"], 1)
            self.assertEqual(len(json_payload["values"]), 3)

    def test_telegram_send_poll_payload(self):
        """Valida que send_telegram_poll despache la petición al endpoint oficial con los flags requeridos."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"poll": {"id": "TG_POLL_123"}}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            ok = self.loop.run_until_complete(
                send_telegram_poll(
                    chat_id="-1001234567890",
                    question="¿Qué plataforma deberíamos sumar al catálogo?",
                    options=["Crunchyroll Mega Fan", "Apple TV+", "Spotify Familiar"],
                    is_anonymous=True,
                    allows_multiple_answers=True
                )
            )

            self.assertTrue(ok)
            mock_post.assert_called_once()
            json_payload = mock_post.call_args[1]["json"]
            self.assertEqual(json_payload["chat_id"], "-1001234567890")
            self.assertEqual(json_payload["question"], "¿Qué plataforma deberíamos sumar al catálogo?")
            self.assertTrue(json_payload["is_anonymous"])
            self.assertTrue(json_payload["allows_multiple_answers"])
            self.assertEqual(len(json_payload["options"]), 3)

    def test_create_and_dispatch_poll_service(self):
        """Valida el orquestador create_and_dispatch_poll enviando a ambos canales y asentando HMAC audit log."""
        with patch("application.community.polls_service.send_poll", new_callable=AsyncMock) as mock_wa, \
             patch("application.community.polls_service.send_telegram_poll", new_callable=AsyncMock) as mock_tg:

            mock_wa.return_value = {"success": True, "message_id": "WA_P_001"}
            mock_tg.return_value = True

            res = self.loop.run_until_complete(
                create_and_dispatch_poll(
                    question="¿Interesados en Combo Familiar Streaming?",
                    options=["Sí, me sumo", "Ya tengo cuenta", "Prefiero VPN"],
                    send_whatsapp=True,
                    whatsapp_target="120363011111111111@g.us",
                    send_telegram=True,
                    telegram_target="-1009876543210",
                    category="demand",
                    actor="admin_tester"
                )
            )

            self.assertTrue(res["success"])
            self.assertTrue(res["whatsapp_sent"])
            self.assertTrue(res["telegram_sent"])
            self.assertEqual(res["category"], "demand")

            mock_wa.assert_called_once()
            mock_tg.assert_called_once()

            # Verificar asentamiento en bitácora de auditoría inmutable
            conn = get_connection()
            try:
                row = conn.execute("""
                    SELECT * FROM audit_logs 
                    WHERE action = 'DISPATCH_COMMUNITY_POLL'
                    ORDER BY id DESC LIMIT 1
                """).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["actor"], "admin_tester")
                self.assertEqual(row["target_type"], "poll")
                self.assertIn("q:¿Interesados en Combo Familiar Streaming?", row["new_value"])
            finally:
                conn.close()

    # =========================================================================
    # 2. COMUNICADOS PROGRAMADOS (LUNES NORMAS & VIERNES PROMOS)
    # =========================================================================

    def test_monday_rules_broadcast(self):
        """Valida que el comunicado de normas se despache a los grupos activos registrados con contenido oficial."""
        conn = get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO whatsapp_groups_config (group_jid, group_name, bot_enabled, antilink_enabled)
                VALUES ('120363012345678999@g.us', 'Comunidad VIP Alpha', 1, 1)
            """)
            conn.commit()
        finally:
            conn.close()

        with patch("application.community.scheduled_broadcast_service.send_channel_or_group_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "key": {"id": "RULE_MSG_01"}}

            res = self.loop.run_until_complete(
                run_monday_rules_broadcast(actor="cron_scheduler")
            )

            self.assertTrue(res["success"])
            self.assertGreaterEqual(res["sent_count"], 1)
            self.assertEqual(res["broadcast_type"], "monday_rules")

            mock_send.assert_called()
            sent_text = mock_send.call_args[1]["text"]
            self.assertIn("NORMAS DE LA COMUNIDAD", sent_text)
            self.assertIn("Privacidad Total", sent_text)
            self.assertIn("Medios de Pago Oficiales Habilitados", sent_text)

    def test_friday_weekend_promo_broadcast(self):
        """Valida que el comunicado de fin de semana promueva liquidación de casilleros y combos."""
        with patch("application.community.scheduled_broadcast_service.send_channel_or_group_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "key": {"id": "PROMO_MSG_01"}}

            res = self.loop.run_until_complete(
                run_friday_weekend_promo_broadcast(
                    target_groups=["120363012345678999@g.us"],
                    actor="manual_trigger"
                )
            )

            self.assertTrue(res["success"])
            self.assertEqual(res["sent_count"], 1)
            self.assertEqual(res["broadcast_type"], "friday_promo")

            mock_send.assert_called_once()
            sent_text = mock_send.call_args[1]["text"]
            self.assertIn("LIQUIDACIÓN DE CASILLEROS LIBRES", sent_text)
            self.assertIn("Netflix 4K (Casa Extra)", sent_text)
            self.assertIn("HTTP Custom VPN", sent_text)

    # =========================================================================
    # 3. MODERACIÓN HEURÍSTICA Y FILTRADO ANTI-ESTAFAS
    # =========================================================================

    def test_inspect_community_message_clean(self):
        """Mensajes normales de conversación deben ser permitidos sin falsos positivos."""
        clean_text = "¡Buenas tardes! ¿Alguien me confirma si la cuenta de Max funciona bien en Smart TV?"
        report = inspect_community_message(clean_text, sender_phone="5491100001111", group_jid="120363@g.us")

        self.assertTrue(report["is_safe"])
        self.assertEqual(report["threat_level"], "none")
        self.assertEqual(report["recommended_action"], "allow")
        self.assertEqual(len(report["detected_threats"]), 0)

    def test_inspect_community_message_scam(self):
        """Detecta patrones de estafas piramidales, cripto fraudes y venta de métodos truchos."""
        scam_text = "Gana dinero facil trabajando desde casa duplicando tu inversion con cripto gratis!"
        report = inspect_community_message(scam_text, sender_phone="5491199990000", group_jid="120363@g.us")

        self.assertFalse(report["is_safe"])
        self.assertEqual(report["threat_level"], "high")
        self.assertIn("scam_phishing", report["detected_threats"])
        self.assertEqual(report["recommended_action"], "delete_and_warn")

    def test_inspect_community_message_invite_link(self):
        """Detecta enlaces no autorizados de invitación a grupos de WhatsApp o canales ajenos de Telegram."""
        invite_wa = "Unite a mi grupo de ofertas chat.whatsapp.com/AbCdEfGhIjKlMnOpQrStUv"
        report_wa = inspect_community_message(invite_wa)

        self.assertFalse(report_wa["is_safe"])
        self.assertEqual(report_wa["threat_level"], "high")
        self.assertIn("unauthorized_invite_link", report_wa["detected_threats"])
        self.assertEqual(report_wa["recommended_action"], "delete_and_warn")

        invite_tg = "Canal vip gratis t.me/+aBcDeFgHiJkLmNoP"
        report_tg = inspect_community_message(invite_tg)
        self.assertFalse(report_tg["is_safe"])
        self.assertIn("unauthorized_invite_link", report_tg["detected_threats"])

    def test_inspect_community_message_toxic(self):
        """Detecta términos ofensivos o difamatorios en la comunidad."""
        toxic_text = "Son unos estafadores y unos hdp todos"
        report = inspect_community_message(toxic_text)

        self.assertFalse(report["is_safe"])
        self.assertEqual(report["threat_level"], "medium")
        self.assertIn("toxic_language", report["detected_threats"])
        self.assertEqual(report["recommended_action"], "warn")
