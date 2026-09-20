import os
import re
import json
import logging
from typing import Optional, List, Dict, Any, Union

from mcp_server.instance import mcp
from mcp_server.models import ItemCuentaLote
import database
import system_logger
import whatsapp_client
from telegram_bot import send_telegram_message, format_and_send_alert, send_full_backup_to_telegram
from scheduler import check_and_send_alerts, check_and_send_stock_alerts

logger = logging.getLogger("mcp")


@mcp.tool()
async def buscar_cliente(
    query: str = "",
    cliente: str = "",
    nombre: str = "",
    telefono: str = "",
    email: str = ""
) -> str:
    """Busca un cliente por nombre/alias ('Carlos', 'Maik'), código (CLI-001), WhatsApp, Telegram o correo electrónico:
    - query: Término de búsqueda general (nombre, teléfono, código o email).
    - cliente / nombre: (Opcional) Nombre o alias del cliente si se pasa como parámetro nombrado.
    - telefono: (Opcional) Teléfono o WhatsApp del cliente.
    - email: (Opcional) Correo electrónico de la cuenta contratada.
    """
    search_term = (query or cliente or nombre or telefono or email or "").strip()
    if not search_term:
        return "❌ Error: Debes indicar un nombre, teléfono, código o correo para buscar el cliente."

    client = database.search_client(search_term)
    if not client:
        # Extraer dígitos de teléfono si venían mezclados
        digits = re.sub(r'\D', '', search_term)
        if len(digits) >= 8:
            client = database.search_client(digits)

    if not client:
        # Fallback inteligente: buscar en la libreta de contactos de Chatwoot (WhatsApp)
        try:
            cw_contacts = await whatsapp_client.search_chatwoot_contacts(search_term)
            if cw_contacts:
                c = cw_contacts[0]
                loc_attr = c.get("additional_attributes") or {}
                loc_parts = [loc_attr.get("city"), loc_attr.get("country")]
                loc = ", ".join([p for p in loc_parts if p]) or "No especificada"
                return (
                    f"📱 <b>Contacto encontrado en Chatwoot (WhatsApp):</b>\n"
                    f"• Nombre: <b>{c.get('name') or 'Sin nombre'}</b>\n"
                    f"• WhatsApp: <code>{c.get('phone_number') or 'No registrado'}</code>\n"
                    f"• Ubicación: {loc}\n"
                    f"• Chatwoot ID: #{c.get('id')}\n\n"
                    f"ℹ️ <i>Este contacto existe en Chatwoot/WhatsApp pero <b>aún no tiene suscripciones o cuenta comercial activa en el CRM</b>.</i>\n\n"
                    f"👉 <b>Acciones que puedes pedirme:</b>\n"
                    f"• <i>'Registra a {c.get('name')} como cliente'</i> para darlo de alta en el CRM.\n"
                    f"• <i>'Mándale un mensaje a {c.get('name')} por WhatsApp diciéndole...'</i>\n"
                    f"• <i>'Véndele una cuenta a {c.get('name')}...'</i>"
                )
        except Exception as e:
            logger.warning(f"Error consultando Chatwoot contacts: {e}")

        # Si no existe, responder con opciones claras y directivas de auto-alta
        cand_phone = ""
        digits = re.sub(r'\D', '', search_term)
        if len(digits) >= 8:
            cand_phone = database.clean_whatsapp_phone(digits)

        cand_name = (nombre or cliente or "").strip()
        if not cand_name:
            name_part = re.sub(r'[\+\d\-\(\)\.]+', ' ', search_term).strip()
            if name_part and len(name_part) >= 2:
                cand_name = name_part
            elif not cand_phone:
                cand_name = search_term

        resp = [
            f"ℹ️ El cliente '{search_term}' NO se encuentra registrado en el CRM.",
            "",
            "👉 <b>REGLA DEL SISTEMA:</b> Si estás procesando una venta, renovación o notificación, <b>DEBES registrar al cliente en el CRM</b>:",
        ]
        if cand_name and cand_phone:
            resp.append(f"• <b>Para registrarlo y asignarle la cuenta:</b> Usa directamente `vender_o_asignar_servicio(cliente='{cand_name}', whatsapp='{cand_phone}', ...)` (esta herramienta lo da de alta automáticamente en el CRM).")
            resp.append(f"• <b>Para darlo de alta ahora:</b> Llama a `registrar_cliente(nombre='{cand_name}', whatsapp='{cand_phone}')`.")
            resp.append(f"• <b>Para enviarle WhatsApp y registrarlo:</b> Usa `enviar_whatsapp_cliente(destinatario='{cand_phone}', nombre='{cand_name}', mensaje='...')`.")
        elif cand_phone:
            resp.append(f"• Dispones del teléfono (`{cand_phone}`) pero no del nombre. <b>Pregunta al administrador:</b> <i>'¿Con qué nombre deseas registrar al cliente del número {cand_phone}?'</i> o regístralo con `registrar_cliente(nombre='Cliente {cand_phone[-4:]}', whatsapp='{cand_phone}')`.")
        else:
            resp.append(f"• Dispones del nombre (`{cand_name}`) pero no del teléfono. <b>Pregunta al administrador:</b> <i>'¿Cuál es el número de WhatsApp de {cand_name}?'</i> o regístralo con `registrar_cliente(nombre='{cand_name}')`.")

        return "\n".join(resp)

    tipo = "👔 Revendedor" if client.get("client_type") == "revendedor" else "👤 Consumidor Final"
    lines = [
        f"👤 <b>Cliente:</b> {client['name']} ({client['client_code']})",
        f"• Tipo: {tipo}",
        f"• WhatsApp: {client.get('whatsapp') or 'No registrado'}",
        f"• Telegram: {client.get('telegram') or 'No registrado'}",
        f"• Notas: {client.get('notes') or '-'}",
        "\n📺 <b>Servicios contratados:</b>"
    ]

    accounts = client.get("accounts", [])
    if not accounts:
        lines.append("  (No tiene cuentas asociadas actualmente)")
    else:
        for a in accounts:
            estado_icon = "✅ Activa" if a["status"] == "ocupada" else ("🚨 CAÍDA" if a["status"] == "caida" else a["status"])
            perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
            pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
            lines.append(
                f"  • {a['platform']}{perf}: {a['email']} | Clave: {a['password']}{pin}\n"
                f"    Vence: {a['expiry_date']} | Estado: {estado_icon} | Cobro: {a.get('price') or '-'}"
            )

    return "\n".join(lines)


