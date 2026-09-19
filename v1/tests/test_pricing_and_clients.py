import asyncio
from domain.client_rules import classify_client_type, preserve_client_type, get_client_type_label
import database

def run_tests():
    print("  [Suite] Tarifas Comerciales y Jerarquía de Clientes...")

    # 1. Reglas puras de clasificación
    assert classify_client_type("consumidor_final") == "consumidor_final"
    assert classify_client_type("revendedor") == "revendedor"
    assert classify_client_type("revendedor_vip") == "revendedor_vip"
    assert classify_client_type("VIP") == "revendedor_vip"
    assert classify_client_type("revendedor mayorista") == "revendedor"
    assert classify_client_type(None) == "consumidor_final"

    # 2. Preservación de jerarquías (no degradar VIP a final por omisión)
    assert preserve_client_type("revendedor_vip", "") == "revendedor_vip"
    assert preserve_client_type("revendedor_vip", "consumidor_final") == "revendedor_vip"
    assert preserve_client_type("revendedor", "") == "revendedor"
    assert preserve_client_type("consumidor_final", "revendedor") == "revendedor"

    # 3. Etiquetas visuales
    assert get_client_type_label("revendedor_vip") == "👑 Revendedor VIP"
    assert get_client_type_label("revendedor") == "💼 Revendedor"
    assert get_client_type_label("consumidor_final") == "👤 Consumidor Final"

    # 4. Catálogo de Precios Dinámico para HTTP Custom
    p_final, _ = database.get_suggested_price("HTTP Custom", "hwid", "consumidor_final")
    p_reseller, _ = database.get_suggested_price("HTTP Custom", "hwid", "revendedor")
    p_vip, _ = database.get_suggested_price("HTTP Custom", "hwid", "revendedor_vip")

    assert p_final == 8000.0, f"Tarifa Final esperada 8000.0, obtenida {p_final}"
    assert p_reseller == 4500.0, f"Tarifa Revendedor esperada 4500.0, obtenida {p_reseller}"
    assert p_vip == 3500.0, f"Tarifa VIP esperada 3500.0, obtenida {p_vip}"

    # 5. Prueba de asignación real en DB
    client_vip = database.find_or_create_client(
        name="Revendedor VIP Test",
        whatsapp="5491111223344",
        client_type="revendedor_vip"
    )
    assert client_vip["client_type"] == "revendedor_vip"

    acc_vip = database.assign_or_sell_account(
        client_name=client_vip["name"],
        platform="HTTP Custom",
        email="vip_user",
        password="00d12f8f5e92c189d8005ddb60614cf9",
        expiry_date="2026-10-30",
        whatsapp=client_vip["whatsapp"],
        client_type=client_vip["client_type"]
    )
    # Debe haber aplicado la tarifa VIP $3500
    from core.utils import parse_money
    assert parse_money(acc_vip["price"]) == 3500.0, f"Esperado 3500.0, asignado: {acc_vip['price']}"

    print("    ✅ Tarifas Comerciales: 5/5 casos de prueba superados exitosamente.")

if __name__ == "__main__":
    run_tests()
