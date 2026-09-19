import re
from datetime import date
from typing import Optional, Dict, Any

def parse_date_to_iso(date_str: Optional[str]) -> Optional[str]:
    """Convierte una fecha en formato DD/MM/YYYY o DD-MM-YYYY a ISO YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return None
    clean = date_str.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", clean)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        d = date(year, month, day)
        return d.isoformat()
    except ValueError:
        return None

def is_valid_hwid(candidate: Optional[str]) -> bool:
    """Verifica si un candidato a HWID cumple con el formato técnico (alfanumérico/hexadecimal 8-64 caracteres)."""
    if not candidate or not isinstance(candidate, str):
        return False
    c = candidate.strip()
    if c.lower() == "hwid":
        return False
    return bool(re.match(r"^[a-fA-F0-9_\-]{8,64}$", c))

def parse_http_custom_message(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Analiza un mensaje enviado al cliente y determina si es un alta o renovación de HTTP Custom.
    
    1) Venta Nueva:
       USUARIO : kevintj
       HWID    : 00d12f8f5e92c189d8005ddb60614cf9
       VALIDEZ : 18/09/2026

    2) Renovación:
       ID/CLIENTE   : 17 / kevintj 
        📱 PERMITIDOS : HWID 
        VALIDO HASTA : 19/10/2026
        RENUEVA EN 31 DIAS, DISFRUTE SU ESTANCIA!.
    """
    if not text or not isinstance(text, str):
        return None

    raw = text.strip()

    # 1. Evaluación de Renovación (ID/CLIENTE + VALIDO HASTA)
    m_renov_hasta = re.search(r'VALIDO\s+HASTA\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', raw, re.IGNORECASE)
    if m_renov_hasta:
        expiry_iso = parse_date_to_iso(m_renov_hasta.group(1))
        # Extraer usuario de ID/CLIENTE : 17 / kevintj tolerando cualquier índice numérico
        m_renov_user = re.search(r'(?:ID\s*/\s*CLIENTE|CLIENTE)\s*:\s*(?:(?:\d+)\s*[/|-]\s*)?([^\r\n]+)', raw, re.IGNORECASE)
        username = ""
        if m_renov_user:
            raw_u = m_renov_user.group(1).strip()
            username = raw_u.split("/")[-1].strip() if "/" in raw_u else raw_u

        upper_text = raw.upper()
        is_http_custom = (
            "HWID" in upper_text or
            "PERMITIDOS" in upper_text or
            "RENUEVA EN" in upper_text or
            "DISFRUTE SU ESTANCIA" in upper_text or
            "ID/CLIENTE" in upper_text
        )

        if is_http_custom and expiry_iso:
            return {
                "action": "renewal",
                "username": username,
                "expiry_date": expiry_iso,
                "raw_date": m_renov_hasta.group(1)
            }

    # 2. Evaluación de Venta Nueva (USUARIO + HWID + VALIDEZ)
    m_user = re.search(r'(?:USUARIO|USER)\s*:\s*([^\r\n]+)', raw, re.IGNORECASE)
    m_hwid = re.search(r'HWID\s*:\s*([a-zA-Z0-9_\-]{8,64})', raw, re.IGNORECASE)
    m_validez = re.search(r'(?:VALIDEZ|VALIDO|VENCE|VENCIMIENTO)\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', raw, re.IGNORECASE)

    if m_user and m_hwid and m_validez:
        hwid_candidate = m_hwid.group(1).strip()
        if is_valid_hwid(hwid_candidate):
            expiry_iso = parse_date_to_iso(m_validez.group(1))
            if expiry_iso:
                return {
                    "action": "new_sale",
                    "username": m_user.group(1).strip(),
                    "hwid": hwid_candidate,
                    "expiry_date": expiry_iso,
                    "raw_date": m_validez.group(1)
                }

    return None
