import asyncio
from domain.client_rules import classify_client_type, preserve_client_type, get_client_type_label, should_promote_to_vip
import database

def run_tests():
    print("  [Suite] Tarifas Comerciales y Jerarquía de Clientes...")

    # 1. Reglas puras de clasificación y promoción
    assert classify_client_type("consumidor_final") == "consumidor_final"
    assert classify_client_type("revendedor") == "revendedor"
    assert classify_client_type("revendedor_vip") == "revendedor_vip"
    assert classify_client_type("VIP") == "revendedor_vip"
    assert classify_client_type("revendedor mayorista") == "revendedor"
    assert classify_client_type(None) == "consumidor_final"

    # 1.1 Regla de promoción sugerida a VIP (>= 10 servicios)
    assert should_promote_to_vip(10, "revendedor") is True
    assert should_promote_to_vip(12, "consumidor_final") is True
    assert should_promote_to_vip(5, "revendedor") is False
    assert should_promote_to_vip(15, "revendedor_vip") is False, "Ya es VIP, no requiere promoción"

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

    # 6. Búsqueda inteligente de clientes (Normalización de teléfonos, queries combinadas y búsqueda inversa por email)
    marco = database.find_or_create_client(
        name="Marco Antonio",
        whatsapp="5491123586964",
        client_type="consumidor_final"
    )
    assert marco["id"] > 0

    acc_marco = database.assign_or_sell_account(
        client_name=marco["name"],
        platform="Netflix (Casa Extra)",
        email="therayonet+rayo44@gmail.com",
        password="colom78@bia1",
        expiry_date="2026-09-18",
        whatsapp=marco["whatsapp"]
    )
    assert acc_marco["id"] > 0

    # Búsqueda por número con prefijo '+' y espacios
    found_by_phone = database.search_client("+54 9 11 2358-6964")
    assert found_by_phone is not None, "Debe encontrar al cliente por teléfono con formato internacional"
    assert found_by_phone["name"] == "Marco Antonio"

    # Búsqueda combinada (teléfono + nombre en la misma consulta)
    found_by_combined = database.search_client("+54 9 11 2358-6964 Marco Antonio")
    assert found_by_combined is not None, "Debe encontrar al cliente cuando el prompt mezcla teléfono y nombre"
    assert found_by_combined["name"] == "Marco Antonio"

    # Búsqueda inversa por correo electrónico de la cuenta contratada
    found_by_email = database.search_client("therayonet+rayo44@gmail.com")
    assert found_by_email is not None, "Debe encontrar al cliente mediante el correo de su cuenta activa"
    assert found_by_email["name"] == "Marco Antonio"

    print("    ✅ Tarifas Comerciales y Clientes: 9/9 casos de prueba superados exitosamente.")

if __name__ == "__main__":
    run_tests()

