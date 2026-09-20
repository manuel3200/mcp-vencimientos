"""
test_evolution_advanced_features.py - Suite 11 del Test Harness (StreamVault v2)
Valida los contratos de integración de Evolution API v2 para:
1. send_reaction (reacción con emojis a mensajes)
2. send_contact_vcard (tarjeta de contacto profesional)
3. send_sticker (despacho de stickers para comunidad / fidelización)
4. mark_as_read (doble tilde azul en mensajes entrantes)
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import whatsapp_client
from infrastructure.external.evolution_whatsapp.client import (
    send_reaction,
    send_contact_vcard,
    send_sticker,
    mark_as_read
)


class TestEvolutionAdvancedFeatures(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_send_reaction_success(self):
        """Valida que send_reaction invoque el endpoint correcto y construya el payload esperado."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "message": "Reaction sent"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = self.loop.run_until_complete(
                send_reaction(
                    remote_jid="5491112345678@s.whatsapp.net",
                    message_id="MSG_12345",
                    emoji="✅"
                )
            )

            self.assertTrue(res.get("success"))
            self.assertTrue(mock_post.called)
            call_url = mock_post.call_args[0][0]
            call_json = mock_post.call_args[1]["json"]

            self.assertIn("/message/sendReaction/", call_url)
            self.assertEqual(call_json["reaction"], "✅")
            self.assertEqual(call_json["key"]["id"], "MSG_12345")
            self.assertEqual(call_json["key"]["remoteJid"], "5491112345678@s.whatsapp.net")

    def test_send_reaction_missing_jid(self):
        """send_reaction debe validar parámetros de forma defensiva."""
        res = self.loop.run_until_complete(send_reaction(remote_jid="", message_id="123", emoji="⏳"))
        self.assertFalse(res.get("success"))
        self.assertIn("requerido", res.get("error", ""))

    def test_send_contact_vcard_success(self):
        """Valida que send_contact_vcard genere la tarjeta VCard esperada."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"key": {"id": "VCARD_001"}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = self.loop.run_until_complete(
                send_contact_vcard(
                    phone="+54 9 11 2233 4455",
                    full_name="Atención StreamVault",
                    contact_phone="5491199887766",
                    organization="StreamVault Global"
                )
            )

            self.assertTrue(res.get("success"))
            call_url = mock_post.call_args[0][0]
            call_json = mock_post.call_args[1]["json"]

            self.assertIn("/message/sendContact/", call_url)
            self.assertEqual(call_json["number"], "5491122334455")
            self.assertEqual(len(call_json["contact"]), 1)
            self.assertEqual(call_json["contact"][0]["fullName"], "Atención StreamVault")
            self.assertEqual(call_json["contact"][0]["phoneNumber"], "+5491199887766")
            self.assertEqual(call_json["contact"][0]["organization"], "StreamVault Global")

    def test_send_sticker_success(self):
        """Valida que send_sticker envíe la URL o base64 del sticker."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": {"id": "STICKER_001"}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = self.loop.run_until_complete(
                send_sticker(
                    phone="5491133445566",
                    sticker_data_or_url="https://streamvault.com/stickers/vip.webp"
                )
            )

            self.assertTrue(res.get("success"))
            call_url = mock_post.call_args[0][0]
            call_json = mock_post.call_args[1]["json"]

            self.assertIn("/message/sendSticker/", call_url)
            self.assertEqual(call_json["number"], "5491133445566")
            self.assertEqual(call_json["sticker"], "https://streamvault.com/stickers/vip.webp")

    def test_mark_as_read_success(self):
        """Valida que mark_as_read envíe el readMessage con id y jid."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "READ"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = self.loop.run_until_complete(
                mark_as_read(
                    remote_jid="5491144556677@s.whatsapp.net",
                    message_id="MSG_READ_01"
                )
            )

            self.assertTrue(res.get("success"))
            call_url = mock_post.call_args[0][0]
            call_json = mock_post.call_args[1]["json"]

            self.assertIn("/chat/markMessageAsRead/", call_url)
            self.assertEqual(len(call_json["readMessages"]), 1)
            self.assertEqual(call_json["readMessages"][0]["id"], "MSG_READ_01")
            self.assertEqual(call_json["readMessages"][0]["remoteJid"], "5491144556677@s.whatsapp.net")


def run_tests():
    """Ejecutor de la Suite 11 para el Harness."""
    print("Iniciando Suite 11: Capacidades Avanzadas de Evolution API WhatsApp...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestEvolutionAdvancedFeatures)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(f"Fallaron {len(result.failures)} pruebas y {len(result.errors)} errores en Suite 11.")
    print("✅ Suite 11 completada exitosamente sin incidencias.")


if __name__ == "__main__":
    run_tests()
