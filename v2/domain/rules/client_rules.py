import re
from typing import Optional

def classify_client_type(raw_type: Optional[str]) -> str:
    """Clasifica el tipo de cliente en uno de los 3 niveles del dominio:
    - 'revendedor_vip'
    - 'revendedor'
    - 'consumidor_final'
    """
    if not raw_type or not isinstance(raw_type, str):
        return "consumidor_final"
    t = raw_type.strip().lower()
    if "vip" in t:
        return "revendedor_vip"
    elif "revend" in t:
        return "revendedor"
    return "consumidor_final"

def preserve_client_type(existing_type: Optional[str], incoming_type: Optional[str]) -> str:
    """Preserva la categoría VIP o Revendedor existente a menos que se especifique un cambio deliberado."""
    e_type = classify_client_type(existing_type)
    in_raw = (incoming_type or "").strip().lower()

    # Si explícitamente se pide VIP o revendedor
    if "vip" in in_raw:
        return "revendedor_vip"
    elif "revend" in in_raw and "vip" not in e_type:
        return "revendedor"
    elif "final" in in_raw:
        return "consumidor_final"

    # Si no se pasó nada o vino el default, preservar el estatus previo de mayor privilegio
    if e_type in ("revendedor_vip", "revendedor") and not in_raw:
        return e_type
    if e_type == "revendedor_vip" and in_raw == "revendedor":
        return "revendedor"
    if e_type in ("revendedor_vip", "revendedor") and in_raw == "consumidor_final":
        return e_type

    return classify_client_type(incoming_type)

def get_client_type_label(client_type: Optional[str]) -> str:
    """Devuelve la etiqueta visual representativa del nivel del cliente."""
    c = classify_client_type(client_type)
    if c == "revendedor_vip":
        return "👑 Revendedor VIP"
    elif c == "revendedor":
        return "💼 Revendedor"
    return "👤 Consumidor Final"

def clean_phone_number(raw_phone: Optional[str]) -> str:
    """Normaliza un número de teléfono extrayendo únicamente dígitos."""
    if not raw_phone:
        return ""
    clean = re.sub(r"[^0-9]", "", str(raw_phone))
    return clean
