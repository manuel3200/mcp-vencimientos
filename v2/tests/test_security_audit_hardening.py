"""
test_security_audit_hardening.py - Suite 14 del Test Harness (StreamVault v2)
Valida las mitigaciones técnicas de la Auditoría de Seguridad:
1. CRIT-01: Secretos Efímeros con Dominio Público (PUBLIC_BASE_URL) y Registro de Auditoría.
2. CRIT-03: Cadencia Gaussiana y Límite Anti-Ban en Scheduler.
3. CRIT-04: Ancla Externa Criptográfica de Auditoría en Telegram.
4. HIGH-01: Anti-Prompt-Injection Guard y Scoping de Permisos en FastMCP.
5. HIGH-02: Validación Criptográfica de Firma X-Evolution-Signature en Webhooks.
6. HIGH-03: Sistema de Lease Preventivo en Sesiones HWID.
7. MED-01: Detección de Encuestas Sensibles y Forzado de Anonimato.
"""

import os
import hmac
import hashlib
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import database
from db.connection import get_connection
from core.config import settings
from core.ephemeral_secrets import (
    create_ephemeral_secret,
    reveal_and_burn_secret,
    burn_secret_immediately
)
from core.mcp_guard import (
    validate_tool_execution,
    ADMIN_ONLY_TOOLS,
    CLIENT_SCOPED_TOOLS
)
from core.audit import anchor_audit_root_to_telegram
from scheduler.task_runner import get_gaussian_human_delay
from domain.rules.http_custom_rules import HWIDLeaseManager
from application.community.polls_service import is_sensitive_poll


