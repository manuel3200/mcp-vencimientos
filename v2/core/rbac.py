"""
rbac.py - Control de Acceso Basado en Roles (RBAC) para StreamVault v2
Define jerarquías de privilegios (SUPER_ADMIN, FINANZAS, SOPORTE) y valida
autorizaciones granulares para comandos de WhatsApp, Telegram y Webhooks.
"""

import os
import re
from typing import Dict, List, Optional, Tuple, Set
from core.config import settings
from core.utils import clean_whatsapp_phone

# Roles disponibles
ROLE_SUPER_ADMIN = "SUPER_ADMIN"
ROLE_FINANZAS = "FINANZAS"
ROLE_SOPORTE = "SOPORTE"
ROLE_UNAUTHORIZED = "UNAUTHORIZED"

# Acciones permitidas por rol
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_SUPER_ADMIN: {"*"},
    ROLE_FINANZAS: {
        "pagoapro", "aprobarpago", "pagodene", "rechazarpago",
        "pagoparcial", "parcial", "revertir_pago", "revertirpago", "anularpago",
        "balance", "auditoria", "consultar_pago"
    },
    ROLE_SOPORTE: {
        "caida", "reemplazo", "reemplazar", "cambiar", "esperar", "espera",
        "autorizar", "posponer", "auditoria", "consultar_cuenta"
    }
}


def normalize_command_action(cmd_text: str) -> str:
    """Extrae el nombre base del comando para verificar permisos.
    
    Soporta:
    - /pagoapro_12 -> pagoapro
    - /pagoapro_12_all -> pagoapro
    - /revertir_pago_15 -> revertir_pago
    - /deshacer_cambio_3 -> deshacer_cambio
    - /caida netflix@gmail.com -> caida
    - /esperar_4 -> esperar
    - /posponer_2 -> posponer
    """
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
    """Parsea la lista blanca de administradores y sus roles asignados.
    
    Formato admitido en ADMIN_PHONES (variable de entorno o settings):
    '+5491166099952:SUPER_ADMIN, 5491122334455:FINANZAS, +5491133445566:SOPORTE'
    Si un número no tiene rol explícito, se asigna 'SUPER_ADMIN' por defecto.
    """
    admin_dict: Dict[str, str] = {}

    # 1. Teléfono principal de settings
    main_admin = (getattr(settings, "ADMIN_WHATSAPP", "") or os.getenv("ADMIN_WHATSAPP", "")).strip()
    if main_admin:
        cleaned = clean_whatsapp_phone(main_admin)
        if cleaned:
            admin_dict[cleaned] = ROLE_SUPER_ADMIN

    # 2. Lista extendida ADMIN_PHONES
    extended = (getattr(settings, "ADMIN_PHONES", "") or os.getenv("ADMIN_PHONES", "")).strip()
    if extended:
        entries = [e.strip() for e in extended.split(",") if e.strip()]
        for entry in entries:
            if ":" in entry:
                phone_part, role_part = entry.split(":", 1)
                phone_clean = clean_whatsapp_phone(phone_part.strip())
                role_upper = role_part.strip().upper()
                if role_upper not in (ROLE_SUPER_ADMIN, ROLE_FINANZAS, ROLE_SOPORTE):
                    role_upper = ROLE_SUPER_ADMIN
                if phone_clean:
                    admin_dict[phone_clean] = role_upper
            else:
                phone_clean = clean_whatsapp_phone(entry)
                if phone_clean:
                    admin_dict[phone_clean] = ROLE_SUPER_ADMIN

    return admin_dict


def get_actor_role(phone_or_id: str, is_from_me: bool = False) -> str:
    """Obtiene el rol correspondiente al remitente.
    
    - is_from_me=True (mensajes emitidos por el propio bot/cuenta) -> SUPER_ADMIN.
    - phone_or_id verificado en la lista de administradores -> rol asignado.
    - Remitente no autorizado -> UNAUTHORIZED.
    """
    if is_from_me:
        return ROLE_SUPER_ADMIN

    clean_target = clean_whatsapp_phone(phone_or_id) if phone_or_id else ""
    if not clean_target:
        return ROLE_UNAUTHORIZED

    admin_map = _parse_admin_phone_roles()
    
    # Coincidencia exacta
    if clean_target in admin_map:
        return admin_map[clean_target]

    # Coincidencia por sufijo (últimos 10 dígitos) para contemplar variaciones de código de país
    if len(clean_target) >= 10:
        target_suffix = clean_target[-10:]
        for adm_phone, role in admin_map.items():
            if len(adm_phone) >= 10 and adm_phone[-10:] == target_suffix:
                return role

    return ROLE_UNAUTHORIZED


def has_permission(role: str, action: str) -> bool:
    """Evalúa si un rol posee autorización para ejecutar una acción dada."""
    if role == ROLE_SUPER_ADMIN:
        return True
    allowed_actions = ROLE_PERMISSIONS.get(role, set())
    norm_action = normalize_command_action(action)
    return norm_action in allowed_actions or "*" in allowed_actions


def check_admin_permission(phone_or_id: str, command_text: str, is_from_me: bool = False) -> Tuple[bool, str, str]:
    """Valida si el remitente tiene permisos para ejecutar el comando solicitado.
    
    Retorna:
    (allowed: bool, role: str, reason: str)
    """
    role = get_actor_role(phone_or_id, is_from_me=is_from_me)
    if role == ROLE_UNAUTHORIZED:
        return False, ROLE_UNAUTHORIZED, f"Remitente '{phone_or_id}' no está registrado como administrador."

    action = normalize_command_action(command_text)
    if not has_permission(role, action):
        return False, role, f"El rol '{role}' no tiene autorización para ejecutar '{action}'."

    return True, role, "Permiso concedido."
