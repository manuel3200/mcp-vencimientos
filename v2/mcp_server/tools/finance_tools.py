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


@mcp.tool()
def listar_comprobantes_pendientes(
    estado: str = "pending",
    limite: int = 10
) -> str:
    """Lista los comprobantes de pago registrados por OCR o recibidos en el sistema según su estado:
    - estado: 'pending' (pendientes de aprobación, por defecto), 'approved' (aprobados), 'rejected' (rechazados) o 'all' (todos).
    - limite: Cantidad máxima de comprobantes a mostrar (por defecto 10).
    Muestra el ID (#P<id>), cliente, contacto, monto, entidad bancaria, número de operación y fecha detectada.
    """
    clean_status = estado.strip().lower()
    items = database.list_pending_payments(status=clean_status, limit=limite)
    if not items:
        if clean_status == "pending":
            return "🎉 ¡Excelente! No hay comprobantes de pago pendientes de revisión en este momento."
        return f"📋 No se encontraron comprobantes con estado '{clean_status}'."

    count_pending = database.count_pending_payments()
    lines = [
        f"🧾 <b>COMPROBANTES DE PAGO REGISTRADOS (Estado: {clean_status.upper()} - {len(items)} items):</b>",
        f"<i>Total pendientes de revisión en el sistema: {count_pending}</i>\n"
    ]
    for p in items:
        pid = p["id"]
        c_name = p.get("client_name") or "Cliente no identificado"
        c_phone = p.get("sender_phone") or p.get("client_whatsapp") or "-"
        monto_str = p.get("amount_formatted") or database.format_ars(p.get("amount") or 0.0)
        banco = p.get("bank") or "No identificado"
        op_id = p.get("operation_id") or "-"
        fecha = p.get("date_detected") or p.get("created_at") or "-"
        st = p.get("status", "pending").upper()
        st_ico = "⏳" if st == "PENDING" else ("✅" if st == "APPROVED" else "❌")

        svc_part = ""
        if p.get("platform"):
            acc_mail = f" ({p.get('account_email')})" if p.get("account_email") else ""
            svc_part = f"\n  📺 Servicio: <b>{p.get('platform')}</b>{acc_mail}"

        notes_part = f"\n  📝 Notas: {p['notes']}" if p.get("notes") else ""

        lines.append(
            f"{st_ico} <b>Comprobante #P{pid}</b> [{st}]\n"
            f"  👤 Cliente: <b>{c_name}</b> (WhatsApp: <code>{c_phone}</code>)\n"
            f"  💰 Monto: <b>{monto_str}</b> | Banco: <b>{banco}</b> | Op: <code>{op_id}</code>\n"
            f"  📅 Fecha: {fecha}"
            f"{svc_part}"
            f"{notes_part}"
        )

    lines.append("\n💡 <i>Para aprobar un comprobante usa:</i> <code>aprobar_comprobante_pago(pago_id=...)</code>")
    lines.append("💡 <i>Para rechazarlo usa:</i> <code>rechazar_comprobante_pago(pago_id=..., motivo=...)</code>")
    return "\n".join(lines)


