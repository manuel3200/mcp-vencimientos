"""
mcp_guard.py - Anti-Prompt-Injection Guard y Scoping de Permisos para Tools MCP
Impide que clientes por chat o atacantes manipulen al agente IA para ejecutar tools de admin
o consultar/modificar datos de otros clientes (IDOR / cross-client attack prevention).
"""

import os
import re
import logging
from typing import Dict, Any, List, Optional

from core.config import settings

logger = logging.getLogger("core.mcp_guard")

# Tools que modifican la infraestructura o requieren privilegios de Administrador estricto
ADMIN_ONLY_TOOLS = {
    "crear_cuenta_con_pantallas",
    "reemplazar_cuenta_caida",
    "marcar_cuenta_caida",
    "eliminar_cuenta",
    "configurar_precio_catalogo",
    "crear_o_actualizar_combo",
    "eliminar_combo",
    "configurar_datos_pago",
    "aprobar_comprobante_pago",
    "rechazar_comprobante_pago",
    "verificar_variacion_costos_proveedor",
    "controlar_grupo",
    "banear_silencioso",
    "desbanear_silencioso",
    "moderar_participante_grupo",
    "configurar_modo_bot",
    "crear_cupon_descuento",
    "eliminar_todas_las_cuentas_excepto_cliente",
    "purgar_comprobantes_antiguos_base64",
    "despachar_comunicado_programado",
    "lanzar_encuesta_comunidad",
}

# Tools con alcance de cliente (deben operar únicamente sobre el teléfono del solicitante)
CLIENT_SCOPED_TOOLS = {
    "consultar_ficha_cliente",
    "generar_cobro_consolidado_whatsapp",
    "generar_mensaje_whatsapp",
    "vender_perfil_compartido",
    "vender_cuenta_completa",
    "vender_o_asignar_servicio",
    "vender_combo",
    "consultar_cupon",
    "canjear_saldo_referidos",
}


def clean_phone_digits(val: Any) -> str:
    """Extrae solo dígitos de una cadena o JID."""
    if not val:
        return ""
    clean = str(val).split("@")[0]
    return re.sub(r'[^0-9]', '', clean)


def validate_tool_execution(
    tool_name: str,
    params: Optional[Dict[str, Any]] = None,
    requester_phone_or_jid: str = "",
    is_admin: bool = False
) -> Dict[str, Any]:
    """Valida la autorización de ejecución de una tool FastMCP.
    
    Previene inyecciones de prompt y ejecuciones cruzadas entre clientes.
    """
    params = params or {}
    clean_req = clean_phone_digits(requester_phone_or_jid)
    admin_phone = clean_phone_digits(getattr(settings, "ADMIN_WHATSAPP", "") or os.getenv("ADMIN_WHATSAPP", ""))

    # 1. Si es admin autenticado por sesión o teléfono admin reconocido
    if is_admin or (admin_phone and clean_req == admin_phone):
        return {"allowed": True, "reason": "authorized_admin"}

    # 2. Bloquear tools exclusivas de administrador si no es admin
    if tool_name in ADMIN_ONLY_TOOLS:
        try:
            from core.audit import log_audit_event
            log_audit_event(
                actor=requester_phone_or_jid or "anonymous_client",
                action="SECURITY_PROMPT_INJECTION_BLOCKED",
                target_type="mcp_tool",
                target_id=tool_name,
                old_value="",
                new_value=f"Blocked admin-only execution attempt on {tool_name}",
                ip_or_source="mcp_guard"
            )
        except Exception as e:
            logger.warning(f"Error registrando auditoría en mcp_guard: {e}")

        logger.warning(f"🚨 Intento de ejecución no autorizada / Prompt Injection bloqueado en '{tool_name}' desde '{requester_phone_or_jid}'")
        return {
            "allowed": False,
            "error": f"Acción restringida: la herramienta '{tool_name}' requiere privilegios de administrador."
        }

    # 3. Validar alcance de cliente (IDOR prevention)
    if tool_name in CLIENT_SCOPED_TOOLS:
        param_phone = clean_phone_digits(
            params.get("telefono") or
            params.get("telefono_o_query") or
            params.get("client_phone") or
            params.get("phone") or
            ""
        )
        if clean_req and param_phone and clean_req != param_phone:
            try:
                from core.audit import log_audit_event
                log_audit_event(
                    actor=requester_phone_or_jid,
                    action="SECURITY_CROSS_CLIENT_ATTEMPT_BLOCKED",
                    target_type="mcp_tool",
                    target_id=tool_name,
                    old_value="",
                    new_value=f"Requester {clean_req} attempted to access client {param_phone}",
                    ip_or_source="mcp_guard"
                )
            except Exception as e:
                logger.warning(f"Error registrando auditoría en mcp_guard: {e}")

            logger.warning(f"🚨 Intento de acceso cross-client bloqueado: {clean_req} intentó acceder a datos de {param_phone}")
            return {
                "allowed": False,
                "error": "Acceso denegado: solo puedes consultar o gestionar tus propios servicios."
            }

    return {"allowed": True, "reason": "client_scoped_ok"}
