import re
from datetime import datetime, date, timedelta
from typing import Union, Optional

def parse_money(val: Union[str, float, int, None]) -> float:
    """Extrae el valor numérico flotante soportando Pesos Argentinos (ARS) y formatos internacionales.
    Maneja: '15000', '$ 15.000', '$ 4.500,50', '4500.50', '$3,500', etc.
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0

    # Remover letras y caracteres que no sean dígitos, comas o puntos
    s = re.sub(r'[^\d\.,]', '', s)
    if not s:
        return 0.0

    # Caso 1: Tiene tanto punto como coma, ej: "15.000,50" o "15,000.50"
    if '.' in s and ',' in s:
        if s.rfind(',') > s.rfind('.'):
            # Notación argentina / hispana (punto de miles, coma decimal: 15.000,50)
            s = s.replace('.', '').replace(',', '.')
        else:
            # Notación anglosajona (coma de miles, punto decimal: 15,000.50)
            s = s.replace(',', '')
    # Caso 2: Solo tiene coma
    elif ',' in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # Decimal: 4500,50 -> 4500.50
            s = s.replace(',', '.')
        else:
            # Miles: 15,000 -> 15000
            s = s.replace(',', '')
    # Caso 3: Solo tiene punto
    elif '.' in s:
        parts = s.split('.')
        if len(parts) > 2:
            # Varios puntos (ej: 1.500.000) -> miles
            s = s.replace('.', '')
        elif len(parts) == 2:
            if len(parts[1]) == 3:
                # E.g. "15.000" o "4.500" en Argentina son miles
                s = s.replace('.', '')
            else:
                # E.g. "4500.50" o "12.5" es decimal
                pass

    try:
        return float(s)
    except ValueError:
        return 0.0

def format_ars(val: Union[float, int, str, None], include_symbol: bool = True) -> str:
    """Formatea un monto en Pesos Argentinos con separador de miles con punto.
    Ejemplos:
    - 15000 -> "$ 15.000 ARS"
    - 4500.50 -> "$ 4.500,50 ARS"
    """
    num = parse_money(val)
    if num.is_integer():
        formatted = f"{int(num):,}".replace(",", ".")
    else:
        int_part = int(num)
        cents = int(round((num - int_part) * 100))
        formatted = f"{int_part:,}".replace(",", ".") + f",{cents:02d}"

    if include_symbol:
        return f"${formatted} ARS"
    return formatted

def clean_whatsapp_phone(phone: Optional[str]) -> str:
    """Limpia y estandariza un número telefónico para el protocolo wa.me."""
    if not phone:
        return ""
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return ""
    # Manejo de números de Argentina si vienen en formato local (10 dígitos)
    if len(digits) == 10:
        if digits.startswith("15"):
            digits = "11" + digits[2:]
        digits = "549" + digits
    elif len(digits) == 12 and digits.startswith("54") and not digits.startswith("549"):
        digits = "549" + digits[2:]
    return digits

def _parse_date_flexible(val: str) -> str:
    """Convierte formatos como DD/MM/YYYY, DD-MM-YYYY a YYYY-MM-DD."""
    clean = val.strip() if val else ""
    if not clean:
        return (date.today() + timedelta(days=30)).isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(clean, fmt).date().isoformat()
        except ValueError:
            pass
    return (date.today() + timedelta(days=30)).isoformat()
