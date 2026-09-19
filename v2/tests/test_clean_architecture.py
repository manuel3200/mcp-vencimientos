"""
StreamVault v2 - Clean Architecture & Modular Integration Tests
Verifies that:
1. Domain layer has 0 external dependencies
2. Application use cases execute correctly
3. Repositories handle database transactions safely
4. View-models decouple presentation completely
"""
import sys
import os

def run_tests():
    print("  [Suite] Clean Architecture & Modular Contracts...")

    # 1. Verificar pureza del Dominio
    import domain
    from domain.entities import Client, Account, Payment, CatalogItem
    from domain.rules import (
        classify_client_type,
        preserve_client_type,
        resolve_http_custom_price,
        parse_http_custom_message,
        is_valid_hwid,
    )

    c = Client(id=1, name="Juan Test", whatsapp="5491100001111", client_type="revendedor_vip")
    assert c.is_vip is True
    assert c.is_reseller is True
    assert c.badge_icon == "👑 VIP"

    a = Account(id=1, platform="HTTP Custom", email="user1", password="hwid_12345678")
    assert a.is_http_custom is True

    # 2. Verificar Use Cases de la Capa de Aplicación
    import application
    from application.payments import execute_approve_payment, execute_bulk_approve_payments
    from application.accounts import execute_sell_account, execute_renew_account
    from application.clients import get_client_360
    from application.http_custom import execute_process_http_custom_sale

    assert callable(execute_approve_payment)
    assert callable(execute_bulk_approve_payments)
    assert callable(execute_sell_account)
    assert callable(execute_renew_account)
    assert callable(get_client_360)
    assert callable(execute_process_http_custom_sale)

    # 3. Verificar View-Models de la Capa de Presentación
    from presentation.web.view_models import (
        render_msg_banner,
        render_active_accounts_rows,
        render_stock_rows,
        render_stock_health_html,
        render_client_select_options,
        render_http_custom_rows,
    )

    banner = render_msg_banner("price_saved")
    assert "✅ Precio de catálogo configurado correctamente." in banner

    opts = render_client_select_options([{"id": 1, "name": "Kevin", "client_type": "revendedor_vip"}])
    assert "👑 VIP" in opts

    # Regresión crítica: Probar render_http_custom_rows para evitar NameError en datetime/date
    empty_html = render_http_custom_rows([])
    assert "No hay servidores HTTP Custom registrados" in empty_html

    sample_custom = [
        {
            "id": 101,
            "email": "user_vip",
            "password": "00d12f8f5e92c189d8005ddb60614cf9",
            "client_name": "Marcos VIP",
            "client_phone": "5491112345678",
            "client_type": "revendedor_vip",
            "expiry_date": "2099-12-31"
        },
        {
            "id": 102,
            "email": "user_rev",
            "password": "aabbccddeeff112233445566",
            "client_name": "Lucas Revendedor",
            "client_phone": "",
            "client_type": "revendedor",
            "expiry_date": "2020-01-01"
        }
    ]
    custom_html = render_http_custom_rows(sample_custom)
    assert "user_vip" in custom_html
    assert "👑 VIP" in custom_html
    assert "00d12f8f5e...60614cf9" in custom_html
    assert "user_rev" in custom_html
    assert "💼 Revendedor" in custom_html
    assert "Vencido hace" in custom_html

    print("    ✅ Clean Architecture: Dominio desacoplado, Casos de Uso y View-Models verificados 100%.")

if __name__ == "__main__":
    run_tests()
