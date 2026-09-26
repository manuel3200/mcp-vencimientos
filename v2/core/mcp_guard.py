"""
mcp_guard.py - Autorización Real en el Despachador FastMCP y Prevención de IDOR (V08)
Impide que clientes o atacantes ejecuten herramientas administrativas o consulten/modifiquen
recursos de otros clientes tanto en llamadas directas como en el transporte MCP real.
"""

import os
import re
import inspect
import logging
import functools
from typing import Dict, Any, Optional, Callable

from core.config import settings
from core.principal import Principal, get_current_mcp_principal
from db.connection import get_connection

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
    "agregar_stock_libre",
    "configurar_umbral_stock",
    "enviar_alerta_stock_telegram",
    "renovar_cuenta_madre",
    "registrar_cliente",
    "registrar_cobro_cliente",
    "enviar_backup_telegram",
    "exportar_resumen_csv",
    "importar_stock_desde_csv",
    "importar_ventas_desde_csv",
    "crear_o_actualizar_proveedor",
    "cambiar_clave_admin",
    "enviar_tagall_grupo",
    "configurar_grupo_whatsapp",
    "sincronizar_grupos_whatsapp",
    "agregar_grupo_whatsapp",
}

# Tools con alcance de cliente (deben operar únicamente sobre recursos propios del cliente autenticado)
CLIENT_SCOPED_TOOLS = {
    "consultar_ficha_cliente",
    "generar_cobro_consolidado_whatsapp",
    "generar_mensaje_whatsapp",
    "consultar_datos_pago",
    "consultar_catalogo_precios",
    "listar_combos",
    "consultar_cupon",
    "canjear_saldo_referidos",
}

# Acciones destructivas que requieren confirmación/auditoría reforzada
DESTRUCTIVE_TOOLS = {
    "eliminar_cuenta",
    "eliminar_todas_las_cuentas_excepto_cliente",
    "purgar_comprobantes_antiguos_base64",
    "cambiar_clave_admin",
}


def clean_phone_digits(val: Any) -> str:
    """Extrae solo dígitos de una cadena o JID."""
    if not val:
        return ""
    clean = str(val).split("@")[0]
    return re.sub(r"[^0-9]", "", clean)


def _verify_resource_ownership_in_db(clean_requester_phone: str, params: Dict[str, Any]) -> bool:
    """Verifica en la base de datos que cualquier client_id, account_id o búsqueda pertenezca al teléfono autenticado (V08)."""
    if not clean_requester_phone:
        return False

    # 1. Revisar parámetros de teléfono/query directos
    for key in ("telefono", "telefono_o_query", "client_phone", "phone", "cliente", "query"):
        val = params.get(key)
        if val is not None and str(val).strip():
            digits = clean_phone_digits(val)
            if digits and digits != clean_requester_phone:
                return False
            # Si no son solo dígitos (ej. nombre o código de cliente), comprobar en DB que corresponda al mismo teléfono
            if not digits:
                conn = get_connection()
                try:
                    row = conn.execute("""
                        SELECT whatsapp FROM clients
                        WHERE lower(name) LIKE ? OR lower(client_code) = ?
                    """, (f"%{str(val).strip().lower()}%", str(val).strip().lower())).fetchall()
                    if not row:
                        return False
                    for r in row:
                        if clean_phone_digits(r["whatsapp"]) != clean_requester_phone:
                            return False
                finally:
                    conn.close()

    # 2. Revisar client_id si está presente
    client_id = params.get("client_id")
    if client_id is not None:
        conn = get_connection()
        try:
            row = conn.execute("SELECT whatsapp FROM clients WHERE id = ?", (client_id,)).fetchone()
            if not row or clean_phone_digits(row["whatsapp"]) != clean_requester_phone:
                return False
        finally:
            conn.close()

    # 3. Revisar account_id / cuenta_id si está presente
    acc_id = params.get("account_id") or params.get("cuenta_id") or params.get("id_cuenta")
    if acc_id is not None:
        conn = get_connection()
        try:
            row = conn.execute("""
                SELECT c.whatsapp
                FROM streaming_accounts sa
                LEFT JOIN clients c ON sa.client_id = c.id
                WHERE sa.id = ?
            """, (acc_id,)).fetchone()
            if not row or clean_phone_digits(row["whatsapp"]) != clean_requester_phone:
                return False
        finally:
            conn.close()

    return True