@mcp.tool()
def consultar_ficha_cliente(cliente: str = "", query: str = "", nombre: str = "") -> str:
    """Consulta la Ficha 360° integral de un cliente: salud de pagos, LTV en ARS, ganancia neta generada, suscripciones activas y link de cobro consolidado."""
    target = (cliente or query or nombre or "").strip()
    if not target:
        return "❌ Error: Debes indicar el nombre o código del cliente."
    profile = database.get_client_360_profile(target)
    if not profile:
        return f"❌ No se encontró ningún cliente con '{target}'."

    c = profile["client"]
    health = profile["health_status"]
    kpis = profile["financial_kpis"]
    act = profile["active_accounts"]
    billing = profile["consolidated_billing"]

    lines = [
        f"👤 <b>FICHA 360°: {c['name']}</b> ({c['client_code']})",
        f"• Tipo: {c['client_type_label']}",
        f"• Estado: {health['label']} ({health['summary']})",
        f"• Contacto: WhatsApp: {c.get('whatsapp') or '-'} | Telegram: {c.get('telegram') or '-'}",
        f"• Notas: {c.get('notes') or '-'}",
        "",
        "💰 <b>MÉTRICAS FINANCIERAS (ARS):</b>",
        f"• LTV (Total Cobrado Histórico): {kpis['ltv_formatted']} ({kpis['payments_count']} cobros)",
        f"• Ganancia Neta Real Acumulada: {kpis['total_profit_formatted']}",
        f"• Facturación Mensual Activa: {kpis['monthly_committed_spend_formatted']}",
    ]
    if kpis.get("last_payment"):
        lp = kpis["last_payment"]
        lines.append(f"• Último Pago: {lp['amount_formatted']} ({str(lp.get('created_at', ''))[:10]}) vía {lp.get('payment_method')}")

    lines.append(f"\n📺 <b>SUSCRIPCIONES ACTIVAS ({len(act)}):</b>")
    if not act:
        lines.append("  (No tiene servicios activos actualmente)")
    else:
        for a in act:
            perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
            pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
            lines.append(
                f"  • {a['platform']}{perf}: {a['email']} | Clave: {a['password']}{pin}\n"
                f"    Vence: {a.get('expiry_date')} ({a.get('days_label')}) | Cobro: {a.get('price_formatted')}"
            )

    if billing.get("success") and billing.get("wa_link"):
        lines.append("\n📲 <b>COBRO CONSOLIDADO WHATSAPP (1 Clic):</b>")
        lines.append(f"• Total a Cobrar: {billing['total_amount_formatted']}")
        lines.append(f"• Enlace directo: {billing['wa_link']}")

    return "\n".join(lines)


@mcp.tool()
def generar_cobro_consolidado_whatsapp(cliente: str, metodos_pago: str = "") -> str:
    """Genera el mensaje y enlace de 1 clic para cobrar todas las suscripciones activas de un cliente vía WhatsApp."""
    res = database.generate_consolidated_billing_whatsapp(cliente, payment_methods=metodos_pago)
    if not res.get("success"):
        return f"❌ {res.get('error', 'Error generando cobro consolidado')}"

    return (
        f"📲 <b>Cobro Consolidado WhatsApp para {res['client_name']}:</b>\n\n"
        f"💰 <b>Total Consolidado:</b> {res['total_amount_formatted']} ({res['accounts_count']} cuentas)\n"
        f"🔗 <b>Enlace de 1 Clic (wa.me):</b> {res['wa_link']}\n\n"
        f"💬 <b>Texto del Mensaje:</b>\n{res['message_text']}"
    )


