"""
Suite 18 (Bloque 4 - Q06): Consolidación de Árboles Duplicados y Fachadas Canónicas.
Verifica que no existan implementaciones divergentes entre:
- db/repositories/* <-> infrastructure/persistence/repositories/*
- services/receipt_service.py <-> infrastructure/ocr/receipt_service.py
- services/chatwoot_bot_service.py <-> infrastructure/external/chatwoot/bot_service.py
- mcp_server/models.py <-> presentation/mcp/models.py
- mcp_server/tools/* <-> presentation/mcp/tools/*
"""

import importlib
import inspect
from pathlib import Path
import unittest


V2_ROOT = Path(__file__).resolve().parent.parent


class TestBloque4Q06Consolidation(unittest.TestCase):
    """Pruebas de regresión para Q06: única fuente de verdad (SSOT) y fachadas explícitas."""

    def test_repositories_identity_and_thin_facades(self):
        """Todos los repositorios en db.repositories e infrastructure.persistence.repositories comparten identidad exacta."""
        repo_names = [
            "accounts_repo",
            "admin_repo",
            "audit_repo",
            "catalog_repo",
            "clients_repo",
            "fallen_reports_repo",
            "finance_repo",
            "groups_moderation_repo",
            "growth_repo",
            "payments_approval_repo",
            "settings_repo",
            "suppliers_repo",
        ]

        # Repositorios cuya fuente canónica está en infrastructure/persistence/repositories/
        infra_canonical = {"audit_repo", "growth_repo"}

        for name in repo_names:
            db_mod = importlib.import_module(f"db.repositories.{name}")
            infra_mod = importlib.import_module(f"infrastructure.persistence.repositories.{name}")

            db_Callables = {
                k: v for k, v in vars(db_mod).items()
                if not k.startswith("__") and callable(v)
            }
            self.assertTrue(len(db_Callables) > 0, f"El repositorio {name} no exporta funciones")

            for sym, fn_obj in db_Callables.items():
                self.assertTrue(
                    hasattr(infra_mod, sym),
                    f"Símbolo '{sym}' falta en infrastructure.persistence.repositories.{name}"
                )
                self.assertIs(
                    fn_obj,
                    getattr(infra_mod, sym),
                    f"Divergencia detectada en {name}.{sym}: objetos distintos entre db/ e infrastructure/"
                )

            if name in infra_canonical:
                facade_path = V2_ROOT / "db" / "repositories" / f"{name}.py"
            else:
                facade_path = V2_ROOT / "infrastructure" / "persistence" / "repositories" / f"{name}.py"

            lines = [
                ln.strip() for ln in facade_path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith('"""')
            ]
            self.assertLessEqual(
                len(lines),
                5,
                f"La fachada {facade_path.relative_to(V2_ROOT)} contiene lógica duplicada ({len(lines)} líneas activas)"
            )

    def test_ocr_and_chatwoot_services_identity(self):
        """services.receipt_service y services.chatwoot_bot_service son idénticos a sus fachadas en infrastructure/."""
        import services.receipt_service as canon_ocr
        import infrastructure.ocr.receipt_service as facade_ocr

        self.assertIs(
            canon_ocr.extract_receipt_data_from_base64,
            facade_ocr.extract_receipt_data_from_base64,
        )
        self.assertEqual(canon_ocr.MAX_BASE64_LENGTH, facade_ocr.MAX_BASE64_LENGTH)
        self.assertEqual(canon_ocr.MAX_DECODED_BYTES, facade_ocr.MAX_DECODED_BYTES)
        self.assertEqual(
            canon_ocr.OCR_SUBPROCESS_TIMEOUT_SECONDS,
            facade_ocr.OCR_SUBPROCESS_TIMEOUT_SECONDS,
        )

        import services.chatwoot_bot_service as canon_cw
        import infrastructure.external.chatwoot.bot_service as facade_cw

        self.assertIs(
            canon_cw.handle_chatwoot_webhook_payload,
            facade_cw.handle_chatwoot_webhook_payload,
        )
        self.assertIs(
            canon_cw.send_chatwoot_reply,
            facade_cw.send_chatwoot_reply,
        )

    def test_mcp_models_and_tools_identity(self):
        """mcp_server/* y presentation/mcp/* apuntan exactamente a las mismas clases Pydantic y registradores."""
        import mcp_server.models as canon_models
        import presentation.mcp.models as facade_models

        model_classes = [
            k for k, v in vars(canon_models).items()
            if inspect.isclass(v) and k.endswith("Input")
        ]
        self.assertGreaterEqual(len(model_classes), 10)
        for cls_name in model_classes:
            self.assertIs(
                getattr(canon_models, cls_name),
                getattr(facade_models, cls_name),
                f"Modelo MCP divergente: {cls_name}"
            )

        tool_modules = [
            ("account_tools", "register_account_tools"),
            ("catalog_tools", "register_catalog_tools"),
            ("client_tools", "register_client_tools"),
            ("finance_tools", "register_finance_tools"),
            ("group_tools", "register_group_tools"),
            ("system_tools", "register_system_tools"),
        ]
        for mod_name, reg_fn in tool_modules:
            canon_t = importlib.import_module(f"mcp_server.tools.{mod_name}")
            facade_t = importlib.import_module(f"presentation.mcp.tools.{mod_name}")
            self.assertIs(
                getattr(canon_t, reg_fn),
                getattr(facade_t, reg_fn),
                f"Registrador MCP divergente en {mod_name}.{reg_fn}"
            )

            facade_file = V2_ROOT / "presentation" / "mcp" / "tools" / f"{mod_name}.py"
            content = facade_file.read_text(encoding="utf-8")
            self.assertNotIn(
                "@mcp.tool",
                content,
                f"La fachada {facade_file.relative_to(V2_ROOT)} no debe declarar @mcp.tool duplicados"
            )


def run_tests() -> bool:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestBloque4Q06Consolidation)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
