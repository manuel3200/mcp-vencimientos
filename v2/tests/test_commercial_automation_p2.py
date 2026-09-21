"""
test_commercial_automation_p2.py - Suite 12 del Test Harness (StreamVault v2)
Valida la arquitectura y reglas de negocio para la Prioridad 2:
1. Difusión Masiva en Canales (WhatsApp Channels @newsletter / @g.us y Canales de Telegram).
2. Reporte Financiero de Rentabilidad Neta Real por Plataforma (Margen neto real y tasa de caídas).
3. Alerta Automática de Variación de Costos de Proveedores (Detección de umbral, proyección y salvaguarda).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import database
from db.connection import get_connection
from application.channels.broadcast_service import (
    broadcast_announcement,
    format_whatsapp_broadcast,
    format_telegram_broadcast
)
from infrastructure.external.evolution_whatsapp.client import send_channel_or_group_message
from application.suppliers.cost_variance_service import evaluate_cost_variance


def run_tests():
    print("  [Suite 12] Prioridad 2: Automatización Comercial y Analítica de Negocio...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCommercialAutomationP2)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        for failure in result.failures + result.errors:
            print(f"    ❌ {failure[0]}: {failure[1]}")
        raise AssertionError("Fallaron pruebas en Suite 12 (Prioridad 2).")
    print(f"    ✅ Suite 12: {result.testsRun}/{result.testsRun} pruebas ejecutadas exitosamente.")


class TestCommercialAutomationP2(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    # =========================================================================
    # 1. DIFUSIÓN MASIVA EN CANALES (WHATSAPP & TELEGRAM)
    # =========================================================================

    def test_whatsapp_broadcast_formatting(self):
        """Valida que el formateador de WhatsApp construya badges por categoría y mantenga formato corporativo."""
        msg_promo = format_whatsapp_broadcast(
            title="Promo Relámpago",
            message="Descuento del 20% en combos",
            category="promo",
            platforms=["Netflix 4K", "Disney+ Premium"]
        )
        self.assertIn("PROMOCIÓN FLASH", msg_promo)
        self.assertIn("Promo Relámpago", msg_promo)
        self.assertIn("`Netflix 4K`", msg_promo)
        self.assertIn("`Disney+ Premium`", msg_promo)

        msg_stock = format_whatsapp_broadcast(
            title="Nuevos perfiles",
            message="Stock cargado",
            category="stock"
        )
        self.assertIn("NUEVO STOCK", msg_stock)

        msg_maint = format_whatsapp_broadcast(
            title="Mantenimiento de Servidores",
            message="Ventana técnica de 02:00 a 04:00",
            category="mantenimiento"
        )
        self.assertIn("AVISO DE MANTENIMIENTO", msg_maint)

    def test_telegram_broadcast_formatting(self):
        """Valida que el formateador de Telegram incluya etiquetas HTML válidas."""
        msg_tg = format_telegram_broadcast(
            title="Aviso a la Comunidad",
            message="Nuevo canal de atención disponible",
            category="comunicado",
            platforms=["HTTP Custom"]
        )
        self.assertIn("<b>¡COMUNICADO OFICIAL", msg_tg)
        self.assertIn("<b>Aviso a la Comunidad</b>", msg_tg)
        self.assertIn("<code>HTTP Custom</code>", msg_tg)

    def test_whatsapp_channel_jid_preservation(self):
        """Valida que send_channel_or_group_message preserve @newsletter y @g.us sin truncar dígitos."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": {"id": "CH_MSG_001"}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            # Canal oficial WhatsApp (@newsletter)
            res_ch = self.loop.run_until_complete(
                send_channel_or_group_message(
                    recipient="120363012345678901@newsletter",
                    text="Prueba en canal"
                )
            )
            self.assertTrue(res_ch.get("success"))
            payload_ch = mock_post.call_args[1]["json"]
            self.assertEqual(payload_ch["number"], "120363012345678901@newsletter")

            # Grupo de WhatsApp (@g.us)
            res_grp = self.loop.run_until_complete(
                send_channel_or_group_message(
                    recipient="120363098765432101@g.us",
                    text="Prueba en grupo"
                )
            )
            self.assertTrue(res_grp.get("success"))
            payload_grp = mock_post.call_args[1]["json"]
            self.assertEqual(payload_grp["number"], "120363098765432101@g.us")

            # Número estándar (debe extraer dígitos limpios)
            res_num = self.loop.run_until_complete(
                send_channel_or_group_message(
                    recipient="+54 9 11 2233 4455",
                    text="Prueba personal"
                )
            )
            self.assertTrue(res_num.get("success"))
            payload_num = mock_post.call_args[1]["json"]
            self.assertEqual(payload_num["number"], "5491122334455")

    def test_broadcast_announcement_service_and_audit(self):
        """Valida la difusión omnicanal y el registro en la bitácora inmutable de auditoría HMAC."""
        mock_wa_resp = {"success": True, "message_id": "WA_123"}

        with patch("application.channels.broadcast_service.send_channel_or_group_message", new_callable=AsyncMock) as mock_wa, \
             patch("application.channels.broadcast_service.broadcast_to_telegram_channel", new_callable=AsyncMock) as mock_tg:

            mock_wa.return_value = mock_wa_resp
            mock_tg.return_value = True

            res = self.loop.run_until_complete(
                broadcast_announcement(
                    title="Flash Sale Netflix 4K",
                    message="Cuentas completas con 30% OFF por 24hs",
                    category="promo",
                    platforms=["Netflix"],
                    send_whatsapp=True,
                    whatsapp_target="120363012345678901@newsletter",
                    send_telegram=True,
                    telegram_target="@streamvault_promo",
                    actor="admin_test"
                )
            )

            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("whatsapp_sent"))
            self.assertTrue(res.get("telegram_sent"))
            self.assertEqual(res.get("category"), "promo")

            # Verificar que se haya asentado en auditoría HMAC
            audit_records = database.get_audit_history(limit=5)
            broadcast_audits = [a for a in audit_records if a["action"] == "BROADCAST_CHANNEL"]
            self.assertGreaterEqual(len(broadcast_audits), 1)
            self.assertEqual(broadcast_audits[0]["actor"], "admin_test")

    # =========================================================================
    # 2. REPORTE FINANCIERO DE RENTABILIDAD NETA REAL POR PLATAFORMA
    # =========================================================================

    def test_profitability_by_platform_calculation(self):
        """Valida que la rentabilidad neta descuente ingresos, costos mayoristas y pérdidas por cuentas caídas."""
        conn = get_connection()
        try:
            with conn:
                # Limpiar cuentas de test previas
                conn.execute("DELETE FROM payments WHERE notes LIKE '%TestP2%'")
                conn.execute("DELETE FROM streaming_accounts WHERE email LIKE '%@testp2.com'")

                # Plataforma 1: Netflix (con 1 cuenta caída)
                cur1 = conn.execute("""
                    INSERT INTO streaming_accounts (platform, email, password, status, payment_status, cost, price)
                    VALUES ('Netflix P2', 'n1@testp2.com', 'pass', 'ocupada', 'pagado', '5000', '8500')
                """)
                acc1_id = cur1.lastrowid

                cur2 = conn.execute("""
                    INSERT INTO streaming_accounts (platform, email, password, status, payment_status, cost, price)
                    VALUES ('Netflix P2', 'n2@testp2.com', 'pass', 'ocupada', 'pagado', '5000', '8500')
                """)
                acc2_id = cur2.lastrowid

                # Cuenta caída con costo $5000
                conn.execute("""
                    INSERT INTO streaming_accounts (platform, email, password, status, payment_status, cost, price)
                    VALUES ('Netflix P2', 'n3_caida@testp2.com', 'pass', 'caida', 'pagado', '5000', '8500')
                """)

                # Cuenta libre en stock
                conn.execute("""
                    INSERT INTO streaming_accounts (platform, email, password, status, payment_status, cost, price)
                    VALUES ('Netflix P2', 'n4_libre@testp2.com', 'pass', 'libre', 'pagado', '5000', '8500')
                """)

                # Pagos cobrados para Netflix P2: 2 cobros de $8500 (Total cobrado = $17000, costo asentado = $10000)
                conn.execute("""
                    INSERT INTO payments (account_id, amount, cost, profit, notes)
                    VALUES (?, 8500.0, 5000.0, 3500.0, 'TestP2 Cobro 1')
                """, (acc1_id,))
                conn.execute("""
                    INSERT INTO payments (account_id, amount, cost, profit, notes)
                    VALUES (?, 8500.0, 5000.0, 3500.0, 'TestP2 Cobro 2')
                """, (acc2_id,))

                # Plataforma 2: HTTP Custom P2 (0 costo, 100% ganancia)
                cur_hc = conn.execute("""
                    INSERT INTO streaming_accounts (platform, email, password, status, payment_status, cost, price)
                    VALUES ('HTTP Custom P2', 'hc1@testp2.com', 'hwid123', 'ocupada', 'pagado', '0', '8000')
                """)
                acc_hc_id = cur_hc.lastrowid

                conn.execute("""
                    INSERT INTO payments (account_id, amount, cost, profit, notes)
                    VALUES (?, 8000.0, 0.0, 8000.0, 'TestP2 Cobro VPN')
                """, (acc_hc_id,))
        finally:
            conn.close()

        # Consultar rentabilidad
        report = database.get_profitability_by_platform()
        p2_netflix = next((p for p in report if p["platform"] == "Netflix P2"), None)
        p2_hc = next((p for p in report if p["platform"] == "HTTP Custom P2"), None)

        self.assertIsNotNone(p2_netflix)
        self.assertIsNotNone(p2_hc)

        # Validar métricas de Netflix P2:
        # Total cuentas: 4 (2 activas, 1 caída, 1 libre)
        self.assertEqual(p2_netflix["total_accounts"], 4)
        self.assertEqual(p2_netflix["active_accounts"], 2)
        self.assertEqual(p2_netflix["fallen_count"], 1)
        self.assertEqual(p2_netflix["free_stock"], 1)
        self.assertEqual(p2_netflix["gross_revenue"], 17000.0)
        self.assertEqual(p2_netflix["supplier_costs"], 10000.0)
        self.assertEqual(p2_netflix["fallen_cost"], 5000.0)

        # Ganancia neta real = 17000 - 10000 - 5000 = 2000 ARS
        self.assertEqual(p2_netflix["net_profit"], 2000.0)
        # Margen = (2000 / 17000) * 100 = 11.76%
        self.assertAlmostEqual(p2_netflix["profit_margin_pct"], 11.76, delta=0.1)
        # Tasa de caídas = 1 / 4 = 25.0%
        self.assertEqual(p2_netflix["fallen_rate_pct"], 25.0)

        # Validar métricas de HTTP Custom P2:
        self.assertEqual(p2_hc["net_profit"], 8000.0)
        self.assertEqual(p2_hc["profit_margin_pct"], 100.0)
        self.assertEqual(p2_hc["fallen_rate_pct"], 0.0)

        # Validar filtrado directo por plataforma
        filtered = database.get_profitability_by_platform(target_platform="HTTP Custom P2")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["platform"], "HTTP Custom P2")

    # =========================================================================
    # 3. ALERTA AUTOMÁTICA DE VARIACIÓN DE COSTOS DE PROVEEDORES
    # =========================================================================

    def test_cost_variance_significant_increase(self):
        """Valida que aumentos >= 5% disparen alerta, calculen margen y recomienden precio de venta."""
        with patch("infrastructure.external.telegram.bot_app.send_telegram_message", new_callable=AsyncMock) as mock_tg:
            mock_tg.return_value = True

            res = self.loop.run_until_complete(
                evaluate_cost_variance(
                    platform="Netflix Premium P2",
                    service_type="pantalla",
                    old_cost=5800.0,
                    new_cost=7000.0,  # Aumento de ~20.69%
                    threshold_pct=5.0,
                    min_ars_diff=200.0,
                    notify_telegram=True,
                    actor="test_admin"
                )
            )

            self.assertTrue(res.get("is_significant"))
            self.assertAlmostEqual(res.get("diff_pct"), 20.69, delta=0.1)
            self.assertEqual(res.get("diff_ars"), 1200.0)
            self.assertTrue(res.get("telegram_alert_sent"))
            self.assertTrue(mock_tg.called)

            # Precio recomendado para 35% de margen: 7000 / (1 - 0.35) = 10769 -> ~10800 ARS
            self.assertGreaterEqual(res.get("recommended_retail"), 10500.0)

    def test_cost_variance_insignificant_change(self):
        """Valida que fluctuaciones menores a 5% y menores a $200 ARS no disparen falsas alarmas."""
        with patch("infrastructure.external.telegram.bot_app.send_telegram_message", new_callable=AsyncMock) as mock_tg:
            res = self.loop.run_until_complete(
                evaluate_cost_variance(
                    platform="Disney+ P2",
                    service_type="pantalla",
                    old_cost=2000.0,
                    new_cost=2050.0,  # +2.5% y $50 ARS
                    threshold_pct=5.0,
                    min_ars_diff=200.0,
                    notify_telegram=True
                )
            )

            self.assertFalse(res.get("is_significant"))
            self.assertFalse(mock_tg.called)
            self.assertFalse(res.get("telegram_alert_sent"))

    def test_cost_variance_initial_setup(self):
        """Valida que configurar por primera vez un costo (costo previo 0) no se considere una alerta de variación."""
        res = self.loop.run_until_complete(
            evaluate_cost_variance(
                platform="Paramount+ P2",
                service_type="pantalla",
                old_cost=0.0,
                new_cost=1500.0
            )
        )
        self.assertFalse(res.get("is_significant"))


if __name__ == "__main__":
    run_tests()
