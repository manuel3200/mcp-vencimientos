"""
rbac.py - Control de Acceso Basado en Roles (RBAC) para StreamVault v2
Define jerarquías de privilegios (SUPER_ADMIN, FINANZAS, SOPORTE) y valida
autorizaciones granulares para comandos de WhatsApp, Telegram y Webhooks.
"""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple, Set
from core.config import settings
from core.utils import clean_whatsapp_phone

logger = logging.getLogger("core.rbac")

# Roles disponibles
ROLE_SUPER_ADMIN = "SUPER_ADMIN"
ROLE_FINANZAS = "FINANZAS"
ROLE_SOPORTE = "SOPORTE"
ROLE_UNAUTHORIZED = "UNAUTHORIZED"

VALID_ROLES: Set[str] = {ROLE_SUPER_ADMIN, ROLE_FINANZAS, ROLE_SOPORTE}

# Acciones permitidas por rol
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_SUPER_ADMIN: {"*"},
    ROLE_FINANZAS: {
        "pagoapro", "aprobarpago", "pagodene", "rechazarpago",
        "pagoparcial", "parcial", "revertir_pago", "revertirpago", "anularpago",
        "balance", "auditoria", "consultar_pago", "cobros", "datos_pago", "cbu", "alias"
    },
    ROLE_SOPORTE: {
        "caida", "reemplazo", "reemplazar", "cambiar", "esperar", "espera",
        "autorizar", "posponer", "auditoria", "consultar_cuenta", "stock",
        "inventario", "cliente", "ficha", "buscar", "pantallas"
    }
}


def validate_configured_role(role: str) -> str:
    """Valida que un rol configurado pertenezca estrictamente a VALID_ROLES (V09).
    Nunca eleva roles desconocidos o con typos a SUPER_ADMIN.
    """
    canonical = (role or "").strip().upper()
    if canonical not in VALID_ROLES:
        raise ValueError(f"Rol administrativo inválido: '{role}'")
    return canonical


def canonicalize_whatsapp_phone(raw_phone: str) -> str:
    """Normaliza un número de teléfono a formato canónico completo con código de país (V09).
    Rechaza identificadores incompletos (<10 dígitos) y unifica la variante móvil de Argentina (54 + 10d -> 549 + 10d)
    sin permitir coincidencias por sufijo entre países distintos.
    """
    cleaned = clean_whatsapp_phone(raw_phone) if raw_phone else ""
    if not cleaned or len(cleaned) < 10:
        return ""
    if len(cleaned) == 12 and cleaned.startswith("54") and not cleaned.startswith("549"):
        return "549" + cleaned[2:]
    return cleaned


def normalize_command_action(cmd_text: str) -> str:
    """Extrae el nombre base del comando para verificar permisos."""
    text_clean = cmd_text.strip().lower()
    if text_clean.startswith("/"):
        text_clean = text_clean[1:]
    token = text_clean.split(None, 1)[0] if text_clean else ""
    if token.endswith("_all"):
        token = token[:-4]
    token = re.sub(r'_\d+$', '', token)
    token = re.sub(r'\d+$', '', token)
    return token


def _parse_admin_phone_roles() -> Dict[str, str]:
    """Parsea la lista blanca de administradores y sus roles asignados (V09).

    Formato exigido en ADMIN_PHONES:
    '+5491166099952:SUPER_ADMIN, 5491122334455:FINANZAS, +5491133445566:SOPORTE'
    Entradas sin ':ROL' o con un rol desconocido son rechazadas (nunca elevadas a SUPER_ADMIN).
    """
    admin_dict: Dict[str, str] = {}

    # 1. Teléfono principal del propietario en ADMIN_WHATSAPP
    main_admin = (getattr(settings, "ADMIN_WHATSAPP", "") or os.getenv("ADMIN_WHATSAPP", "")).strip()
    if main_admin:
        cleaned = canonicalize_whatsapp_phone(main_admin)
        if cleaned:
            admin_dict[cleaned] = ROLE_SUPER_ADMIN

    # 2. Lista extendida ADMIN_PHONES (requiere formato explicito NUMERO:ROL_VALIDO)
    extended = (getattr(settings, "ADMIN_PHONES", "") or os.getenv("ADMIN_PHONES", "")).strip()
    if extended:
        entries = [e.strip() for e in extended.split(",") if e.strip()]
        for entry in entries:
            if ":" not in entry:
                logger.warning(f"RBAC [V09]: Entrada ignorada en ADMIN_PHONES por carecer de ':ROL' explícito.")
                continue
            phone_part, role_part = entry.split(":", 1)
            phone_clean = canonicalize_whatsapp_phone(phone_part.strip())
            if not phone_clean:
                logger.warning("RBAC [V09]: Número inválido o incompleto en ADMIN_PHONES (<10 dígitos).")
                continue
            try:
                role_valid = validate_configured_role(role_part)
            except ValueError:
                logger.warning(f"RBAC [V09]: Rol desconocido '{role_part.strip()}' rechazado en ADMIN_PHONES.")
                continue
            admin_dict[phone_clean] = role_valid

    return admin_dict


def get_actor_role(
    phone_or_id: str,
    is_from_me: bool = False,
    verified_instance: bool = True,
) -> str:
    """Obtiene el rol correspondiente al remitente (V09).

    - is_from_me=True solo concede SUPER_ADMIN cuando verified_instance=True.
    - Compara números en formato canónico completo con código de país (sin sufijos de 10 dígitos).
    - Remitente no autorizado -> UNAUTHORIZED.
    """
    if is_from_me and verified_instance:
        return ROLE_SUPER_ADMIN

    clean_target = canonicalize_whatsapp_phone(phone_or_id) if phone_or_id else ""
    if not clean_target:
        return ROLE_UNAUTHORIZED

    admin_map = _parse_admin_phone_roles()
    return admin_map.get(clean_target, ROLE_UNAUTHORIZED)


def has_permission(role: str, action: str) -> bool:
    """Evalúa si un rol posee autorización para ejecutar una acción dada."""
    if role == ROLE_SUPER_ADMIN:
        return True
    allowed_actions = ROLE_PERMISSIONS.get(role, set())
    norm_action = normalize_command_action(action)
    return norm_action in allowed_actions or "*" in allowed_actions


def check_admin_permission(
    phone_or_id: str,
    command_text: str,
    is_from_me: bool = False,
    verified_instance: bool = True,
) -> Tuple[bool, str, str]:
    """Valida si el remitente tiene permisos para ejecutar el comando solicitado."""
    role = get_actor_role(phone_or_id, is_from_me=is_from_me, verified_instance=verified_instance)
    if role == ROLE_UNAUTHORIZED:
        return False, ROLE_UNAUTHORIZED, f"Remitente '{phone_or_id}' no está registrado como administrador."

    action = normalize_command_action(command_text)
    if not has_permission(role, action):
        return False, role, f"El rol '{role}' no tiene autorización para ejecutar '{action}'."

    return True, role, "Permiso concedido."