@mcp.tool()
async def aprobar_comprobante_pago(
    pago_id: int,
    renovar_todas: bool = False,
    monto_personalizado: Optional[float] = None,
    notificar_cliente: bool = True
) -> str:
    """Aprueba un comprobante de pago (#P<id>):
    - Asienta el dinero en el balance financiero de ingresos cobrados.
    - Extiende 30 días el vencimiento de la suscripción vinculada (o de todas las cuentas activas del cliente si renovar_todas=True).
    - Marca el comprobante como 'approved'.
    - Notifica al cliente por WhatsApp con confirmación oficial si notificar_cliente=True.
    - Envía alerta de confirmación al bot de Telegram del administrador.
    
    Parámetros:
    - pago_id: Número ID del comprobante a aprobar (ej: 12 para #P12).
    - renovar_todas: Si es True, renueva todas las cuentas activas del cliente en lugar de solo una.
    - monto_personalizado: (Opcional) Si el OCR leyó un monto incorrecto, puedes indicar el monto real en ARS.
    - notificar_cliente: Si es True (por defecto), envía mensaje de confirmación y agradecimiento al cliente por WhatsApp.
    """
    res = database.approve_pending_payment(
        payment_id=pago_id,
        admin_user="Gemini Spark (MCP)",
        custom_amount=monto_personalizado,
        renew_all=renovar_todas
    )
    if not res.get("success"):
        return f"❌ Error al aprobar comprobante #P{pago_id}: {res.get('error')}"

    p = res.get("payment", {})
    c_name = p.get("client_name") or "Cliente"
    amt_fmt = p.get("amount_formatted") or database.format_ars(p.get("amount") or 0.0)
    bank_name = p.get("bank") or "Transferencia"
    c_phone = p.get("sender_phone") or p.get("client_whatsapp") or ""
    renewed = res.get("renewed_accounts", [])

    # 1. Notificación por WhatsApp al cliente si corresponde
    wa_notif_status = "⏸️ Notificación al cliente omitida (notificar_cliente=False)"
    if notificar_cliente and c_phone:
        c_clean = database.clean_whatsapp_phone(c_phone)
        if c_clean:
            if renewed and len(renewed) > 1:
                lines_ren = "\n".join([f"• *{r['platform']}*: `{r['email']}` (Vence: {r.get('new_expiry_date')})" for r in renewed])
                c_msg = (
                    f"🎉 ¡Hola {c_name}! Confirmamos la recepción de tu pago de *{amt_fmt}* ({bank_name})"
                    f" y la renovación exitosa de tus servicios activos:\n\n{lines_ren}\n\n"
                    f"¡Tus suscripciones quedaron al día! Muchas gracias por tu pago y preferencia. 🙌✨"
                )
            else:
                svc_desc = p.get("platform") or (renewed[0]["platform"] if renewed else "Suscripción")
                new_exp = (renewed[0]["new_expiry_date"] if renewed else p.get("account_expiry")) or "extendido (+30 días)"
                c_msg = (
                    f"🎉 ¡Hola {c_name}! Confirmamos la recepción de tu pago de *{amt_fmt}* ({bank_name}).\n\n"
                    f"Tu servicio de *{svc_desc}* quedó renovado con éxito hasta el *{new_exp}*. "
                    f"¡Muchas gracias por tu pago y confianza! 🙌✨"
                )
            try:
                wa_res = await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)
                if wa_res.get("success"):
                    wa_notif_status = f"📲 Confirmación enviada al WhatsApp del cliente ({c_clean})"
                else:
                    wa_notif_status = f"⚠️ Falló envío de WhatsApp al cliente: {wa_res.get('error')}"
            except Exception as e:
                wa_notif_status = f"⚠️ Excepción al enviar WhatsApp: {e}"

    # 2. Notificación en Telegram para auditoría
    try:
        tg_text = (
            f"✅ <b>COMPROBANTE #P{pago_id} APROBADO (GEMINI MCP)</b>\n\n"
            f"• Cliente: <b>{c_name}</b>\n"
            f"• Monto Acreditado: <b>{amt_fmt}</b> ({bank_name})\n"
            f"• Cuentas Renovadas: <b>{len(renewed) if renewed else 1}</b>\n"
            f"• Impactado en Balance Financiero & CRM."
        )
        await send_telegram_message(tg_text)
    except Exception as e:
        logger.warning(f"Error enviando notificación a Telegram: {e}")

    # Resumen estructurado para Gemini
    out = [
        f"✅ <b>COMPROBANTE #P{pago_id} APROBADO EXITOSAMENTE:</b>",
        f"• Cliente: <b>{c_name}</b> (WhatsApp: {c_phone})",
        f"• Monto Ingresado: <b>{amt_fmt}</b> ({bank_name})",
        f"• Estado: <b>Aprobado (Impactado en Finanzas)</b>",
        f"• Notificación: {wa_notif_status}"
    ]
    if renewed:
        out.append(f"• Servicios renovados (+30 días):")
        for r in renewed:
            out.append(f"  - <b>{r['platform']}</b> ({r['email']}) ➔ Nuevo vencimiento: <code>{r.get('new_expiry_date')}</code>")
    return "\n".join(out)


@mcp.tool()
async def rechazar_comprobante_pago(
    pago_id: int,
    motivo: str = "Comprobante no válido o ilegible",
    notificar_cliente: bool = False
) -> str:
    """Rechaza o descarta un comprobante de pago (#P<id>):
    - Marca el registro como 'rejected' registrando el motivo y la auditoría.
    - Opcionalmente le envía un mensaje por WhatsApp al cliente informándole que el comprobante no pudo validarse.
    - Envía aviso al bot de Telegram.

    Parámetros:
    - pago_id: Número ID del comprobante a rechazar (ej: 12 para #P12).
    - motivo: Razón del rechazo (ej: 'Transferencia no impactada en la cuenta bancaria', 'Monto insuficiente', 'Comprobante borroso').
    - notificar_cliente: Si es True, envía un mensaje cordial al cliente pidiéndole reenviar el comprobante correcto.
    """
    res = database.reject_pending_payment(
        payment_id=pago_id,
        reason=motivo,
        admin_user="Gemini Spark (MCP)"
    )
    if not res.get("success"):
        return f"❌ Error al rechazar comprobante #P{pago_id}: {res.get('error')}"

    p = res.get("payment", {})
    c_name = p.get("client_name") or "Cliente"
    c_phone = p.get("sender_phone") or p.get("client_whatsapp") or ""

    wa_status = "⏸️ Notificación al cliente no solicitada"
    if notificar_cliente and c_phone:
        c_clean = database.clean_whatsapp_phone(c_phone)
        if c_clean:
            c_msg = (
                f"Hola {c_name}. 🙌 Te informamos que no pudimos validar el comprobante enviado (#P{pago_id}).\n\n"
                f"📌 *Motivo:* {motivo}\n\n"
                f"Por favor, revisa la transferencia y envíanos el comprobante emitido por tu banco para poder acreditar tu pago. ¡Muchas gracias!"
            )
            try:
                wa_res = await whatsapp_client.send_text_message(c_clean, c_msg, delay_seconds=1.0)
                if wa_res.get("success"):
                    wa_status = f"📲 Notificación de rechazo enviada al WhatsApp del cliente ({c_clean})"
                else:
                    wa_status = f"⚠️ Falló envío de WhatsApp: {wa_res.get('error')}"
            except Exception as e:
                wa_status = f"⚠️ Excepción al enviar WhatsApp: {e}"

    # Telegram
    try:
        await send_telegram_message(
            f"❌ <b>COMPROBANTE #P{pago_id} RECHAZADO (GEMINI MCP)</b>\n\n"
            f"• Cliente: <b>{c_name}</b>\n"
            f"• Motivo: {motivo}\n"
            f"• Administrador: Gemini Spark (MCP)"
        )
    except Exception as e:
        logger.warning(f"Error enviando aviso a Telegram: {e}")

    return (
        f"❌ <b>COMPROBANTE #P{pago_id} RECHAZADO:</b>\n"
        f"• Cliente: <b>{c_name}</b>\n"
        f"• Motivo registrado: <i>{motivo}</i>\n"
        f"• Estado: <b>Rejected (Descartado sin impacto financiero)</b>\n"
        f"• Notificación: {wa_status}"
    )