@mcp.tool()
def listar_clientes_activos() -> str:
    """Muestra el listado completo de clientes registrados con su número de cuentas activas."""
    clients = database.list_all_clients()
    if not clients:
        return "No hay clientes registrados en la base de datos."

    lines = [f"👥 <b>Clientes Registrados ({len(clients)}):</b>\n"]
    for c in clients:
        tipo = "👔 Revendedor" if c.get("client_type") == "revendedor" else "👤 Final"
        lines.append(
            f"• [{c['client_code']}] {c['name']} ({tipo}) - Cuentas activas: {c.get('active_accounts_count', 0)}\n"
            f"  WhatsApp: {c.get('whatsapp') or '-'} | Telegram: {c.get('telegram') or '-'}"
        )
    return "\n".join(lines)


@mcp.tool()
def registrar_cliente(
    nombre: str,
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final",
    notas: str = ""
) -> str:
    """Registra o da de alta un nuevo cliente en el CRM asignándole automáticamente su código único CLI-XXX:
    - nombre: Nombre completo o alias del cliente (ej: 'Carlos Gómez').
    - whatsapp: Número de teléfono con código de país (ej: '+5493704418231').
    - telegram: (Opcional) Usuario de Telegram (@usuario).
    - tipo_cliente: 'consumidor_final' o 'revendedor'.
    - notas: Notas adicionales sobre el cliente o procedencia.
    """
    res = database.find_or_create_client(
        name=nombre,
        whatsapp=whatsapp,
        telegram=telegram,
        client_type=tipo_cliente,
        notes=notas
    )
    tipo = "👔 Revendedor" if res.get("client_type") == "revendedor" else "👤 Consumidor Final"
    return (
        f"✅ CLIENTE REGISTRADO CON ÉXITO EN EL CRM:\n"
        f"• Código: <code>{res['client_code']}</code>\n"
        f"• Nombre: <b>{res['name']}</b>\n"
        f"• Tipo: {tipo}\n"
        f"• WhatsApp: <code>{res.get('whatsapp') or 'No registrado'}</code>\n"
        f"• Telegram: {res.get('telegram') or 'No registrado'}\n"
        f"• Notas: {res.get('notes') or '-'}"
    )


@mcp.tool()
def generar_mensaje_whatsapp(
    correo_o_id: str,
    tipo_mensaje: str = "entrega",
    metodos_pago: str = ""
) -> str:
    """Genera plantillas profesionales y enlaces directos de 1 clic para WhatsApp (wa.me):
    - correo_o_id: Correo o ID de la cuenta/cliente.
    - tipo_mensaje: 'entrega' (datos de acceso y reglas de uso), 'cobro' (recordatorio de pago y vencimiento) o 'reemplazo' (reposición de cuenta caída).
    - metodos_pago: (Opcional) Texto con métodos de pago si se desea personalizar.
    """
    res = database.generate_whatsapp_message(
        account_or_id=correo_o_id,
        message_type=tipo_mensaje,
        payment_methods=metodos_pago
    )
    if not res.get("success"):
        return f"❌ Error: {res.get('error')}"

    tipo_nombre = {
        "entrega": "ENTREGA DE SERVICIO",
        "cobro": "RECORDATORIO DE COBRO Y RENOVACIÓN",
        "reemplazo": "REPOSICIÓN DE CUENTA CAÍDA"
    }.get(res['message_type'], res['message_type'].upper())

    return (
        f"💬 <b>MENSAJE LISTO PARA WHATSAPP ({tipo_nombre}):</b>\n\n"
        f"{res['message_text']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 <b>ENLACE DIRECTO (1 CLIC):</b>\n"
        f"{res['wa_link']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 Haz clic en el enlace para abrir WhatsApp con el mensaje ya redactado y listo para enviar."
    )


@mcp.tool()
def consultar_programa_referidos_cliente(cliente_o_id: str) -> str:
    """Consulta el estado del programa de referidos de un cliente: código único, saldo a favor en ARS y amigos invitados."""
    c = database.search_client(cliente_o_id)
    if not c:
        return f"❌ No se encontró ningún cliente para '{cliente_o_id}'."
    client_id = c["id"]
    return database.ReferralManager.format_referral_summary(client_id)


@mcp.tool()
def auditar_riesgo_churn_clientes(filtro_riesgo: str = "MEDIO") -> str:
    """Ejecuta una auditoría predictiva de riesgo de churn (abandono de clientes) identificando morosidad, cuentas caídas no resueltas e inactividad.
    - filtro_riesgo: 'ALTO' (solo clientes en alerta roja), 'MEDIO' (alertas amarillas y rojas) o 'TODOS'.
    """
    filtro = filtro_riesgo.upper().strip()
    if filtro not in ("ALTO", "MEDIO", "TODOS"):
        filtro = "MEDIO"
    min_level = "ALTO" if filtro == "ALTO" else ("MEDIO" if filtro == "MEDIO" else "BAJO")
    report = database.ChurnPredictor.get_risk_report(min_risk_level=min_level)
    return database.ChurnPredictor.format_report(report)
