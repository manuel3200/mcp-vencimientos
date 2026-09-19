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
def consultar_balance_y_ganancias(periodo: str = "mes_actual") -> str:
    """Calcula y reporta el balance financiero en tiempo real:
    - Ingresos totales cobrados este mes.
    - Costos pagados a proveedores.
    - Ganancia NETA real en el bolsillo.
    - Dinero pendiente por cobrar en los próximos 7 días.
    - Proyección mensual completa si todos los clientes pagan.
    """
    b = database.get_financial_balance(period=periodo)
    lines = [
        f"📊 <b>BALANCE FINANCIERO Y GANANCIAS ({b['period']}):</b>\n",
        f"💰 <b>Ingresos Cobrados:</b> {database.format_ars(b['collected_income'])} ({b['transactions_count']} cobros registrados)",
        f"📉 <b>Costos de Proveedor:</b> {database.format_ars(b['collected_costs'])}",
        f"💵 <b>GANANCIA NETA REAL:</b> {database.format_ars(b['collected_profit'])}",
        "\n━━━━━━━━━━━━━━━━━━━━━━",
        f"⏳ <b>POR COBRAR PRÓXIMAMENTE (7 días):</b>",
        f"• Total a cobrar: <b>{database.format_ars(b['pending_receivables_7d'])}</b> ({b['pending_accounts_count']} cuentas)",
        f"🎯 <b>Proyección Mensual Total (Todas las cuentas):</b> {database.format_ars(b['projected_monthly_profit'])} de ganancia neta",
        f"📱 Total de suscripciones activas: {b['active_subscriptions_total']}"
    ]
    return "\n".join(lines)


@mcp.tool()
def registrar_cobro_cliente(
    correo_o_id: str,
    monto: Optional[float] = None,
    metodo_pago: str = "Transferencia",
    nueva_fecha_vencimiento: Optional[str] = None
) -> str:
    """Registra el cobro de una mensualidad o renovación de un cliente en Pesos Argentinos (ARS):
    Suma el dinero a tus ingresos cobrados, calcula la ganancia neta y extiende la fecha de vencimiento 30 días automáticamente.
    - correo_o_id: Correo o ID de la cuenta que pagó.
    - monto: Monto recibido en ARS (si no se especifica, toma el precio habitual de la cuenta).
    - metodo_pago: 'Transferencia', 'Mercado Pago', 'Efectivo', 'Binance / USDT'.
    - nueva_fecha_vencimiento: (Opcional) Si quieres fijar una fecha específica en lugar de sumar 30 días.
    """
    res = database.register_customer_payment(
        email_or_id=correo_o_id,
        amount=monto,
        payment_method=metodo_pago,
        new_expiry_date=nueva_fecha_vencimiento
    )
    if not res.get("success"):
        return f"❌ Error: {res.get('error')}"

    return (
        f"✅ PAGO Y RENOVACIÓN REGISTRADOS CON ÉXITO:\n"
        f"• Cliente: {res['client_name']}\n"
        f"• Servicio: {res['platform']} ({res['email']})\n"
        f"• Monto cobrado: {database.format_ars(res['amount'])} ({metodo_pago})\n"
        f"• Ganancia neta de este cobro: +{database.format_ars(res['profit'])}\n"
        f"• Nuevo vencimiento: <code>{res['new_expiry']}</code> (30 días extendidos)\n"
        f"🎉 El balance financiero ha sido actualizado automáticamente."
    )