@mcp.tool()
def crear_cupon_descuento(
    codigo: str,
    tipo_descuento: str = "percent",
    valor_descuento: float = 10.0,
    compra_minima: float = 0.0,
    limite_usos: int = 100,
    dias_vigencia: int = 30
) -> str:
    """Crea un cupón de descuento promocional para clientes en StreamVault.
    - codigo: Texto del cupón (ej: 'PROMO10', 'BIENVENIDA', 'ESTRENO2026').
    - tipo_descuento: 'percent' (porcentaje) o 'fixed_ars' (monto fijo en pesos).
    - valor_descuento: Valor del descuento (ej: 15 para 15%, o 1500 para $1500 ARS).
    - compra_minima: Monto mínimo de compra en ARS para aplicar el cupón.
    - limite_usos: Cantidad máxima de canjes permitidos.
    - dias_vigencia: Días antes de que el cupón expire.
    """
    try:
        coupon = database.CouponManager.create(
            code=codigo,
            discount_type=tipo_descuento,
            discount_value=valor_descuento,
            min_purchase=compra_minima,
            max_uses=limite_usos,
            expires_in_days=dias_vigencia
        )
        t_label = f"{coupon['discount_value']}%" if coupon['discount_type'] == 'percent' else f"${coupon['discount_value']} ARS"
        return (
            f"🎟️ <b>CUPÓN CREADO EXITOSAMENTE:</b>\n\n"
            f"• Código: <code>{coupon['code']}</code>\n"
            f"• Descuento: <b>{t_label}</b>\n"
            f"• Compra Mínima: ${coupon['min_purchase']:.2f} ARS\n"
            f"• Cupo Máximo: {coupon['max_uses']} usos\n"
            f"• Vence: {coupon['expires_at'][:10]}\n"
            f"• Estado: 🟢 Activo"
        )
    except Exception as e:
        return f"❌ Error creando cupón: {str(e)}"


@mcp.tool()
def listar_cupones_activos() -> str:
    """Lista todos los cupones de descuento vigentes y disponibles para canje."""
    coupons = database.list_active_coupons()
    if not coupons:
        return "ℹ️ No hay cupones activos actualmente en el sistema."

    lines = ["🎟️ <b>CUPONES PROMOCIONALES ACTIVOS:</b>\n"]
    for c in coupons:
        t_label = f"{c['discount_value']}%" if c['discount_type'] == 'percent' else f"${c['discount_value']} ARS"
        lines.append(
            f"• <code>{c['code']}</code>: <b>{t_label}</b> | Usos: {c['times_used']}/{c['max_uses']} | Vence: {c['expires_at'][:10]}"
        )
    return "\n".join(lines)


@mcp.tool()
def validar_cupon_descuento(codigo: str, monto_compra: float) -> str:
    """Valida un código de cupón contra un monto de orden y calcula el ahorro neto en ARS."""
    is_valid, msg, discount, final_amount = database.CouponManager.validate_and_calculate(codigo, monto_compra)
    if is_valid:
        return (
            f"✅ <b>CUPÓN VÁLIDO:</b>\n"
            f"• Código: <code>{codigo.upper()}</code>\n"
            f"• Monto Original: ${monto_compra:.2f} ARS\n"
            f"• Descuento: -${discount:.2f} ARS\n"
            f"• <b>TOTAL FINAL A PAGAR: ${final_amount:.2f} ARS</b>"
        )
    return f"❌ <b>CUPÓN INVÁLIDO:</b> {msg}"


