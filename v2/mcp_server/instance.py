from fastmcp import FastMCP
from core.mcp_guard import wrap_mcp_tool_with_guard

# Instancia central de FastMCP para Gemini Spark con directivas de negocio
MCP_INSTRUCTIONS = """
Eres el Asistente Oficial y Administrador Inteligente de StreamVault CRM.
REGLAS OBLIGATORIAS:
1. GESTIÓN Y AUTO-ALTA DE CLIENTES: Si un cliente no está registrado en el CRM al solicitar una venta, asignación o envío de mensaje, SIEMPRE debes registrarlo en el CRM (usando 'vender_o_asignar_servicio', 'vender_perfil_compartido' o 'registrar_cliente'). Si no se especificó un nombre para el número, pregúntale al administrador: "¿Con qué nombre deseas registrar al cliente de este número?" antes de continuar o tras despachar la acción.
2. ENVÍO DE WHATSAPP: Al enviar avisos mediante 'enviar_whatsapp_cliente', si el cliente no estaba registrado, el sistema lo dará de alta automáticamente con su nombre y número.
3. FLUIDEZ OPERATIVA: Nunca bloquees una solicitud ni asumas que no se puede interactuar con el cliente; regístralo y ejecuta la operación solicitada.
"""

mcp = FastMCP("Streaming CRM & Expiry Bot", instructions=MCP_INSTRUCTIONS)

_original_mcp_tool = mcp.tool


def _guarded_mcp_tool(*args, **kwargs):
    """Intercepta el decorador @mcp.tool para envolver cada herramienta con wrap_mcp_tool_with_guard (V08)."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        guarded = wrap_mcp_tool_with_guard(args[0])
        return _original_mcp_tool(guarded)

    decorator = _original_mcp_tool(*args, **kwargs)

    def _inner(func):
        guarded = wrap_mcp_tool_with_guard(func)
        return decorator(guarded)

    return _inner


mcp.tool = _guarded_mcp_tool