@mcp.tool()
def consultar_cuentas_por_cobrar(dias_anticipacion: int = 7) -> str:
    """Muestra todas las cuentas que vencen en los próximos días con el monto en ARS que debes cobrar y los datos del cliente."""
    b = database.get_financial_balance()
    pending = b.get("pending_accounts", [])
    if not pending:
        return f"🎉 ¡Al día! No hay cobros pendientes para los próximos {dias_anticipacion} días."

    lines = [
        f"⏳ <b>Cobros Pendientes ({len(pending)} cuentas - Total: {database.format_ars(b['pending_receivables_7d'])}):</b>\n"
    ]
    for p in pending:
        d_txt = "HOY" if p['days_remaining'] == 0 else (f"en {p['days_remaining']}d" if p['days_remaining'] > 0 else f"VENCIDA hace {abs(p['days_remaining'])}d")
        wa_data = database.generate_whatsapp_message(p['email'], message_type="cobro")
        wa_url = wa_data.get('wa_link', '')
        wa_line = f"\n  📲 Link WhatsApp (1 Clic): {wa_url}" if wa_url else ""
        lines.append(
            f"• <b>{p['client']}</b> - {p['platform']} ({p['email']})\n"
            f"  A cobrar: <b>{database.format_ars(p['price'])}</b> | Vence: {d_txt}\n"
            f"  Contacto: WhatsApp: {p.get('whatsapp') or '-'} | Telegram: {p.get('telegram') or '-'}"
            f"{wa_line}"
        )
    return "\n".join(lines)


@mcp.tool()
def consultar_datos_pago() -> str:
    """Consulta la configuración actual de datos de cobro bancarios (Alias Mercado Pago, CBU/CVU, Titular, Banco y USDT)."""
    s = database.get_payment_settings()
    pm_block = database.get_formatted_payment_methods()
    return (
        f"💳 <b>DATOS DE COBRO CONFIGURADOS (ARS / USDT):</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Alias MP:</b> <code>{s.get('alias_mp') or 'No configurado'}</code>\n"
        f"• <b>CBU / CVU:</b> <code>{s.get('cvu_cbu') or 'No configurado'}</code>\n"
        f"• <b>Titular:</b> <b>{s.get('account_holder') or 'No configurado'}</b>\n"
        f"• <b>Banco / Entidad:</b> {s.get('bank_name') or 'Mercado Pago'}\n"
        f"• <b>USDT / Cripto:</b> <code>{s.get('usdt_address') or 'No configurado'}</code>\n"
        f"• <b>Instrucciones:</b> {s.get('extra_instructions') or 'Enviar comprobante por WhatsApp'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Bloque formateado que se inserta en {{metodos_pago}}:</b>\n\n{pm_block}"
    )


@mcp.tool()
def configurar_datos_pago(
    alias_mp: str = "",
    cvu_cbu: str = "",
    titular: str = "",
    banco: str = "Mercado Pago / Transferencia Bancaria",
    usdt: str = "",
    instrucciones_extra: str = ""
) -> str:
    """Actualiza los datos de cobro bancarios (Alias Mercado Pago, CBU, Titular, etc.) para que se inserten automáticamente en los mensajes de WhatsApp.
    - alias_mp: Alias de Mercado Pago o billetera virtual (ej: juan.streaming.mp).
    - cvu_cbu: Número de 22 dígitos CBU/CVU.
    - titular: Nombre completo del titular de la cuenta receptora.
    - banco: Nombre de la entidad financiera (ej: Mercado Pago, Banco Galicia, Brubank).
    - usdt: Dirección de Binance Pay o red TRC20/BEP20 (opcional).
    - instrucciones_extra: Instrucciones de pago adicionales (ej: 'Enviar comprobante').
    """
    res = database.save_payment_settings(
        alias_mp=alias_mp,
        cvu_cbu=cvu_cbu,
        account_holder=titular,
        bank_name=banco,
        usdt_address=usdt,
        extra_instructions=instrucciones_extra
    )
    return (
        f"✅ DATOS DE COBRO ACTUALIZADOS EXITOSAMENTE:\n"
        f"• Alias MP: <code>{res.get('alias_mp')}</code>\n"
        f"• CBU/CVU: <code>{res.get('cvu_cbu')}</code>\n"
        f"• Titular: {res.get('account_holder')}\n"
        f"• Banco: {res.get('bank_name')}\n"
        f"🎉 Ya están disponibles e integrados en todas las plantillas de WhatsApp mediante la etiqueta {{metodos_pago}}."
    )