def validate_tool_execution(
    tool_name: str,
    params: Optional[Dict[str, Any]] = None,
    requester_phone_or_jid: str = "",
    is_admin: bool = False,
    principal: Optional[Principal] = None,
) -> Dict[str, Any]:
    """Valida la autorización de ejecución de una tool FastMCP (V08).
    Deniega por defecto herramientas sin política para clientes y verifica propiedad en base de datos.
    """
    params = params or {}

    if principal is not None:
        is_admin = bool("*" in principal.scopes or "mcp:admin" in principal.scopes)
        if not is_admin and principal.client_phone:
            requester_phone_or_jid = principal.client_phone

    clean_req = clean_phone_digits(requester_phone_or_jid)
    admin_phone = clean_phone_digits(getattr(settings, "ADMIN_WHATSAPP", "") or os.getenv("ADMIN_WHATSAPP", ""))

    # 1. Si es admin autenticado por sesión/token o teléfono admin reconocido
    if is_admin or (admin_phone and len(admin_phone) >= 10 and clean_req == admin_phone):
        if tool_name in DESTRUCTIVE_TOOLS:
            try:
                from core.audit import log_audit_event
                log_audit_event(
                    actor=(principal.subject if principal else requester_phone_or_jid) or "admin",
                    action="MCP_DESTRUCTIVE_TOOL_INVOKED",
                    target_type="mcp_tool",
                    target_id=tool_name,
                    old_value="",
                    new_value="Authorized destructive tool execution",
                    ip_or_source="mcp_guard",
                )
            except Exception:
                pass
        return {"allowed": True, "reason": "authorized_admin"}

    # 2. Bloquear tools exclusivas de administrador O cualquier tool no clasificada explícitamente como CLIENT_SCOPED (Default-Deny V08)
    if tool_name in ADMIN_ONLY_TOOLS or tool_name not in CLIENT_SCOPED_TOOLS:
        try:
            from core.audit import log_audit_event
            log_audit_event(
                actor=requester_phone_or_jid or (principal.subject if principal else "anonymous_client"),
                action="SECURITY_PROMPT_INJECTION_BLOCKED",
                target_type="mcp_tool",
                target_id=tool_name,
                old_value="",
                new_value=f"Blocked admin-only or unclassified execution attempt on {tool_name}",
                ip_or_source="mcp_guard",
            )
        except Exception as e:
            logger.warning(f"Error registrando auditoría en mcp_guard: {e}")

        logger.warning(f"🚨 Intento de ejecución no autorizada / Prompt Injection bloqueado en '{tool_name}' desde '{requester_phone_or_jid}'")
        return {
            "allowed": False,
            "error": f"Acción restringida: la herramienta '{tool_name}' requiere privilegios de administrador.",
        }

    # 3. Para tools de cliente, exigir identidad verificable y validar propiedad del recurso (IDOR prevention V08)
    if not clean_req:
        return {
            "allowed": False,
            "error": "Acceso denegado: no se pudo verificar la identidad del cliente solicitante.",
        }

    if not _verify_resource_ownership_in_db(clean_req, params):
        try:
            from core.audit import log_audit_event
            log_audit_event(
                actor=requester_phone_or_jid,
                action="SECURITY_CROSS_CLIENT_ATTEMPT_BLOCKED",
                target_type="mcp_tool",
                target_id=tool_name,
                old_value="",
                new_value=f"Requester {clean_req} attempted unauthorized cross-client resource access",
                ip_or_source="mcp_guard",
            )
        except Exception as e:
            logger.warning(f"Error registrando auditoría en mcp_guard: {e}")

        logger.warning(f"🚨 Intento de acceso cross-client bloqueado para {clean_req} en {tool_name}")
        return {
            "allowed": False,
            "error": "Acceso denegado: solo puedes consultar o gestionar tus propios servicios.",
        }

    return {"allowed": True, "reason": "client_scoped_ok"}


def wrap_mcp_tool_with_guard(func: Callable) -> Callable:
    """Envuelve una herramienta registrada en FastMCP para aplicar validate_tool_execution en cada invocación (V08)."""
    sig = inspect.signature(func)

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs):
            principal = get_current_mcp_principal()
            if principal is not None:
                try:
                    bound = sig.bind_partial(*args, **kwargs)
                    bound.apply_defaults()
                    params = dict(bound.arguments)
                except Exception:
                    params = dict(kwargs)
                verdict = validate_tool_execution(
                    tool_name=func.__name__,
                    params=params,
                    principal=principal,
                )
                if not verdict.get("allowed"):
                    raise PermissionError(verdict.get("error", "Acción no autorizada en MCP"))
            return await func(*args, **kwargs)
        return _async_wrapper
    else:
        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs):
            principal = get_current_mcp_principal()
            if principal is not None:
                try:
                    bound = sig.bind_partial(*args, **kwargs)
                    bound.apply_defaults()
                    params = dict(bound.arguments)
                except Exception:
                    params = dict(kwargs)
                verdict = validate_tool_execution(
                    tool_name=func.__name__,
                    params=params,
                    principal=principal,
                )
                if not verdict.get("allowed"):
                    raise PermissionError(verdict.get("error", "Acción no autorizada en MCP"))
            return func(*args, **kwargs)
        return _sync_wrapper
