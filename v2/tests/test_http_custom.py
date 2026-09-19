import asyncio
from datetime import date
from domain.http_custom_rules import parse_http_custom_message, parse_date_to_iso, is_valid_hwid
from services.http_custom_service import process_http_custom_outgoing_message
import database

def run_tests():
    print("  [Suite] HTTP Custom & HWID Rules...")

    # 1. Validación de fechas ISO
    assert parse_date_to_iso("18/09/2026") == "2026-09-18", "Fallo al convertir fecha DD/MM/YYYY"
    assert parse_date_to_iso("05-11-2026") == "2026-11-05", "Fallo al convertir fecha DD-MM-YYYY"
    assert parse_date_to_iso("invalid") is None, "Fecha inválida debe retornar None"

    # 2. Validación sintáctica de HWID
    assert is_valid_hwid("00d12f8f5e92c189d8005ddb60614cf9") is True
    assert is_valid_hwid("hwid") is False, "La palabra literal 'hwid' no es un hash válido"
    assert is_valid_hwid("short") is False, "HWID muy corto debe ser inválido"

    # 3. Parser de Venta Nueva
    msg_sale = """
    USUARIO : kevintj
    HWID    : 00d12f8f5e92c189d8005ddb60614cf9
    VALIDEZ : 18/09/2026
    """
    res_sale = parse_http_custom_message(msg_sale)
    assert res_sale is not None, "El parser no detectó el mensaje de venta nueva"
    assert res_sale["action"] == "new_sale"
    assert res_sale["username"] == "kevintj"
    assert res_sale["hwid"] == "00d12f8f5e92c189d8005ddb60614cf9"
    assert res_sale["expiry_date"] == "2026-09-18"

    # 4. Parser de Renovación con slot variable (17)
    msg_renew_17 = """
    ID/CLIENTE   : 17 / kevintj 
     📱 PERMITIDOS : HWID 
     VALIDO HASTA : 19/10/2026
     RENUEVA EN 31 DIAS, DISFRUTE SU ESTANCIA!.
    """
    res_renew = parse_http_custom_message(msg_renew_17)
    assert res_renew is not None, "El parser no detectó la renovación con slot 17"
    assert res_renew["action"] == "renewal"
    assert res_renew["username"] == "kevintj", f"Esperado 'kevintj', obtenido '{res_renew['username']}'"
    assert res_renew["expiry_date"] == "2026-10-19"

    # 5. Parser de Renovación con slot variable diferente (50)
    msg_renew_50 = """
    ID/CLIENTE   : 50 / juan_pro 
     📱 PERMITIDOS : HWID 
     VALIDO HASTA : 15/11/2026
     RENUEVA EN 30 DIAS, DISFRUTE SU ESTANCIA!.
    """
    res_renew_50 = parse_http_custom_message(msg_renew_50)
    assert res_renew_50 is not None
    assert res_renew_50["username"] == "juan_pro"
    assert res_renew_50["expiry_date"] == "2026-11-15"

    # 6. Descarte de mensajes cotidianos
    assert parse_http_custom_message("Hola, tenés stock de netflix?") is None
    assert parse_http_custom_message("Ya te transferí") is None

    # 7. Flujo End-to-End en Base de Datos (Alta y Renovación)
    async def test_db_flow():
        test_phone = "5491199887766"

        # Simular comprobante previo pendiente
        pen = database.create_pending_payment(
            sender_phone=test_phone,
            client_name="Cliente Test",
            amount=8000.0,
            bank="Mercado Pago"
        )
        assert pen["status"] == "pending"

        # Ejecutar alta automática de venta nueva
        out_sale = await process_http_custom_outgoing_message(test_phone, msg_sale, source="TestHarness")
        assert out_sale["status"] == "success"
        assert out_sale["action"] == "new_sale"
        assert out_sale["price"] == 8000.0  # Consumidor final por defecto

        # Comprobar que el comprobante pendiente fue auto-aprobado
        pen_updated = database.get_pending_payment(pen["id"])
        assert pen_updated["status"] == "approved", "El comprobante previo debía ser aprobado automáticamente"

        # Ejecutar renovación
        out_renew = await process_http_custom_outgoing_message(test_phone, msg_renew_17, source="TestHarness")
        assert out_renew["status"] == "success"
        assert out_renew["action"] == "renewal"
        assert out_renew["expiry_date"] == "2026-10-19"

    asyncio.run(test_db_flow())
    print("    ✅ HTTP Custom & HWID: 7/7 casos de prueba superados exitosamente.")

if __name__ == "__main__":
    run_tests()