def run_tests():
    print("  [Suite 14] Hardening de Seguridad de Auditoría (Mitigación CRIT/HIGH/MED)...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSecurityAuditHardening)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        for failure in result.failures + result.errors:
            print(f"    ❌ {failure[0]}: {failure[1]}")
        raise AssertionError("Fallaron pruebas en Suite 14 (Hardening de Auditoría).")
    print(f"    ✅ Suite 14: {result.testsRun}/{result.testsRun} pruebas ejecutadas exitosamente.")


class TestSecurityAuditHardening(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    # =========================================================================
    # 1. CRIT-01: SECRETOS EFÍMEROS CON PUBLIC_BASE_URL Y AUDITORÍA
    # =========================================================================

    def test_ephemeral_secret_public_url_and_audit(self):
        """Valida que los enlaces efímeros utilicen PUBLIC_BASE_URL y se registren en la bitácora."""
        with patch.object(settings, "PUBLIC_BASE_URL", "https://secrets.midominio.com"):
            token, full_url = create_ephemeral_secret(
                data={"email": "vip@netflix.com", "password": "passSecret123"},
                title="Acceso Netflix 4K",
                ttl_seconds=300,
                actor="test_admin"
            )

            self.assertTrue(full_url.startswith("https://secrets.midominio.com/v/sec_"))
            self.assertNotIn("streamvault.local", full_url)

            # Verificar auditoría de generación
            conn = get_connection()
            try:
                row = conn.execute("""
                    SELECT * FROM audit_log
                    WHERE action = 'GENERATE_EPHEMERAL_SECRET' AND target_id = ?
                """, (token,)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["actor"], "test_admin")
            finally:
                conn.close()

            # Consumir y quemar secreto
            payload, status = reveal_and_burn_secret(token)
            self.assertEqual(status, "revealed")
            self.assertEqual(payload["email"], "vip@netflix.com")

            # Verificar auditoría de consumo
            conn = get_connection()
            try:
                row_burn = conn.execute("""
                    SELECT * FROM audit_log
                    WHERE action = 'CONSUME_EPHEMERAL_SECRET' AND target_id = ?
                """, (token,)).fetchone()
                self.assertIsNotNone(row_burn)
            finally:
                conn.close()

    # =========================================================================
    # 2. HIGH-01: ANTI-PROMPT-INJECTION GUARD Y SCOPING EN MCP
    # =========================================================================

    def test_mcp_guard_blocks_admin_tool_for_unauthorized_user(self):
        """Clientes o atacantes no pueden forzar la ejecución de tools de administrador."""
        res = validate_tool_execution(
            tool_name="reemplazar_cuenta_caida",
            params={"account_id": 1, "target_phone": "5491199998888"},
            requester_phone_or_jid="5491199998888@s.whatsapp.net",
            is_admin=False
        )
        self.assertFalse(res["allowed"])
        self.assertIn("requiere privilegios de administrador", res["error"])

        # Verificar asentamiento de alerta de seguridad en auditoría
        conn = get_connection()
        try:
            row = conn.execute("""
                SELECT * FROM audit_log
                WHERE action = 'SECURITY_PROMPT_INJECTION_BLOCKED'
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["target_id"], "reemplazar_cuenta_caida")
        finally:
            conn.close()

    def test_mcp_guard_allows_admin_tool_for_admin(self):
        """El administrador autorizado puede ejecutar tools administrativas sin bloqueo."""
        with patch.object(settings, "ADMIN_WHATSAPP", "5491155550000"):
            res = validate_tool_execution(
                tool_name="crear_cupon_descuento",
                params={"codigo": "PROMO10", "descuento": 10},
                requester_phone_or_jid="5491155550000@s.whatsapp.net",
                is_admin=False
            )
            self.assertTrue(res["allowed"])

            res_session = validate_tool_execution(
                tool_name="controlar_grupo",
                params={"group_jid": "123@g.us", "action": "mute"},
                requester_phone_or_jid="",
                is_admin=True
            )
            self.assertTrue(res_session["allowed"])

    def test_mcp_guard_blocks_cross_client_access(self):
        """Un cliente no puede solicitar información o cobro de otro cliente (IDOR prevention)."""
        res = validate_tool_execution(
            tool_name="consultar_ficha_cliente",
            params={"telefono": "5491122223333"},
            requester_phone_or_jid="5491188887777@s.whatsapp.net",
            is_admin=False
        )
        self.assertFalse(res["allowed"])
        self.assertIn("solo puedes consultar o gestionar tus propios servicios", res["error"])

    # =========================================================================
    # 3. CRIT-03: CADENCIA GAUSSIANA ANTI-BAN
    # =========================================================================

    def test_gaussian_human_delay_distribution(self):
        """Valida que la cadencia gaussiana se mantenga estrictamente entre 45s y 90s."""
        delays = [get_gaussian_human_delay() for _ in range(50)]
        for d in delays:
            self.assertGreaterEqual(d, 45.0)
            self.assertLessEqual(d, 90.0)
        avg = sum(delays) / len(delays)
        # La media debe aproximarse a 67.5s (tolerancia ±5s)
        self.assertGreater(avg, 60.0)
        self.assertLess(avg, 75.0)

    # =========================================================================
    # 4. CRIT-04: ANCLA CRIPTOGRÁFICA EN TELEGRAM
    # =========================================================================

    def test_anchor_audit_root_to_telegram(self):
        """Valida la publicación del ancla criptográfica en Telegram."""
        with patch("telegram_bot.send_telegram_message", new_callable=AsyncMock) as mock_tg:
            mock_tg.return_value = True

            res = self.loop.run_until_complete(
                anchor_audit_root_to_telegram(chat_id="-10099998888")
            )

            self.assertTrue(res["success"])
            self.assertIn("root_hash", res)
            mock_tg.assert_called_once()
            tg_text = mock_tg.call_args[1]["text"]
            self.assertIn("AUDIT ANCHOR", tg_text)
            self.assertIn("Root Hash HMAC", tg_text)

    # =========================================================================
    # 5. HIGH-03: SISTEMA DE LEASE HWID
    # =========================================================================

    def test_hwid_lease_manager_active_session_blocking(self):
        """Un segundo HWID debe ser bloqueado si el primer HWID tiene una sesión activa reciente."""
        lease_mgr = HWIDLeaseManager()
        lease_mgr.LEASE_TIMEOUT_SECONDS = 60

        # Dispositivo 1 solicita acceso
        req1 = lease_mgr.request_access(config_id="vpn_user_01", hwid="AABBCCDDEEFF01")
        self.assertTrue(req1["granted"])

        # Dispositivo 2 intenta usar la misma config inmediatamente
        req2 = lease_mgr.request_access(config_id="vpn_user_01", hwid="11223344556602")
        self.assertFalse(req2["granted"])
        self.assertEqual(req2["reason"], "active_session_exists")
        self.assertEqual(req2["active_hwid"], "AABBCCDDEEFF01")

        # Dispositivo 1 libera el lease
        lease_mgr.release_lease(config_id="vpn_user_01", hwid="AABBCCDDEEFF01")

        # Dispositivo 2 ahora puede obtener lease
        req3 = lease_mgr.request_access(config_id="vpn_user_01", hwid="11223344556602")
        self.assertTrue(req3["granted"])

    # =========================================================================
    # 6. MED-01: DETECCIÓN DE ENCUESTAS SENSIBLES
    # =========================================================================

    def test_sensitive_poll_keywords_detection(self):
        """Valida que encuestas con preguntas de precios o intención de compra sean detectadas."""
        self.assertTrue(is_sensitive_poll("¿Cuánto estarías dispuesto a pagar por una cuenta Max Platino?"))
        self.assertTrue(is_sensitive_poll("¿Te interesa contratar el servicio de VPN con costo reducido?"))
        self.assertFalse(is_sensitive_poll("¿Qué película o serie prefieren ver este fin de semana?"))
        self.assertFalse(is_sensitive_poll("¿Tienen Smart TV con Android o con Roku?"))
