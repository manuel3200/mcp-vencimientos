from typing import Tuple, Optional, Dict, Any
from domain.rules.client_rules import classify_client_type

HTTP_CUSTOM_DEFAULT_PRICES = {
    "consumidor_final": 8000.0,
    "revendedor": 4500.0,
    "revendedor_vip": 3500.0
}

def resolve_http_custom_price(client_type: Optional[str], catalog_item: Optional[Dict[str, Any]] = None) -> Tuple[float, float, float]:
    """Calcula (precio_venta, costo, ganancia) para un servicio HTTP Custom según el tipo de cliente.
    Prioriza el catálogo si existe; de lo contrario utiliza los valores comerciales base.
    """
    c_type = classify_client_type(client_type)
    cost = 0.0

    if catalog_item:
        cost = float(catalog_item.get("cost_price") or 0.0)
        p_vip = float(catalog_item.get("price_reseller_vip") or 0.0)
        p_res = float(catalog_item.get("price_reseller") or 0.0)
        p_fin = float(catalog_item.get("price_final") or 0.0)

        if c_type == "revendedor_vip" and p_vip > 0:
            price = p_vip
        elif c_type == "revendedor" and p_res > 0:
            price = p_res
        elif p_fin > 0:
            price = p_fin
        else:
            price = HTTP_CUSTOM_DEFAULT_PRICES.get(c_type, 8000.0)
    else:
        price = HTTP_CUSTOM_DEFAULT_PRICES.get(c_type, 8000.0)

    profit = price - cost
    return price, cost, profit
