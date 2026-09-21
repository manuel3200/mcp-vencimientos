import re
from datetime import date, datetime
from typing import Optional, Dict, Any

def parse_date_to_iso(date_str: Optional[str]) -> Optional[str]:
    """Convierte una fecha en formato DD/MM/YYYY, DD-MM-YYYY o DD.MM.YYYY a ISO YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return None
    clean = date_str.strip()
    m = re.match(r"^(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{2,4})$", clean)
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
    """Verifica si un candidato a HWID cumple con el formato técnico (alfanumérico/hexadecimal 8-64 caracteres), tolerando espacios accidentales."""
    if not candidate or not isinstance(candidate, str):
        return False
    c = re.sub(r'\s+', '', candidate.strip())
    if c.lower() == "hwid":
        return False
    return bool(re.match(r"^[a-fA-F0-9_\-]{8,64}$", c))

def parse_http_custom_message(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Analiza un mensaje enviado al cliente y determina si es un alta o renovación de HTTP Custom.
    
    1) Venta Nueva:
       USUARIO : kevintj
       HWID    : 00d12f8f5e92c189d8005ddb60614cf9
       VALIDEZ : 18/09/2026 (o 18.09.2026)

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
    m_renov_hasta = re.search(r'VALIDO\s+HASTA\s*:\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})', raw, re.IGNORECASE)
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
    m_hwid = re.search(r'HWID\s*:\s*([^\r\n]+)', raw, re.IGNORECASE)
    m_validez = re.search(r'(?:VALIDEZ|VALIDO|VENCE|VENCIMIENTO)\s*:\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})', raw, re.IGNORECASE)

    if m_user and m_hwid and m_validez:
        raw_hwid = m_hwid.group(1).strip()
        cleaned_hwid = re.sub(r'\s+', '', raw_hwid)
        if is_valid_hwid(cleaned_hwid):
            expiry_iso = parse_date_to_iso(m_validez.group(1))
            if expiry_iso:
                return {
                    "action": "new_sale",
                    "username": m_user.group(1).strip(),
                    "hwid": cleaned_hwid,
                    "expiry_date": expiry_iso,
                    "raw_date": m_validez.group(1)
                }

    return None


class HWIDLeaseManager:
    """Administra leases de sesión HWID para prevenir clonaciones y accesos simultáneos (HIGH-03)."""
    LEASE_TIMEOUT_SECONDS = 300  # 5 minutos de ventana activa

    def __init__(self):
        self._leases: Dict[str, Dict[str, Any]] = {}

    def request_access(self, config_id: str, hwid: str) -> Dict[str, Any]:
        """Verifica si el HWID puede acceder o si existe una sesión activa con otro HWID."""
        now = datetime.utcnow()
        clean_cid = str(config_id).strip()
        clean_h = str(hwid).strip()

        current_lease = self._leases.get(clean_cid)

        if current_lease and current_lease["hwid"] != clean_h:
            last_seen = current_lease["last_activity"]
            elapsed = (now - last_seen).total_seconds()
            if elapsed < self.LEASE_TIMEOUT_SECONDS:
                # Sesión activa de otro dispositivo dentro del timeout
                return {
                    "granted": False,
                    "reason": "active_session_exists",
                    "active_hwid": current_lease["hwid"],
                    "remaining_seconds": int(self.LEASE_TIMEOUT_SECONDS - elapsed)
                }

        # Conceder o renovar lease
        self._leases[clean_cid] = {"hwid": clean_h, "last_activity": now}
        return {"granted": True, "reason": "lease_granted"}

    def release_lease(self, config_id: str, hwid: Optional[str] = None) -> bool:
        clean_cid = str(config_id).strip()
        if clean_cid in self._leases:
            if hwid is None or self._leases[clean_cid]["hwid"] == str(hwid).strip():
                del self._leases[clean_cid]
                return True
        return False

    def get_lease(self, config_id: str) -> Optional[Dict[str, Any]]:
        return self._leases.get(str(config_id).strip())


hwid_lease_manager = HWIDLeaseManager()

