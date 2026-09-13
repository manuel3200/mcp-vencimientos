import os
import re
import json
import base64
import secrets
import logging
import urllib.parse
from datetime import date, datetime
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union, Tuple

from fastapi import FastAPI, Request, Response, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import pyotp

import database
import system_logger
system_logger.setup_system_logging()
import whatsapp_client

from telegram_bot import send_telegram_message, format_and_send_alert, start_telegram_polling, stop_telegram_polling, send_full_backup_to_telegram
from scheduler import start_scheduler, stop_scheduler, check_and_send_alerts

logger = logging.getLogger("main")

# ==========================================
# Configuración de Seguridad y Sesiones
# ==========================================
SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "mcp-super-secret-key-change-in-prod-2026")
serializer = URLSafeTimedSerializer(SECRET_KEY)

def create_session_cookie(username: str) -> str:
    return serializer.dumps({"user": username.lower(), "auth": True}, salt="session-auth")

def verify_session_cookie(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, salt="session-auth", max_age=86400 * 7)
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

def create_preauth_cookie(username: str) -> str:
    return serializer.dumps({"user": username.lower(), "step": "2fa"}, salt="preauth")

def verify_preauth_cookie(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, salt="preauth", max_age=300)
        return data.get("user")
    except (SignatureExpired, BadSignature):
        return None

# ==========================================
# Modelos Pydantic para Carga en Lote
# ==========================================
class ItemCuentaLote(BaseModel):
    plataforma: str = Field(description="Plataforma (Netflix, Disney+, Max, etc.)")
    correo: str = Field(description="Correo o usuario de la cuenta")
    contrasena: str = Field(description="Contraseña de la cuenta")
    fecha_vencimiento: str = Field(description="Fecha de vencimiento formato YYYY-MM-DD")
    precio: str = Field(default="", description="Precio cobrado al cliente")
    recurrencia: str = Field(default="mensual", description="mensual, trimestral, anual, unico")
    perfil: str = Field(default="", description="Nombre de perfil si aplica")
    pin: str = Field(default="", description="PIN del perfil si aplica")

# ==========================================
# 1. Herramientas FastMCP para Gemini Spark
# ==========================================
mcp = FastMCP("Streaming CRM & Expiry Bot")

# --- Herramientas Financieras (Paso 1) ---
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

# --- Herramientas de Pantallas Compartidas y Perfiles (Paso 3) ---
@mcp.tool()
def crear_cuenta_con_pantallas(
    plataforma: str,
    correo: str,
    contrasena: str,
    cantidad_pantallas: int = 4,
    pines: str = "",
    costo_total: str = "",
    notas: str = ""
) -> str:
    """Crea una cuenta madre en stock y genera automáticamente sus N pantallas/perfiles libres para la venta:
    - plataforma: Netflix, Disney+, Max, Prime Video, etc.
    - correo: Correo de la cuenta madre.
    - contrasena: Contraseña de la cuenta.
    - cantidad_pantallas: Cantidad de perfiles permitidos (por defecto 4).
    - pines: (Opcional) PINes individuales separados por coma o espacio (ej: '1111, 2222, 3333, 4444').
    - costo_total: (Opcional) Lo que te costó la cuenta al proveedor.
    """
    res = database.create_master_account_with_profiles(
        platform=plataforma,
        email=correo,
        password=contrasena,
        profile_count=cantidad_pantallas,
        pins=pines,
        cost=costo_total,
        notes=notas
    )
    p_names = [f"• {p['profile_name']}" + (f" [PIN: {p['profile_pin']}]" if p.get('profile_pin') else "") for p in res]
    return (
        f"✅ CUENTA MADRE CON PANTALLAS CREADA:\n"
        f"• Plataforma: {plataforma.title()}\n"
        f"• Correo: {correo}\n"
        f"• Clave: {contrasena}\n"
        f"• Total de pantallas libres disponibles: {len(res)}\n"
        + "\n".join(p_names) + "\n"
        f"🎉 Todas las pantallas están listas en inventario para ser asignadas a clientes individualmente."
    )

@mcp.tool()
def vender_perfil_compartido(
    cliente: str,
    plataforma: str,
    fecha_vencimiento: str,
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final",
    precio: str = "",
    notas: str = ""
) -> str:
    """Asigna automáticamente el próximo perfil libre disponible de una cuenta madre al cliente:
    - cliente: Nombre o alias del cliente (ej: Carlos, Maik).
    - plataforma: Netflix, Disney+, Max, etc.
    - fecha_vencimiento: Formato YYYY-MM-DD.
    - whatsapp / telegram: Datos de contacto.
    - precio: Precio de venta del perfil individual.
    """
    acc = database.assign_next_free_profile(
        client_name=cliente,
        platform=plataforma,
        expiry_date=fecha_vencimiento,
        whatsapp=whatsapp,
        telegram=telegram,
        client_type=tipo_cliente,
        price=precio,
        notes=notes
    )
    if not acc:
        return f"⚠️ No hay perfiles libres disponibles para la plataforma '{plataforma}'. Agrega stock o crea una cuenta madre."

    wa = database.generate_whatsapp_message(acc, message_type="entrega")
    wa_url = wa.get("wa_link", "")

    return (
        f"🎉 PERFIL INDIVIDUAL ASIGNADO CON ÉXITO:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Cliente:</b> {acc['client_name']} ({acc.get('client_code') or ''})\n"
        f"📺 <b>Plataforma:</b> {acc['platform']} - <b>{acc['profile_name']}</b>\n"
        f"📧 <b>Correo Madre:</b> <code>{acc['email']}</code>\n"
        f"🔑 <b>Contraseña:</b> <code>{acc['password']}</code>" + (f" | <b>PIN:</b> <code>{acc['profile_pin']}</code>" if acc.get('profile_pin') else "") + "\n"
        f"📅 <b>Vencimiento:</b> <code>{acc['expiry_date']}</code> | Precio: {acc.get('price') or '-'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 <b>WhatsApp Listo para Enviar (1 Clic):</b>\n{wa_url}"
    )

@mcp.tool()
def consultar_estado_pantallas(plataforma: str = "") -> str:
    """Muestra el inventario organizado por cuenta madre con casilleros de pantallas ocupadas vs libres."""
    overview = database.get_shared_screens_overview(platform=plataforma if plataforma else None)
    if not overview:
        return "No hay cuentas madre registradas."

    lines = [f"📺 <b>ESTADO DE PANTALLAS COMPARTIDAS ({len(overview)} Cuentas Madre):</b>\n"]
    for o in overview:
        lines.append(
            f"🔹 <b>{o['platform']}</b> | <code>{o['email']}</code>\n"
            f"   Ocupación: {o['occupied_count']}/{o['total_profiles']} pantallas ({o['free_count']} libres) - {o['occupancy_rate']}%\n"
            f"   Detalle de pantallas:"
        )
        for p in o['profiles']:
            st = p['status']
            pin_txt = f" [PIN: {p['profile_pin']}]" if p.get('profile_pin') else ""
            if st == 'ocupada':
                lines.append(f"     ✅ {p['profile_name']}{pin_txt}: {p.get('client_name', 'Cliente')} (Vence: {p.get('expiry_date')})")
            elif st == 'libre':
                lines.append(f"     ✨ {p['profile_name']}{pin_txt}: <b>DISPONIBLE</b>")
            elif st == 'caida':
                lines.append(f"     🚨 {p['profile_name']}{pin_txt}: CAÍDA ({p.get('client_name')})")
        lines.append("")
    return "\n".join(lines)

@mcp.tool()
def reportar_caida_cuenta_madre(correo: str, motivo: str = "Caída de cuenta completa") -> str:
    """Marca como caídas todas las pantallas de una cuenta madre y lista los clientes afectados para su reemplazo."""
    res = database.mark_entire_master_account_fallen(correo, reason=motivo)
    if not res.get("success"):
        return f"❌ {res.get('error')}"

    clients = res.get("affected_clients", [])
    lines = [
        f"🚨 <b>CUENTA MADRE COMPLETA MARCADA COMO CAÍDA:</b>\n"
        f"• Correo: {res['email']}\n"
        f"• Total de pantallas afectadas: {res['total_profiles_affected']}\n"
        f"• Clientes que necesitan reemplazo ({len(clients)}):"
    ]
    for c in clients:
        lines.append(f"  • {c['client_name']} ({c['platform']} - {c['profile_name']}) | WhatsApp: {c.get('whatsapp') or '-'}")

    lines.append("\n💡 Puedes pedirme: 'Cámbiame el correo caído X por una libre' para reasignarlos.")
    return "\n".join(lines)

# --- Herramientas de Carga y Gestión ---
@mcp.tool()
def registrar_ventas_en_lote(
    cliente: str,
    cuentas: Union[List[ItemCuentaLote], List[Dict[str, Any]], str],
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final"
) -> str:
    """Registra múltiples ventas en UNA SOLA OPERACIÓN masiva para que solo pida permiso 1 sola vez."""
    lista_items = cuentas
    if isinstance(cuentas, str):
        try:
            lista_items = json.loads(cuentas)
        except Exception:
            lista_items = []

    cargadas = 0
    errores = 0

    for item in lista_items:
        try:
            if isinstance(item, ItemCuentaLote):
                c_dict = item.model_dump()
            elif isinstance(item, dict):
                c_dict = item
            else:
                continue

            database.assign_or_sell_account(
                client_name=cliente,
                platform=c_dict.get("plataforma", "Streaming"),
                email=c_dict.get("correo", ""),
                password=c_dict.get("contrasena", ""),
                expiry_date=c_dict.get("fecha_vencimiento", ""),
                whatsapp=whatsapp,
                telegram=telegram,
                client_type=tipo_cliente,
                profile_name=c_dict.get("perfil", ""),
                profile_pin=c_dict.get("pin", ""),
                recurrence=c_dict.get("recurrencia", "mensual"),
                price=c_dict.get("precio", "")
            )
            cargadas += 1
        except Exception as e:
            logger.error(f"Error cargando cuenta individual: {e}")
            errores += 1

    tipo_badge = "👔 Revendedor" if "revend" in tipo_cliente.lower() else "👤 Consumidor Final"
    return (
        f"🎉 CARGA MASIVA COMPLETADA:\n"
        f"• Cliente: {cliente} ({tipo_badge})\n"
        f"• WhatsApp: {whatsapp or '-'} | Telegram: {telegram or '-'}\n"
        f"• Total de cuentas registradas con éxito: {cargadas}\n"
        + (f"• Errores: {errores}\n" if errores > 0 else "")
        + "Todas las cuentas ya están disponibles en el panel y programadas para alerta 2 días antes."
    )

@mcp.tool()
def vender_o_asignar_servicio(
    cliente: str,
    plataforma: str,
    correo: str,
    contrasena: str,
    fecha_vencimiento: str,
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final",
    perfil: str = "",
    pin: str = "",
    precio: str = "",
    costo: str = "",
    recurrencia: str = "mensual",
    notas: str = ""
) -> str:
    """Registra una venta o asignación individual de cuenta o perfil de streaming a un cliente."""
    try:
        acc = database.assign_or_sell_account(
            client_name=cliente,
            platform=plataforma,
            email=correo,
            password=contrasena,
            expiry_date=fecha_vencimiento,
            whatsapp=whatsapp,
            telegram=telegram,
            client_type=tipo_cliente,
            profile_name=perfil,
            profile_pin=pin,
            recurrence=recurrencia,
            price=precio,
            cost=costo,
            notes=notas
        )
        wa_data = database.generate_whatsapp_message(acc, message_type="entrega")
        wa_url = wa_data.get("wa_link", "")
        wa_section = f"\n\n📲 <b>WhatsApp de Entrega Listo (1 Clic):</b>\n{wa_url}" if wa_url else ""

        return (
            f"✅ Venta registrada exitosamente para {acc['client_name']} ({acc['client_code']}):\n"
            f"• Plataforma: {acc['platform']}" + (f" (Perfil: {acc['profile_name']})" if acc.get('profile_name') else "") + "\n"
            f"• Correo: {acc['email']}\n"
            f"• Clave: {acc['password']}" + (f" | PIN: {acc['profile_pin']}" if acc.get('profile_pin') else "") + "\n"
            f"• Vence: {acc['expiry_date']} | Recurrencia: {acc['recurrence']}\n"
            f"• Precio: {acc.get('price') or '-'} | Costo prov: {acc.get('cost') or '-'}\n"
            f"• Tipo: {'👔 Revendedor' if acc.get('client_type') == 'revendedor' else '👤 Consumidor Final'}\n"
            f"• Contacto: WhatsApp: {acc.get('whatsapp') or '-'} | Telegram: {acc.get('telegram') or '-'}"
            f"{wa_section}"
        )
    except Exception as e:
        return f"❌ Error al registrar venta: {str(e)}"

@mcp.tool()
def buscar_cliente(query: str) -> str:
    """Busca un cliente por nombre/alias ('Maik'), código (CLI-001), WhatsApp o Telegram."""
    client = database.search_client(query)
    if not client:
        return f"❌ No se encontró ningún cliente que coincida con '{query}'."

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
def consultar_ficha_cliente(cliente: str) -> str:
    """Consulta la Ficha 360° integral de un cliente: salud de pagos, LTV en ARS, ganancia neta generada, suscripciones activas y link de cobro consolidado."""
    profile = database.get_client_360_profile(cliente)
    if not profile:
        return f"❌ No se encontró ningún cliente con '{cliente}'."

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
def marcar_cuenta_caida(correo_o_id: str, motivo: str = "Suscripción caída") -> str:
    """Marca una cuenta o perfil como 'caida' para colocarla en la lista de reclamos."""
    acc = database.mark_account_fallen(correo_o_id, reason=motivo)
    if not acc:
        return f"❌ No se encontró ninguna cuenta activa con '{correo_o_id}'."

    client_name = acc.get("client_name") or "Sin cliente"
    return (
        f"🚨 Cuenta marcada como CAÍDA:\n"
        f"• Plataforma: {acc['platform']}\n"
        f"• Correo: {acc['email']}\n"
        f"• Cliente afectado: {client_name}\n"
        f"• Motivo: {motivo}\n\n"
        f"💡 Puedes pedirme: 'Cámbiame este correo {acc['email']} por una libre' para asignarle reemplazo automático."
    )

@mcp.tool()
def agregar_stock_libre(
    plataforma: str,
    correo: str,
    contrasena: str,
    perfil: str = "",
    pin: str = "",
    costo: str = "",
    notas: str = ""
) -> str:
    """Agrega una cuenta o perfil libre al inventario disponible para la venta o reemplazo."""
    try:
        acc = database.add_free_account(
            platform=plataforma,
            email=correo,
            password=contrasena,
            profile_name=perfil,
            profile_pin=pin,
            cost=costo,
            notes=notas
        )
        return (
            f"✅ Cuenta libre agregada al stock disponible:\n"
            f"• ID: {acc['id']}\n"
            f"• Plataforma: {acc['platform']}\n"
            f"• Correo: {acc['email']}\n"
            f"• Clave: {acc['password']}" + (f" | Perfil: {acc['profile_name']}" if acc.get("profile_name") else "")
        )
    except Exception as e:
        return f"❌ Error al agregar stock: {str(e)}"

@mcp.tool()
def reemplazar_cuenta_caida(correo_o_id: str) -> str:
    """Reemplazo inteligente de cuenta por una libre de la misma plataforma."""
    res = database.replace_fallen_account(correo_o_id)
    if not res:
        return f"❌ No se encontró ninguna cuenta caída o activa con '{correo_o_id}'."

    if not res.get("success"):
        old = res.get("old_account", {})
        return (
            f"⚠️ NO HAY STOCK DISPONIBLE:\n"
            f"No se encontraron cuentas libres de la plataforma '{old.get('platform')}' en el inventario.\n"
            f"La cuenta {old.get('email')} permanece marcada como caída."
        )

    new_acc = res["new_account"]
    old_acc = res["old_account"]
    client_name = new_acc.get("client_name") or "Cliente"

    wa_data = database.generate_whatsapp_message(new_acc, message_type="reemplazo")
    wa_url = wa_data.get("wa_link", "")
    wa_section = f"\n\n📲 <b>WhatsApp de Reemplazo Listo (1 Clic):</b>\n{wa_url}" if wa_url else ""

    return (
        f"🎉 REEMPLAZO EXITOSO REALIZADO:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Cliente:</b> {client_name}\n"
        f"📺 <b>Plataforma:</b> {new_acc['platform']}\n"
        f"❌ <b>Cuenta anterior (Caída):</b> {old_acc['email']}\n"
        f"✨ <b>NUEVA CUENTA ASIGNADA:</b>\n"
        f"• Correo: <code>{new_acc['email']}</code>\n"
        f"• Contraseña: <code>{new_acc['password']}</code>" + (f"\n• Perfil: {new_acc['profile_name']}" if new_acc.get("profile_name") else "") + (f" [PIN: {new_acc['profile_pin']}]" if new_acc.get("profile_pin") else "") + "\n"
        f"📅 <b>Mantiene vencimiento:</b> <code>{new_acc['expiry_date']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 Copia estos datos y envíaselos a {client_name}."
        f"{wa_section}"
    )

@mcp.tool()
def consultar_stock_libre(plataforma: str = "") -> str:
    """Consulta el inventario de cuentas y perfiles libres listos para entregar."""
    stock = database.get_free_stock(plataforma if plataforma else None)
    if not stock:
        msg = f"de la plataforma '{plataforma}'" if plataforma else "en el inventario"
        return f"📦 No hay cuentas libres disponibles {msg}."

    lines = [f"📦 <b>Stock Disponible ({len(stock)} cuentas libres):</b>\n"]
    for s in stock:
        perf = f" (Perfil: {s['profile_name']})" if s.get("profile_name") else ""
        pin = f" [PIN: {s['profile_pin']}]" if s.get("profile_pin") else ""
        lines.append(f"• [{s['id']}] {s['platform']}{perf}: <code>{s['email']}</code> | Clave: <code>{s['password']}</code>{pin}")

    return "\n".join(lines)

@mcp.tool()
def consultar_alerta_stock_bajo() -> str:
    """Diagnostica la salud del stock y alerta si hay plataformas agotadas o por debajo del umbral mínimo de seguridad."""
    summary = database.get_stock_health_summary()
    platforms = summary.get("platforms", [])

    if not platforms:
        return "📦 No hay plataformas ni cuentas registradas en el catálogo."

    lines = [
        "📦 <b>DIAGNÓSTICO DE SALUD DEL INVENTARIO</b>\n"
    ]

    if summary.get("has_alerts"):
        lines.append("⚠️ <b>¡ATENCIÓN! Se detectaron plataformas que requieren reposición urgente:</b>")
    else:
        lines.append("✅ <b>¡Todo el inventario está saludable! Ninguna plataforma está en nivel crítico.</b>")

    for p in platforms:
        plat = p["platform"]
        free = p["free_count"]
        thresh = p["min_threshold"]
        occupied = p["occupied_count"]

        if p["status"] == "agotado":
            lines.append(f"🔴 <b>{plat}:</b> ¡AGOTADO! 0 disponibles | Mínimo: {thresh} | Activas: {occupied}")
        elif p["status"] == "bajo":
            lines.append(f"🟡 <b>{plat}:</b> STOCK BAJO ({free} disponibles) | Mínimo: {thresh} | Activas: {occupied}")
        else:
            lines.append(f"🟢 <b>{plat}:</b> Stock Óptimo ({free} disponibles) | Mínimo: {thresh}")

    lines.append(f"\n📊 <b>Total Unidades Libres:</b> {summary['total_free_units']} en {summary['total_platforms']} plataformas.")
    if summary["out_of_stock_count"] > 0:
        lines.append(f"🚨 {summary['out_of_stock_count']} plataforma(s) con 0 stock (Agotadas).")
    if summary["low_stock_count"] > 0:
        lines.append(f"⚠️ {summary['low_stock_count']} plataforma(s) en umbral crítico.")

    return "\n".join(lines)

@mcp.tool()
def configurar_umbral_stock(plataforma: str, umbral_minimo: int) -> str:
    """Configura la cantidad mínima de cuentas/perfiles en stock para alertar cuando escaseen (ej: plataforma='Netflix', umbral_minimo=3)."""
    clean_plat = plataforma.strip()
    if umbral_minimo < 0:
        return "❌ El umbral mínimo no puede ser negativo."
    ok = database.set_platform_min_stock(clean_plat, umbral_minimo)
    if ok:
        return f"✅ Umbral mínimo de stock para '{clean_plat}' configurado en {umbral_minimo} unidades."
    return "❌ Error al guardar el umbral de stock."

@mcp.tool()
async def enviar_alerta_stock_telegram() -> str:
    """Envía inmediatamente el reporte visual con semáforo de stock (🔴/🟡/🟢) al bot de Telegram."""
    from telegram_bot import format_and_send_stock_alert
    ok = await format_and_send_stock_alert()
    if ok:
        return "✅ Reporte y alerta de stock enviado exitosamente a Telegram."
    return "❌ Error: Verifica la configuración del bot de Telegram."

@mcp.tool()
def consultar_cuentas_caidas() -> str:
    """Lista todas las cuentas marcadas como caídas pendientes de reclamo."""
    fallen = database.get_fallen_accounts()
    if not fallen:
        return "🎉 ¡Excelente! No hay ninguna cuenta caída actualmente."

    lines = [f"🚨 <b>Cuentas Caídas Pendientes ({len(fallen)}):</b>\n"]
    for f in fallen:
        client_name = f.get("client_name") or "Sin cliente asignado"
        lines.append(
            f"• [{f['id']}] {f['platform']}: {f['email']}\n"
            f"  Cliente: {client_name} | Tel: {f.get('whatsapp') or '-'} | Tg: {f.get('telegram') or '-'}\n"
            f"  Detalle: {f.get('notes') or '-'}\n"
        )
    return "\n".join(lines)

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
def cambiar_clave_admin(nueva_contrasena: str, usuario: str = "admin") -> str:
    """Cambia o restablece la contraseña de acceso al panel web del administrador."""
    try:
        clean_user = usuario.strip().lower()
        clean_pass = nueva_contrasena.strip()
        database.create_or_update_admin(clean_user, clean_pass)
        return f"✅ Contraseña del usuario '{clean_user}' actualizada correctamente."
    except Exception as e:
        return f"❌ Error al actualizar contraseña: {str(e)}"

@mcp.tool()
async def enviar_alerta_prueba_telegram(mensaje: str = "Prueba de conexión con Gemini MCP Bot") -> str:
    """Envía un mensaje de prueba al chat de Telegram configurado."""
    text = (
        "🤖 <b>Test de Conexión Gemini MCP</b>\n\n"
        f"{mensaje}\n\n"
        "✅ ¡Si recibes este mensaje, la integración de Telegram está funcionando al 100%!"
    )
    ok = await send_telegram_message(text)
    if ok:
        return "✅ Mensaje de prueba enviado exitosamente a Telegram."
    return "❌ Error: Verifica que TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID estén configurados correctamente."

@mcp.tool()
async def verificar_vencimientos_ahora(dias_anticipacion: int = 7) -> str:
    """Ejecuta una comprobación inmediata de vencimientos de streaming y envía alertas con datos de contacto y botones de acción rápida por Telegram."""
    enviadas = await check_and_send_alerts(days_window=dias_anticipacion, force=True)
    return f"Comprobación manual completada. Se enviaron {enviadas} alerta(s) de vencimiento interactivas por Telegram."

@mcp.tool()
def exportar_resumen_csv(tipo: str = "activas") -> str:
    """Genera y retorna una planilla en formato CSV para Excel. tipo puede ser: 'activas', 'stock', o 'transacciones'."""
    clean_type = tipo.strip().lower()
    if "stock" in clean_type:
        return database.export_free_stock_csv()
    elif "trans" in clean_type or "pago" in clean_type or "balance" in clean_type:
        return database.export_transactions_csv()
    else:
        return database.export_active_accounts_csv()

@mcp.tool()
def importar_stock_desde_csv(contenido_csv: str) -> str:
    """Carga masivamente cuentas y perfiles libres a partir de un texto o contenido CSV/Excel (separado por comas, puntos y comas o tabulaciones)."""
    res = database.import_free_stock_csv(contenido_csv)
    if not res.get("success"):
        return f"❌ Error en la importación: {res.get('error')}"
    err_msg = f" (Errores en {len(res['errors'])} líneas: {', '.join(res['errors'][:3])})" if res.get("errors") else ""
    return f"✅ Importación completada: {res['imported']} cuentas agregadas al stock libre, {res['skipped']} omitidas{err_msg}."

@mcp.tool()
def importar_ventas_desde_csv(contenido_csv: str) -> str:
    """Importa masivamente clientes y ventas activas con sus vencimientos desde un archivo o texto CSV/Excel."""
    res = database.import_sales_csv(contenido_csv)
    if not res.get("success"):
        return f"❌ Error en la importación: {res.get('error')}"
    err_msg = f" (Errores en {len(res['errors'])} líneas: {', '.join(res['errors'][:3])})" if res.get("errors") else ""
    return f"✅ Importación de ventas completada: {res['imported']} suscripciones y clientes asignados con éxito, {res['skipped']} omitidas{err_msg}."

@mcp.tool()
async def enviar_backup_telegram() -> str:
    """Genera y envía automáticamente copias de seguridad en formato Excel/CSV (cuentas activas, stock y balance) como archivos descargables al bot de Telegram."""
    from telegram_bot import send_full_backup_to_telegram
    ok = await send_full_backup_to_telegram()
    if ok:
        return "✅ Copia de seguridad en Excel/CSV generada y enviada a tu chat de Telegram."
    return "❌ Error al generar o enviar la copia de seguridad por Telegram."

# ==========================================
# Herramientas de Catálogo de Precios y Combos (Paso 7 - v2.8.0)
# ==========================================
@mcp.tool()
def consultar_catalogo_precios() -> str:
    """Muestra el catálogo oficial de precios en Pesos Argentinos (ARS) por plataforma, distinguiendo precio a Consumidor Final, Revendedor y Costo Mayorista."""
    catalog = database.get_price_catalog()
    if not catalog:
        return "El catálogo de precios está vacío. Usa 'configurar_precio_catalogo' para agregar plataformas."

    lines = ["🏷️ <b>CATÁLOGO OFICIAL DE PRECIOS (ARS):</b>\n"]
    for c in catalog:
        stype = "📱 Pantalla" if c["service_type"] == "pantalla" else "👑 Completa"
        lines.append(
            f"• <b>{c['platform']}</b> ({stype})\n"
            f"  Costo Prov: {c['cost_price_formatted']} | Final: <b>{c['price_final_formatted']}</b> | Revendedor: <b>{c['price_reseller_formatted']}</b>\n"
            f"  Margen Ganancia: +{database.format_ars(c['profit_final'])} (Final) / +{database.format_ars(c['profit_reseller'])} (Rev.)\n"
        )
    return "\n".join(lines)

@mcp.tool()
def configurar_precio_catalogo(
    plataforma: str,
    precio_final_ars: float,
    precio_revendedor_ars: float,
    costo_ars: float = 0.0,
    tipo_servicio: str = "pantalla",
    notas: str = ""
) -> str:
    """Configura o actualiza el precio oficial y costo en Pesos Argentinos (ARS) para una plataforma:
    - plataforma: Netflix 4K, Disney+, Max, etc.
    - precio_final_ars: Precio para el cliente consumidor final en ARS (ej: 5500).
    - precio_revendedor_ars: Precio mayorista para revendedores en ARS (ej: 4200).
    - costo_ars: Costo de compra ante el proveedor en ARS (ej: 3200).
    - tipo_servicio: 'pantalla' (individual) o 'cuenta_completa'.
    """
    res = database.upsert_catalog_price(
        platform=plataforma,
        service_type=tipo_servicio,
        cost_price=costo_ars,
        price_final=precio_final_ars,
        price_reseller=precio_revendedor_ars,
        notes=notas
    )
    return (
        f"✅ PRECIO DE CATÁLOGO GUARDADO CON ÉXITO:\n"
        f"• Plataforma: {res['platform']} ({res['service_type']})\n"
        f"• Precio Consumidor Final: {database.format_ars(res['price_final'])}\n"
        f"• Precio Revendedor: {database.format_ars(res['price_reseller'])}\n"
        f"• Costo Proveedor: {database.format_ars(res['cost_price'])}\n"
        f"💡 Este precio se aplicará automáticamente a nuevas ventas si no se especifica un monto particular."
    )

@mcp.tool()
def listar_combos() -> str:
    """Lista todos los packs promocionales o combos configurados con sus precios en ARS y plataformas incluidas."""
    combos = database.get_combos(only_active=True)
    if not combos:
        return "No hay combos promocionales activos configurados. Usa 'crear_o_actualizar_combo' para armar uno."

    lines = ["📦 <b>PACKS Y COMBOS ACTIVOS (ARS):</b>\n"]
    for cb in combos:
        lines.append(
            f"🔹 <b>{cb['name']}</b>\n"
            f"  Plataformas: {cb['platforms_str']}\n"
            f"  Precio Final: <b>{cb['price_final_formatted']}</b> | Revendedor: <b>{cb['price_reseller_formatted']}</b>\n"
            f"  Descripción: {cb.get('description') or 'Sin descripción'}\n"
        )
    return "\n".join(lines)

@mcp.tool()
def crear_o_actualizar_combo(
    nombre: str,
    plataformas: str,
    precio_final_ars: float,
    precio_revendedor_ars: float,
    descripcion: str = ""
) -> str:
    """Crea o actualiza un combo o pack promocional:
    - nombre: Nombre del combo (ej: 'Dúo Cine Netflix + Disney').
    - plataformas: Lista de plataformas separadas por coma (ej: 'Netflix 4K, Disney+ Premium').
    - precio_final_ars: Precio total del combo en ARS para consumidor final (ej: 8500).
    - precio_revendedor_ars: Precio en ARS para revendedor (ej: 6800).
    - descripcion: Breve detalle o condiciones del pack.
    """
    plat_list = [p.strip() for p in plataformas.split(",") if p.strip()]
    if not plat_list:
        return "❌ Debes especificar al menos una plataforma para el combo."
    res = database.create_or_update_combo(
        name=nombre,
        description=descripcion,
        price_final=precio_final_ars,
        price_reseller=precio_revendedor_ars,
        platforms=plat_list
    )
    return (
        f"✅ COMBO '{res['name']}' CONFIGURADO CON ÉXITO:\n"
        f"• Plataformas: {', '.join(plat_list)}\n"
        f"• Precio Final: {database.format_ars(precio_final_ars)}\n"
        f"• Precio Revendedor: {database.format_ars(precio_revendedor_ars)}\n"
        f"🎉 Ya está disponible para ser vendido en 1 clic con la herramienta 'vender_combo'."
    )

@mcp.tool()
def vender_combo(
    nombre_o_id_combo: str,
    cliente: str,
    whatsapp: str = "",
    telegram: str = "",
    tipo_cliente: str = "consumidor_final",
    metodo_pago: str = "Transferencia",
    dias_validez: int = 30,
    notas: str = ""
) -> str:
    """Vende un combo promocional en un solo paso:
    Descuenta de stock libre las cuentas/pantallas de cada plataforma requerida, registra el cobro consolidado en ARS y genera el enlace de WhatsApp listo para entregar.
    - nombre_o_id_combo: Nombre o ID del combo (ej: 'Combo Dúo Cine (Netflix + Disney+)').
    - cliente: Nombre del comprador.
    - whatsapp / telegram: Contacto del cliente.
    - tipo_cliente: 'consumidor_final' o 'revendedor'.
    - metodo_pago: 'Transferencia', 'Mercado Pago', 'Efectivo', etc.
    - dias_validez: Duración del servicio (por defecto 30 días).
    """
    res = database.sell_combo(
        combo_name_or_id=nombre_o_id_combo,
        client_name=cliente,
        whatsapp=whatsapp,
        telegram=telegram,
        client_type=tipo_cliente,
        payment_method=metodo_pago,
        duration_days=dias_validez,
        notes=notas
    )
    if not res.get("success"):
        return f"❌ {res.get('error')}"

    accs_summary = "\n".join([f"  • {a['platform']}: {a['email']} (Perfil: {a.get('profile_name') or 'Único'})" for a in res['accounts']])
    return (
        f"🎉 COMBO VENDIDO Y ASIGNADO CON ÉXITO:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Pack:</b> {res['combo_name']}\n"
        f"👤 <b>Cliente:</b> {res['client_name']} ({tipo_cliente})\n"
        f"💰 <b>Total Cobrado:</b> {database.format_ars(res['amount'])}\n"
        f"💵 <b>Ganancia Neta:</b> +{database.format_ars(res['profit'])}\n"
        f"📅 <b>Vencimiento:</b> <code>{res['expiry_date']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 <b>Cuentas y Pantallas Asignadas:</b>\n{accs_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 <b>WhatsApp de Entrega Listo (1 Clic):</b>\n{res['wa_link']}"
    )

@mcp.tool()
def consultar_plantillas_whatsapp() -> str:
    """Lista todas las plantillas de mensajes de WhatsApp configuradas en el sistema (cobro individual, cobro consolidado, entrega de accesos y reemplazo por caída)."""
    templates = database.get_whatsapp_templates()
    lines = ["📝 <b>PLANTILLAS DE WHATSAPP CONFIGURADAS:</b>\n"]
    for k, t in templates.items():
        lines.append(
            f"🔹 <b>{t['title']}</b> (Clave: <code>{k}</code>)\n"
            f"  Descripción: {t.get('description') or 'Sin descripción'}\n"
            f"  Última modificación: {t.get('updated_at') or 'Predeterminada'}\n"
            f"  Contenido:\n<pre>{t['content']}</pre>\n"
        )
    return "\n".join(lines)

@mcp.tool()
def guardar_plantilla_whatsapp(
    clave: str,
    contenido: str,
    titulo: str = "",
    descripcion: str = ""
) -> str:
    """Modifica y guarda una plantilla de WhatsApp del sistema.
    - clave: 'cobro', 'cobro_consolidado', 'entrega' o 'reemplazo'.
    - contenido: Texto del mensaje con etiquetas dinámicas {cliente}, {plataforma}, {email}, {password}, {perfil}, {pin}, {vencimiento}, {monto}, {metodos_pago}, etc.
    - titulo: Título descriptivo opcional de la plantilla.
    - descripcion: Breve detalle opcional sobre el uso de la plantilla.
    """
    ok = database.save_whatsapp_template(clave, contenido, title=titulo, description=descripcion)
    if not ok:
        return f"❌ No se pudo guardar la plantilla con clave '{clave}'."
    return f"✅ Plantilla '{clave}' actualizada correctamente con éxito. Se aplicará a todos los mensajes generados a partir de ahora."

@mcp.tool()
def restaurar_plantilla_whatsapp(clave: str) -> str:
    """Restaura una plantilla de WhatsApp a su texto predeterminado original de fábrica.
    - clave: 'cobro', 'cobro_consolidado', 'entrega' o 'reemplazo'.
    """
    ok = database.reset_whatsapp_template(clave)
    if not ok:
        return f"❌ Clave '{clave}' no reconocida o error al restaurar."
    return f"🔄 Plantilla '{clave}' restaurada exitosamente a sus valores originales de fábrica."

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
def consultar_logs_sistema(
    nivel: str = "ALL",
    modulo: str = "",
    query: str = "",
    limite: int = 50
) -> str:
    """Consulta los logs y eventos recientes del sistema para diagnóstico y depuración de errores.
    - nivel: 'ALL', 'ERROR', 'WARNING', 'INFO'.
    - modulo: Filtrar por módulo (ej: 'main', 'telegram_bot', 'scheduler', 'database').
    - query: Término de búsqueda de texto o excepción en los logs.
    - limite: Cantidad máxima de registros a devolver (por defecto 50).
    """
    logs = system_logger.get_recent_logs(level=nivel, module=modulo, query=query, limit=limite)
    if not logs:
        return "📋 No se encontraron logs que coincidan con los filtros especificados."

    lines = [f"📋 <b>LOGS RECIENTES DEL SISTEMA ({len(logs)} eventos):</b>\n"]
    for l in logs:
        ico = "🚨" if l["level"] in ("ERROR", "CRITICAL") else ("⚠️" if l["level"] == "WARNING" else "ℹ️")
        lines.append(f"{ico} <code>{l['timestamp']}</code> [<b>{l['level']}</b>] [<b>{l['module']}</b>]: {l['message']}")
        if l.get("traceback"):
            lines.append(f"<pre>{l['traceback'][:500]}</pre>")
    return "\n".join(lines)

@mcp.tool()
def diagnostico_salud_sistema() -> str:
    """Devuelve un informe integral del estado de salud del sistema (base de datos, bot de Telegram, scheduler y resumen de errores recientes)."""
    rep = system_logger.get_system_health_report()
    db = rep["database"]
    ls = rep["logs_summary"]
    status_ico = "🟢" if rep["status"] == "OK" else ("🟡" if rep["status"] == "WARNING" else "🔴")

    out = [
        f"{status_ico} <b>REPORTE DE SALUD DEL SISTEMA ({rep['status']}):</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"⏱️ <b>Tiempo Activo (Uptime):</b> {rep['uptime']}",
        f"🗄️ <b>Base de Datos:</b> {db['status']} ({db['size']} | {db['total_accounts']} cuentas)",
        f"🤖 <b>Bot Telegram:</b> {rep['telegram']['status']}",
        f"⏰ <b>Scheduler Alertas:</b> {rep['scheduler']['status']}",
        f"📊 <b>Buffer de Eventos:</b> {ls['total_buffered']}",
        f"⚠️ <b>Advertencias:</b> {ls['warnings_count']} | 🚨 <b>Errores:</b> {ls['errors_count']}",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    if ls.get("last_error"):
        le = ls["last_error"]
        out.append(f"🚨 <b>Último Error Registrado ({le['timestamp']}) en [{le['module']}]:</b>\n<code>{le['message']}</code>")
    else:
        out.append("✨ <i>Todos los servicios operan con normalidad sin errores recientes.</i>")
    return "\n".join(out)

@mcp.tool()
def listar_proveedores() -> str:
    """Lista todos los proveedores mayoristas registrados, cuentas contratadas y total pagado en ARS."""
    sups = database.get_suppliers()
    if not sups:
        return "🏢 No hay proveedores mayoristas registrados actualmente. Usa 'crear_o_actualizar_proveedor' para dar de alta uno."

    lines = ["🏢 <b>PROVEEDORES MAYORISTAS REGISTRADOS:</b>\n"]
    for s in sups:
        lines.append(
            f"🔹 <b>{s['name']}</b> (ID: <code>{s['id']}</code>)\n"
            f"  📱 Contacto: {s.get('contact') or 'No registrado'}\n"
            f"  💳 Datos de Pago: <code>{s.get('payment_info') or 'Sin datos'}</code>\n"
            f"  📺 Cuentas Madre: <b>{s['master_accounts_count']}</b> ({s['profiles_count']} perfiles)\n"
            f"  💰 Total Pagado: <b>{s['total_spent_formatted']}</b>\n"
            f"  📝 Notas: {s.get('notes') or '-'}\n"
        )
    return "\n".join(lines)

@mcp.tool()
def crear_o_actualizar_proveedor(
    nombre: str,
    contacto: str = "",
    info_pago: str = "",
    notas: str = "",
    id_proveedor: Optional[int] = None
) -> str:
    """Registra o modifica un proveedor mayorista:
    - nombre: Nombre del proveedor o empresa mayorista (ej: 'Streaming Mayorista ARG').
    - contacto: Teléfono WhatsApp o usuario de Telegram.
    - info_pago: CBU, CVU, Alias o Binance USDT para abonarle las cuentas.
    - notas: Condiciones de garantía, horarios o acuerdos.
    - id_proveedor: ID si se desea editar uno existente.
    """
    res = database.save_supplier(
        supplier_id=id_proveedor,
        name=nombre,
        contact=contacto,
        payment_info=info_pago,
        notes=notas
    )
    act = "actualizado" if id_proveedor else "creado"
    return (
        f"✅ PROVEEDOR {act.upper()} CON ÉXITO:\n"
        f"• ID: <code>{res['id']}</code>\n"
        f"• Nombre: <b>{res['name']}</b>\n"
        f"• Contacto: {res.get('contact') or '-'}\n"
        f"• Info de Pago: <code>{res.get('payment_info') or '-'}</code>"
    )

@mcp.tool()
def consultar_cuentas_madre(solo_con_riesgo: bool = False) -> str:
    """Monitorea las cuentas madre ante proveedores mayoristas, reportando fecha de vencimiento y detectando si algún cliente vence después que la cuenta (Riesgo de Corte).
    - solo_con_riesgo: Si es True, filtra únicamente las cuentas con alerta de desfase o próximas a vencer en <= 3 días.
    """
    masters = database.get_master_accounts_overview()
    if not masters:
        return "📺 No hay cuentas registradas en el sistema."

    if solo_con_riesgo:
        masters = [m for m in masters if m["risk_mismatch"] or (m["days_remaining_supplier"] is not None and m["days_remaining_supplier"] <= 3)]
        if not masters:
            return "✅ ¡Excelente! No hay cuentas madre con riesgo de corte ni próximas a vencer."

    lines = ["🏢 <b>ESTADO DE CUENTAS MADRE ANTE PROVEEDORES:</b>\n"]
    for m in masters:
        risk_ico = "⚠️ " if m["risk_mismatch"] else ""
        lines.append(
            f"{risk_ico}📺 <b>{m['platform']}</b> - <code>{m['email']}</code>\n"
            f"  👔 Proveedor: <b>{m['supplier_name']}</b>\n"
            f"  📅 Vencimiento Mayorista: <code>{m['supplier_expiry_date'] or 'Sin fecha'}</code> ({m['status_label']})\n"
            f"  👥 Perfiles: {m['profiles_occupied']} ocupados / {m['profiles_total']} totales\n"
            f"  💵 Costo Mayorista: {m['supplier_cost_formatted']}\n"
        )
        if m["risk_mismatch"]:
            lines.append(f"  🚨 <b>ALERTA:</b> {m['mismatch_warning']}\n")
    return "\n".join(lines)

@mcp.tool()
def renovar_cuenta_madre(
    correo: str,
    plataforma: str,
    nueva_fecha_vence: str,
    costo_ars: float = 0.0,
    metodo_pago: str = "Transferencia",
    notas: str = ""
) -> str:
    """Renueva una cuenta madre actualizando su fecha de vencimiento ante el proveedor en todos sus perfiles y asentando el costo en las finanzas del negocio.
    - correo: Email de la cuenta principal.
    - plataforma: Nombre del servicio (ej: 'Netflix 4K').
    - nueva_fecha_vence: Nueva fecha de corte con el proveedor (formato YYYY-MM-DD).
    - costo_ars: Importe pagado al proveedor en Pesos Argentinos (ARS).
    - metodo_pago: 'Transferencia', 'Mercado Pago', 'Binance USDT', etc.
    - notas: Detalle adicional o comprobante.
    """
    res = database.renew_master_account(
        email=correo,
        platform=plataforma,
        new_supplier_expiry=nueva_fecha_vence,
        cost=costo_ars,
        payment_method=metodo_pago,
        notes=notes
    )
    return (
        f"✅ CUENTA MADRE RENOVADA CON ÉXITO:\n"
        f"• Plataforma: <b>{res['platform']}</b>\n"
        f"• Correo: <code>{res['email']}</code>\n"
        f"• Nuevo Vencimiento Mayorista: <code>{res['new_supplier_expiry']}</code>\n"
        f"• Costo Registrado: {res['cost_formatted']} ({metodo_pago})\n"
        f"🎉 Todos los perfiles vinculados quedaron sincronizados y el egreso asentado en el balance financiero."
    )




# --- Herramientas Evolution API & WhatsApp Bot (Paso 5) ---
@mcp.tool()
async def consultar_estado_whatsapp() -> str:
    """Verifica el estado de conexión de la instancia de WhatsApp en Evolution API (Conectado / Desconectado / QR pendiente)."""
    st = await whatsapp_client.check_connection_status()
    cfg = whatsapp_client.get_evolution_config()
    
    if st.get("connected"):
        return (
            f"🟢 <b>WHATSAPP CONECTADO Y OPERATIVO:</b>\n"
            f"• Instancia: <code>{st.get('instance')}</code>\n"
            f"• Estado: <b>En línea (Open)</b>\n"
            f"• Servidor API: <code>{cfg['api_url']}</code>\n"
            f"• Auto-Cobro diario (09:00 AM): {'✅ Activado' if cfg['auto_send_expiry'] else '⏸️ Desactivado'}\n"
            f"• Auto-Envío en ventas: {'✅ Activado' if cfg['auto_send_sales'] else '⏸️ Desactivado'}\n"
            f"• Auto-Respuesta / Webhook: {'✅ Activado' if cfg['auto_reply_enabled'] else '⏸️ Desactivado'}"
        )
    else:
        err = st.get("error") or "No conectado"
        return (
            f"🔴 <b>WHATSAPP DESCONECTADO:</b>\n"
            f"• Instancia: <code>{st.get('instance')}</code>\n"
            f"• Estado actual: <code>{st.get('state')}</code>\n"
            f"• Detalle: {err}\n\n"
            f"👉 Abre la pestaña '💬 WhatsApp & Mensajería' en el panel web para escanear el código QR."
        )

@mcp.tool()
async def enviar_whatsapp_cliente(
    destinatario: str = "",
    mensaje: str = "",
    telefono: str = "",
    delay_segundos: float = 2.0
) -> str:
    """Envía un mensaje de texto por WhatsApp directamente a un cliente usando Evolution API:
    - destinatario: Puede ser el NOMBRE o alias del cliente registrado (ej: 'Juan Prueba Ortiz', 'Maik', 'CLI-001') o directamente su número de teléfono (+549...).
    - mensaje: Texto del mensaje a enviar.
    - telefono: (Opcional) Número del cliente si no se especificó en destinatario.
    - delay_segundos: Simulación de escritura anti-baneo en segundos (por defecto 2.0).
    """
    target = (destinatario or telefono or "").strip()
    if not target:
        return "❌ Error: Debes indicar el nombre del cliente o su número de teléfono."
    if not mensaje.strip():
        return "❌ Error: El mensaje a enviar no puede estar vacío."

    client_name_str = ""
    phone_to_send = target

    # Si contiene letras o parece un nombre/código en vez de solo números
    clean_digits = re.sub(r'[^0-9]', '', target)
    if re.search(r'[a-zA-Z]', target) or len(clean_digits) < 8:
        client = database.search_client(target)
        if not client:
            return f"❌ No se encontró ningún cliente en el sistema con el nombre o código '{target}'."
        phone_reg = client.get("whatsapp")
        if not phone_reg:
            return f"❌ El cliente '{client.get('name')}' ({client.get('client_code')}) está registrado pero no tiene número de WhatsApp configurado."
        phone_to_send = phone_reg
        client_name_str = f" a {client.get('name')}"

    res = await whatsapp_client.send_text_message(phone_to_send, mensaje, delay_seconds=delay_segundos)
    if res.get("success"):
        return f"✅ Mensaje de WhatsApp enviado exitosamente{client_name_str} ({res.get('phone')}) (ID: {res.get('message_id')})."
    else:
        return f"❌ Error al enviar WhatsApp{client_name_str} ({phone_to_send}): {res.get('error')}"

@mcp.tool()
def configurar_automatizacion_whatsapp(
    api_url: str = "",
    api_key: str = "",
    instance_name: str = "",
    auto_send_expiry: Optional[int] = None,
    auto_send_sales: Optional[int] = None,
    auto_reply_enabled: Optional[int] = None
) -> str:
    """Configura las opciones de Evolution API WhatsApp y las automatizaciones del sistema:
    - api_url: URL base de Evolution API (ej: http://evolution-api:8080).
    - api_key: Clave API global de autenticación.
    - instance_name: Nombre de la sesión/instancia (ej: streaming-bot).
    - auto_send_expiry: 1 para activar envío 100% automático de cobranzas a las 09:00 AM, 0 para desactivar.
    - auto_send_sales: 1 para despachar credenciales por WhatsApp automáticamente al vender combos, 0 para desactivar.
    - auto_reply_enabled: 1 para activar bot de auto-respuesta a consultas de clientes, 0 para desactivar.
    """
    current = database.get_whatsapp_api_settings()
    new_url = api_url.strip() if api_url else current.get("api_url", "http://evolution-api:8080")
    new_key = api_key.strip() if api_key else current.get("api_key", "mcp-evolution-key-2026")
    new_inst = instance_name.strip() if instance_name else current.get("instance_name", "streaming-bot")
    new_expiry = auto_send_expiry if auto_send_expiry is not None else current.get("auto_send_expiry", 0)
    new_sales = auto_send_sales if auto_send_sales is not None else current.get("auto_send_sales", 0)
    new_reply = auto_reply_enabled if auto_reply_enabled is not None else current.get("auto_reply_enabled", 1)

    database.save_whatsapp_api_settings(
        api_url=new_url,
        api_key=new_key,
        instance_name=new_inst,
        auto_send_expiry=new_expiry,
        auto_send_sales=new_sales,
        auto_reply_enabled=new_reply
    )
    return (
        f"✅ CONFIGURACIÓN DE WHATSAPP ACTUALIZADA:\n"
        f"• URL: <code>{new_url}</code> | Instancia: <code>{new_inst}</code>\n"
        f"• Auto-Cobro diario (09:00 AM): {'Activado' if new_expiry else 'Desactivado'}\n"
        f"• Auto-Envío en ventas: {'Activado' if new_sales else 'Desactivado'}\n"
        f"• Bot Auto-Respuesta: {'Activado' if new_reply else 'Desactivado'}"
    )

# ==========================================
# 2. Servidor Web FastAPI & Lifespan
# ==========================================
mcp_app = mcp.http_app(path="/")

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    
    raw_user = os.getenv("ADMIN_USERNAME")
    raw_pass = os.getenv("ADMIN_PASSWORD")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    admin_pass = raw_pass.strip() if raw_pass and raw_pass.strip() else "admin123"

    existing = database.get_admin_user(admin_user)
    totp_secret = existing.get("totp_secret") if existing else pyotp.random_base32()
    database.create_or_update_admin(admin_user, admin_pass, totp_secret)
    logger.info(f"Usuario administrador '{admin_user}' sincronizado con éxito.")

    start_scheduler()
    start_telegram_polling()
    logger.info("Aplicación, tareas programadas y Telegram Polling interactivo iniciados.")
    async with mcp_app.lifespan(app):
        yield
    stop_telegram_polling()
    stop_scheduler()
    logger.info("Aplicación detenida.")

app = FastAPI(
    title="Gemini Streaming CRM & Financial Bot",
    description="Servidor MCP para Gemini Spark y CRM de Streaming con Finanzas y 2FA",
    version="2.8.0",
    lifespan=lifespan
)

app.mount("/mcp", mcp_app)

# ==========================================
# Middleware de Protección OAuth 2.0 para /mcp
# ==========================================
@app.middleware("http")
async def mcp_oauth_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/mcp"):
        # Permitir discovery público (.well-known)
        if "/.well-known/" in path:
            return await call_next(request)
        
        # Extraer token Bearer
        auth_header = request.headers.get("Authorization", "").strip()
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.query_params.get("access_token", "").strip()
        
        oauth_cfg = database.get_oauth_settings()
        if oauth_cfg.get("enabled", 1):
            token_data = database.verify_oauth_access_token(token) if token else None
            if not token_data:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "unauthorized",
                        "message": "Acceso protegido por OAuth 2.0. Se requiere token Bearer válido generado con Client ID y Client Secret."
                    },
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token", error_description="The access token is missing or invalid"'}
                )
    return await call_next(request)

# ==========================================
# Endpoints de Descubrimiento OAuth 2.0 (RFC 8414)
# ==========================================
@app.get("/.well-known/oauth-authorization-server")
@app.get("/mcp/.well-known/oauth-authorization-server")
@app.get("/.well-known/openid-configuration")
@app.get("/mcp/.well-known/openid-configuration")
async def oauth_discovery(request: Request):
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", request.url.netloc)
    server_origin = f"{proto}://{host}"
    
    return {
        "issuer": server_origin,
        "authorization_endpoint": f"{server_origin}/oauth/authorize",
        "token_endpoint": f"{server_origin}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "scopes_supported": ["mcp"]
    }

# ==========================================
# Plantilla y Endpoints de Autorización OAuth
# ==========================================
OAUTH_AUTHORIZE_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Autorizar Gemini Spark - Streaming CRM & MCP</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }
        .oauth-card { background: #161e2e; border: 1px solid #1e293b; border-radius: 16px; padding: 36px; width: 100%; max-width: 460px; box-shadow: 0 15px 35px -5px rgba(0,0,0,0.6); }
        .logo-box { background: #1e293b; width: 64px; height: 64px; border-radius: 16px; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px auto; font-size: 32px; border: 1px solid #38bdf833; }
        h2 { text-align: center; margin: 0 0 8px 0; font-size: 1.35rem; color: #38bdf8; }
        p.desc { text-align: center; color: #94a3b8; font-size: 0.9rem; line-height: 1.5; margin: 0 0 20px 0; }
        .scope-box { background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 16px; margin-bottom: 20px; }
        .scope-item { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; font-size: 0.85rem; color: #cbd5e1; }
        .scope-item:last-child { margin-bottom: 0; }
        .scope-icon { color: #10b981; font-weight: bold; }
        .info-row { font-size: 0.78rem; color: #64748b; margin-top: 12px; padding-top: 10px; border-top: 1px solid #1e293b; word-break: break-all; }
        .btn-group { display: flex; gap: 12px; margin-top: 12px; }
        .btn { flex: 1; padding: 12px; font-size: 0.95rem; font-weight: 600; border-radius: 8px; border: none; cursor: pointer; transition: all 0.2s; text-align: center; text-decoration: none; }
        .btn-primary { background: #0284c7; color: white; }
        .btn-primary:hover { background: #0369a1; }
        .btn-secondary { background: #1e293b; color: #94a3b8; border: 1px solid #334155; }
        .btn-secondary:hover { background: #334155; color: #f1f5f9; }
        .form-group { margin-bottom: 14px; text-align: left; }
        label { display: block; margin-bottom: 6px; font-size: 0.82rem; color: #cbd5e1; font-weight: 500; }
        input[type="text"], input[type="password"] { width: 100%; box-sizing: border-box; background: #0b0f19; border: 1px solid #334155; border-radius: 8px; padding: 10px 12px; color: #fff; font-size: 0.9rem; outline: none; }
        input:focus { border-color: #38bdf8; }
        .alert-error { background: #7f1d1d33; border: 1px solid #ef4444; color: #fca5a5; padding: 10px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 16px; text-align: center; }
        .logged-user { display: inline-flex; align-items: center; gap: 6px; background: #0284c722; color: #38bdf8; border: 1px solid #0284c744; padding: 5px 12px; border-radius: 20px; font-size: 0.82rem; margin-bottom: 16px; }
    </style>
</head>
<body>
    <div class="oauth-card">
        <div class="logo-box">🤖</div>
        <h2>Autorizar Gemini Spark</h2>
        <p class="desc">La aplicación conectada solicita vincularse a tu servidor MCP para gestionar streaming y mensajería.</p>
        
        {USER_BLOCK}

        <div class="scope-box">
            <div class="scope-item">
                <span class="scope-icon">✓</span>
                <div><b>Herramientas MCP:</b> Consultar cuentas, clientes, stock y balance financiero.</div>
            </div>
            <div class="scope-item">
                <span class="scope-icon">✓</span>
                <div><b>WhatsApp & Chatwoot:</b> Despachar cobros y mensajes mediante Evolution API.</div>
            </div>
            <div class="scope-item">
                <span class="scope-icon">✓</span>
                <div><b>Automatizaciones:</b> Asignar perfiles y reemplazar suscripciones caídas con IA.</div>
            </div>
            <div class="info-row">
                <b>ID de Cliente:</b> <code>{CLIENT_ID}</code><br>
                <b>URI Redirección:</b> <code>{REDIRECT_URI}</code>
            </div>
        </div>

        <form method="POST" action="/oauth/authorize">
            <input type="hidden" name="client_id" value="{CLIENT_ID}">
            <input type="hidden" name="redirect_uri" value="{REDIRECT_URI}">
            <input type="hidden" name="state" value="{STATE}">
            <input type="hidden" name="code_challenge" value="{CODE_CHALLENGE}">
            <input type="hidden" name="code_challenge_method" value="{CODE_CHALLENGE_METHOD}">
            <input type="hidden" name="scope" value="{SCOPE}">
            
            {LOGIN_FIELDS}

            <div class="btn-group">
                <button type="submit" name="action" value="deny" class="btn btn-secondary">Cancelar</button>
                <button type="submit" name="action" value="allow" class="btn btn-primary">Autorizar Conexión</button>
            </div>
        </form>
    </div>
</body>
</html>
"""

def render_oauth_authorize_page(
    client_id: str,
    redirect_uri: str,
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "plain",
    scope: str = "",
    is_logged_in: bool = False,
    username: str = "",
    error: str = ""
) -> str:
    user_block = ""
    login_fields = ""
    
    if error:
        user_block += f'<div class="alert-error">⚠️ {error}</div>'
    
    if is_logged_in:
        user_block += f'<div style="text-align:center;"><span class="logged-user">👤 Conectado como: <b>{username}</b></span></div>'
    else:
        login_fields = """
        <div class="form-group">
            <label>Usuario Administrador:</label>
            <input type="text" name="username" required placeholder="admin" autocomplete="username">
        </div>
        <div class="form-group">
            <label>Contraseña:</label>
            <input type="password" name="password" required placeholder="••••••••" autocomplete="current-password">
        </div>
        """
    
    return (
        OAUTH_AUTHORIZE_TEMPLATE
        .replace("{CLIENT_ID}", client_id)
        .replace("{REDIRECT_URI}", redirect_uri)
        .replace("{STATE}", state or "")
        .replace("{CODE_CHALLENGE}", code_challenge or "")
        .replace("{CODE_CHALLENGE_METHOD}", code_challenge_method or "plain")
        .replace("{SCOPE}", scope or "mcp")
        .replace("{USER_BLOCK}", user_block)
        .replace("{LOGIN_FIELDS}", login_fields)
    )

@app.get("/oauth/authorize", response_class=HTMLResponse)
async def oauth_authorize_get(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "code",
    state: str = "",
    scope: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "plain"
):
    oauth_cfg = database.get_oauth_settings()
    clean_client_id = client_id.strip()
    if not clean_client_id or not secrets.compare_digest(clean_client_id, oauth_cfg["client_id"]):
        return HTMLResponse(
            f"<body style='background:#0b0f19;color:#f87171;font-family:sans-serif;padding:40px;text-align:center;'>"
            f"<h2>❌ Error OAuth: Client ID Inválido</h2>"
            f"<p>El ID de cliente recibido ('{clean_client_id}') no coincide con el configurado en tu panel.</p>"
            f"</body>",
            status_code=400
        )
    
    if not redirect_uri.strip():
        return HTMLResponse(
            "<body style='background:#0b0f19;color:#f87171;font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h2>❌ Error OAuth: Falta redirect_uri</h2>"
            "</body>",
            status_code=400
        )

    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    return HTMLResponse(render_oauth_authorize_page(
        client_id=clean_client_id,
        redirect_uri=redirect_uri.strip(),
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        is_logged_in=bool(user),
        username=user or ""
    ))

@app.post("/oauth/authorize")
async def oauth_authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(default=""),
    code_challenge: str = Form(default=""),
    code_challenge_method: str = Form(default="plain"),
    scope: str = Form(default=""),
    action: str = Form(default="allow"),
    username: str = Form(default=""),
    password: str = Form(default="")
):
    oauth_cfg = database.get_oauth_settings()
    clean_client_id = client_id.strip()
    clean_redirect_uri = redirect_uri.strip()
    
    if not secrets.compare_digest(clean_client_id, oauth_cfg["client_id"]):
        raise HTTPException(status_code=400, detail="Client ID inválido")
    
    sep = "&" if "?" in clean_redirect_uri else "?"
    if action != "allow":
        deny_url = f"{clean_redirect_uri}{sep}error=access_denied"
        if state:
            deny_url += f"&state={urllib.parse.quote(state)}"
        return RedirectResponse(deny_url, status_code=302)

    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        clean_u = username.strip().lower()
        if not database.verify_admin_credentials(clean_u, password.strip()):
            return HTMLResponse(
                render_oauth_authorize_page(
                    client_id=clean_client_id,
                    redirect_uri=clean_redirect_uri,
                    state=state,
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                    scope=scope,
                    is_logged_in=False,
                    error="Credenciales de administrador incorrectas."
                ),
                status_code=401
            )
        user = clean_u

    code = database.create_oauth_auth_code(
        client_id=clean_client_id,
        redirect_uri=clean_redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        user_id=user
    )

    redirect_target = f"{clean_redirect_uri}{sep}code={urllib.parse.quote(code)}"
    if state:
        redirect_target += f"&state={urllib.parse.quote(state)}"
    
    return RedirectResponse(redirect_target, status_code=302)

# ==========================================
# Endpoint de Token OAuth 2.0 (RFC 6749)
# ==========================================
@app.post("/oauth/token")
async def oauth_token_endpoint(request: Request):
    content_type = request.headers.get("content-type", "")
    params = {}
    if "application/json" in content_type:
        try:
            params = await request.json()
        except Exception:
            params = {}
    else:
        form = await request.form()
        params = dict(form)

    # Autenticación HTTP Basic Auth
    auth_header = request.headers.get("authorization", "")
    client_id = params.get("client_id", "")
    client_secret = params.get("client_secret", "")
    
    if auth_header.lower().startswith("basic "):
        try:
            b64_creds = auth_header[6:].strip()
            decoded = base64.b64decode(b64_creds).decode("utf-8")
            if ":" in decoded:
                b_id, b_sec = decoded.split(":", 1)
                client_id = client_id or b_id
                client_secret = client_secret or b_sec
        except Exception:
            pass

    grant_type = params.get("grant_type", "authorization_code")
    
    if not database.validate_oauth_client(client_id, client_secret):
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_client", "error_description": "Client ID o Client Secret no válidos."}
        )

    if grant_type == "authorization_code":
        code = params.get("code", "")
        redirect_uri = params.get("redirect_uri", "")
        code_verifier = params.get("code_verifier", "")
        
        auth_data = database.verify_and_consume_auth_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier
        )
        if not auth_data:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "El código de autorización es inválido, ya fue usado o expiró."}
            )
        
        tokens = database.create_oauth_tokens(client_id=client_id, user_id=auth_data.get("user_id", "admin"))
        return JSONResponse(tokens)

    elif grant_type == "refresh_token":
        refresh_token = params.get("refresh_token", "")
        tokens = database.refresh_oauth_token(refresh_token=refresh_token, client_id=client_id)
        if not tokens:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "Refresh token inválido o expirado."}
            )
        return JSONResponse(tokens)

    else:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_grant_type", "error_description": f"Tipo de concesión '{grant_type}' no soportado."}
        )



# ==========================================
# 3. Vistas de Autenticación y 2FA
# ==========================================
LOGIN_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iniciar Sesión - Streaming CRM & MCP</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .login-card { background: #161e2e; border: 1px solid #1e293b; border-radius: 16px; padding: 36px; width: 100%; max-width: 400px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .icon-box { background: #1e293b; width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px auto; font-size: 28px; }
        h2 { text-align: center; margin: 0 0 8px 0; font-size: 1.4rem; color: #38bdf8; }
        p.subtitle { text-align: center; color: #94a3b8; font-size: 0.9rem; margin: 0 0 24px 0; }
        .form-group { margin-bottom: 18px; }
        label { display: block; margin-bottom: 6px; font-size: 0.85rem; color: #cbd5e1; font-weight: 500; }
        input[type="text"], input[type="password"] { width: 100%; box-sizing: border-box; background: #0b0f19; border: 1px solid #334155; border-radius: 8px; padding: 12px 14px; color: #fff; font-size: 0.95rem; outline: none; transition: border-color 0.2s; }
        input:focus { border-color: #38bdf8; }
        .btn { width: 100%; background: #0284c7; color: white; border: none; border-radius: 8px; padding: 12px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; margin-top: 8px; }
        .btn:hover { background: #0369a1; }
        .btn-recovery { width: 100%; background: transparent; border: 1px solid #334155; color: #38bdf8; border-radius: 8px; padding: 10px; font-size: 0.85rem; font-weight: 500; cursor: pointer; transition: all 0.2s; margin-top: 14px; }
        .btn-recovery:hover { background: #1e293b; border-color: #38bdf8; }
        .error-msg { background: #450a0a; border: 1px solid #7f1d1d; color: #fca5a5; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; text-align: center; }
        .success-msg { background: #064e3b; border: 1px solid #047857; color: #6ee7b7; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; text-align: center; }
        .footer { text-align: center; margin-top: 24px; font-size: 0.8rem; color: #64748b; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-box">🔐</div>
        <h2>Acceso Administrativo</h2>
        <p class="subtitle">CRM de Streaming & Gemini MCP</p>
        {{MESSAGE_HTML}}
        <form action="/login" method="POST">
            <div class="form-group">
                <label>Usuario</label>
                <input type="text" name="username" required autofocus placeholder="admin" value="admin">
            </div>
            <div class="form-group">
                <label>Contraseña</label>
                <input type="password" name="password" required placeholder="••••••••">
            </div>
            <button type="submit" class="btn">Continuar</button>
        </form>

        <form action="/recuperar" method="POST">
            <button type="submit" class="btn-recovery">📲 Enviar clave temporal a mi Telegram</button>
        </form>

        <div class="footer">Autenticación de Dos Factores (2FA) Requerida</div>
    </div>
</body>
</html>
"""

TWOFA_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Doble Factor 2FA - Verificación</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .login-card { background: #161e2e; border: 1px solid #1e293b; border-radius: 16px; padding: 36px; width: 100%; max-width: 400px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); text-align: center; }
        .icon-box { background: #1e293b; width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px auto; font-size: 28px; }
        h2 { margin: 0 0 8px 0; font-size: 1.4rem; color: #38bdf8; }
        p.subtitle { color: #94a3b8; font-size: 0.9rem; margin: 0 0 20px 0; line-height: 1.4; }
        .form-group { margin-bottom: 20px; }
        input[type="text"] { width: 100%; box-sizing: border-box; background: #0b0f19; border: 1px solid #334155; border-radius: 8px; padding: 14px; color: #38bdf8; font-size: 1.8rem; font-family: monospace; letter-spacing: 6px; text-align: center; outline: none; transition: border-color 0.2s; }
        input:focus { border-color: #38bdf8; }
        .btn { width: 100%; background: #059669; color: white; border: none; border-radius: 8px; padding: 12px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .btn:hover { background: #047857; }
        .error-msg { background: #450a0a; border: 1px solid #7f1d1d; color: #fca5a5; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 18px; }
        .hint { margin-top: 15px; font-size: 0.85rem; color: #64748b; }
        .back-link { display: inline-block; margin-top: 15px; font-size: 0.85rem; color: #38bdf8; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-box">📲</div>
        <h2>Verificación 2FA</h2>
        <p class="subtitle">Ingresa el código de 6 dígitos que enviamos a tu <b>Telegram</b> (o tu código de Google Authenticator).</p>
        {{ERROR_HTML}}
        <form action="/2fa" method="POST">
            <div class="form-group">
                <input type="text" name="otp_code" maxlength="6" pattern="[0-9]{6}" required autofocus placeholder="000000" autocomplete="off">
            </div>
            <button type="submit" class="btn">Verificar e Ingresar</button>
        </form>
        <div class="hint">El código expira en 5 minutos</div>
        <a href="/login" class="back-link">← Volver al login</a>
    </div>
</body>
</html>
"""

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None, msg: Optional[str] = None):
    session_user = verify_session_cookie(request.cookies.get("session_token"))
    if session_user:
        return RedirectResponse(url="/", status_code=302)
        
    message_html = ""
    if error:
        message_html = f'<div class="error-msg">{error}</div>'
    elif msg:
        message_html = f'<div class="success-msg">{msg}</div>'
        
    return LOGIN_HTML_TEMPLATE.replace("{{MESSAGE_HTML}}", message_html)

@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    user = username.strip().lower()
    passw = password.strip()
    
    if not database.verify_admin_credentials(user, passw):
        return RedirectResponse(url="/login?error=Usuario+o+contrase%C3%B1a+incorrectos", status_code=302)

    otp = f"{secrets.randbelow(900000) + 100000}"
    database.set_telegram_otp(user, otp, duration_seconds=300)

    msg = (
        "🔐 <b>Código de Verificación 2FA</b>\n\n"
        "Alguien está iniciando sesión en el Panel de Streaming.\n\n"
        f"Tu código de acceso es: <code>{otp}</code>\n\n"
        "⏱️ <i>Válido durante 5 minutos.</i>"
    )
    await send_telegram_message(msg)

    preauth = create_preauth_cookie(user)
    response = RedirectResponse(url="/2fa", status_code=302)
    response.set_cookie(
        key="preauth_token",
        value=preauth,
        httponly=True,
        samesite="lax",
        max_age=300
    )
    return response

@app.post("/recuperar")
async def recover_password():
    raw_user = os.getenv("ADMIN_USERNAME")
    admin_user = raw_user.strip().lower() if raw_user and raw_user.strip() else "admin"
    
    temp_pass = f"{secrets.randbelow(900000) + 100000}"
    database.create_or_update_admin(admin_user, temp_pass)
    
    msg = (
        "🔑 <b>Recuperación de Contraseña</b>\n\n"
        "Has solicitado una clave temporal de acceso al Panel Web:\n\n"
        f"• Usuario: <code>{admin_user}</code>\n"
        f"• Contraseña temporal: <code>{temp_pass}</code>\n\n"
        "Ingresa con estos datos en https://mcp.juanconnect.online"
    )
    ok = await send_telegram_message(msg)
    if ok:
        return RedirectResponse(url="/login?msg=Se+envi%C3%B3+tu+clave+temporal+a+Telegram", status_code=302)
    else:
        return RedirectResponse(url="/login?error=Fallo+al+enviar+mensaje+a+Telegram", status_code=302)

@app.get("/2fa", response_class=HTMLResponse)
async def twofa_page(request: Request, error: Optional[str] = None):
    preauth_user = verify_preauth_cookie(request.cookies.get("preauth_token"))
    if not preauth_user:
        return RedirectResponse(url="/login", status_code=302)
        
    error_html = f'<div class="error-msg">{error}</div>' if error else ""
    return TWOFA_HTML_TEMPLATE.replace("{{ERROR_HTML}}", error_html)

@app.post("/2fa")
async def twofa_submit(request: Request, otp_code: str = Form(...)):
    preauth_user = verify_preauth_cookie(request.cookies.get("preauth_token"))
    if not preauth_user:
        return RedirectResponse(url="/login?error=Sesi%C3%B3n+expirada.+Intenta+nuevamente.", status_code=302)

    code = otp_code.strip()
    is_valid = False

    if database.verify_telegram_otp(preauth_user, code):
        is_valid = True
    else:
        admin_data = database.get_admin_user(preauth_user)
        if admin_data and admin_data.get("totp_secret"):
            totp = pyotp.TOTP(admin_data["totp_secret"])
            if totp.verify(code):
                is_valid = True

    if not is_valid:
        return RedirectResponse(url="/2fa?error=C%C3%B3digo+inv%C3%A1lido+o+expirado", status_code=302)

    session = create_session_cookie(preauth_user)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="session_token",
        value=session,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7
    )
    response.delete_cookie("preauth_token")
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_token")
    response.delete_cookie("preauth_token")
    return response


# ==========================================
# 4. Panel de Control Web Completo (CRM + Finanzas)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    active_accounts = database.get_active_accounts()
    free_stock = database.get_free_stock()
    fallen_accounts = database.get_fallen_accounts()
    finance = database.get_financial_balance()
    transactions = database.get_recent_transactions(limit=15)
    catalog_items = database.get_price_catalog()
    combos_list = database.get_combos()
    all_clients_list = database.list_all_clients()
    client_select_options = "".join([f'<option value="{c["id"]}">{c["name"]} ({c.get("client_code") or ""})</option>' for c in all_clients_list])
    payment_settings = database.get_payment_settings()
    whatsapp_templates = database.get_whatsapp_templates()
    formatted_payment_preview = database.get_formatted_payment_methods()
    suppliers_list = database.get_suppliers()
    master_accounts_list = database.get_master_accounts_overview()
    system_health = system_logger.get_system_health_report()
    wa_settings = database.get_whatsapp_api_settings()
    oauth_cfg = database.get_oauth_settings()
    oauth_client_id = oauth_cfg.get("client_id", "gemini-spark-joif")
    oauth_client_secret = oauth_cfg.get("client_secret", "")
    oauth_redirect_uris = oauth_cfg.get("redirect_uris", "https://gemini.google.com")
    oauth_enabled = oauth_cfg.get("enabled", 1)

    msg_raw = request.query_params.get("msg", "")
    wa_param = request.query_params.get("wa", "")
    err_param = request.query_params.get("err", "")
    msg_banner = ""
    if err_param:
        msg_banner = f"""
        <div style="background:#450a0a; border:1px solid #ef4444; color:#fca5a5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>⚠️ <strong>Error:</strong> {err_param}</span>
            <a href="/" style="color:#fca5a5; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """
    elif msg_raw == "combo_sold" and wa_param:
        msg_banner = f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <span>🎉 <strong>¡Combo vendido y asignado con éxito!</strong> Los perfiles quedaron asignados y las cuentas sincronizadas.</span>
            <a href="{wa_param}" target="_blank" class="btn" style="background:#25d366; color:#fff; text-decoration:none; font-weight:bold; padding:8px 16px; border-radius:6px;">📲 Enviar Accesos por WhatsApp (1 Clic)</a>
        </div>
        """
    elif msg_raw:
        msg_text = "¡Cambios guardados con éxito!"
        if msg_raw == "catalog_saved":
            msg_text = "✅ Precio de catálogo guardado correctamente."
        elif msg_raw == "catalog_deleted":
            msg_text = "🗑️ Precio eliminado del catálogo."
        elif msg_raw == "combo_saved":
            msg_text = "✅ Combo promocional guardado correctamente."
        elif msg_raw == "combo_deleted":
            msg_text = "🗑️ Combo promocional eliminado."
        elif msg_raw == "payment_settings_saved":
            msg_text = "✅ Datos de cobro (CBU / Alias / MP) actualizados correctamente."
        elif msg_raw == "template_saved":
            msg_text = "✅ Plantilla de WhatsApp guardada con éxito."
        elif msg_raw == "template_reset":
            msg_text = "🔄 Plantilla restaurada a los valores predeterminados de fábrica."
        elif msg_raw == "supplier_saved":
            msg_text = "✅ Proveedor mayorista guardado correctamente."
        elif msg_raw == "supplier_deleted":
            msg_text = "🗑️ Proveedor mayorista eliminado."
        elif msg_raw == "master_renewed":
            msg_text = "✅ Cuenta madre renovada con éxito y perfiles sincronizados."
        elif msg_raw == "logs_cleared":
            msg_text = "🧹 Buffer de logs en memoria limpiado."
        elif msg_raw == "wa_settings_saved":
            msg_text = "✅ Configuración de Evolution API WhatsApp guardada con éxito."
        elif msg_raw == "wa_test_sent":
            msg_text = "✅ Mensaje de prueba enviado exitosamente por WhatsApp."
        elif msg_raw == "wa_logged_out":
            msg_text = "🚪 Sesión de WhatsApp cerrada correctamente."
        elif msg_raw == "wa_webhook_configured":
            msg_text = "🔗 Webhook configurado exitosamente en Evolution API."
        elif msg_raw == "wa_chatwoot_configured":
            msg_text = "🎉 ¡Chatwoot vinculado exitosamente con Evolution API! Ya puedes gestionar tus clientes desde la app móvil."
        else:
            msg_text = msg_raw
        msg_banner = f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>{msg_text}</span>
            <a href="/" style="color:#a7f3d0; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """

    # 1. Filas de Cuentas Activas con botón de Cobrar y Ficha 360
    active_rows = ""
    for a in active_accounts:
        days = a.get("days_remaining")
        badge = "badge-ok"
        badge_txt = f"En {days}d"
        if days is None:
            badge = "badge-warn"; badge_txt = "Fecha inválida"
        elif days < 0:
            badge = "badge-danger"; badge_txt = f"Vencida (-{abs(days)}d)"
        elif days <= 2:
            badge = "badge-warn"; badge_txt = f"¡Vence en {days}d!"

        wa_clean = re.sub(r'[^0-9]', '', a.get("whatsapp", ""))
        wa_link = f'<a href="https://wa.me/{wa_clean}" target="_blank" style="color: #22c55e;">{a.get("whatsapp")}</a>' if wa_clean else '-'
        tg_clean = a.get("telegram", "").lstrip("@")
        tg_link = f'<a href="https://t.me/{tg_clean}" target="_blank" style="color: #38bdf8;">@{tg_clean}</a>' if tg_clean else '-'
        client_tag = f"👔 {a.get('client_name')}" if "revend" in (a.get("client_type") or "").lower() else f"👤 {a.get('client_name')}"
        client_id_val = a.get("client_id")
        client_click = f'onclick="openClient360Modal({client_id_val})" style="cursor:pointer;color:#38bdf8;text-decoration:underline;" title="Ver Ficha 360° del Cliente"' if client_id_val else ''
        btn_360 = f'<button type="button" onclick="openClient360Modal({client_id_val})" class="btn-action" style="background:#1e293b;border:1px solid #38bdf8;color:#38bdf8;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Ver Ficha 360°">👤 360°</button>' if client_id_val else ''

        perf = f"<br><small style='color:#94a3b8;'>Perf: {a['profile_name']}</small>" if a.get("profile_name") else ""
        pin = f"<small style='color:#94a3b8;'>PIN: {a['profile_pin']}</small>" if a.get("profile_pin") else ""

        wa_cobro = database.generate_whatsapp_message(a, message_type="cobro")
        wa_link_cobro = wa_cobro.get("wa_link", "#")
        wa_entrega = database.generate_whatsapp_message(a, message_type="entrega")
        wa_link_entrega = wa_entrega.get("wa_link", "#")

        active_rows += f"""
        <tr>
            <td><strong {client_click}>{client_tag}</strong><br><small style="color:#64748b;">{a.get('client_code') or ''}</small></td>
            <td>{wa_link}<br>{tg_link}</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{a['platform']}</span>{perf}</td>
            <td><code>{a['email']}</code><br><code>{a['password']}</code> {pin}</td>
            <td><code>{a['expiry_date']}</code></td>
            <td><span class="badge {badge}">{badge_txt}</span></td>
            <td><strong>{a.get('price') or '-'}</strong></td>
            <td style="white-space: nowrap;">
                {btn_360}
                <a href="{wa_link_cobro}" target="_blank" class="btn-action" style="background:#15803d;color:white;text-decoration:none;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Abrir chat de WhatsApp con mensaje de cobro listo">💬 Cobro</a>
                <a href="{wa_link_entrega}" target="_blank" class="btn-action" style="background:#0284c7;color:white;text-decoration:none;display:inline-block;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Abrir chat de WhatsApp con credenciales listas">📩 Datos</a>
                <form action="/api/collect-payment/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Registrar cobro y renovar 30 días para {a['client_name']}?');">
                    <button type="submit" class="btn-action" style="background:#059669;color:white;border:none;padding:4px 7px;border-radius:5px;font-size:0.75rem;font-weight:600;" title="Registrar cobro y renovar">💵 Pagó</button>
                </form>
                <form action="/api/mark-fallen/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Marcar {a['email']} como caída?');">
                    <button type="submit" class="btn-action btn-warn" style="padding:4px 7px;border-radius:5px;font-size:0.75rem;" title="Reportar Caída">🚨</button>
                </form>
                <form action="/api/delete-account/{a['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar cuenta?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;padding:4px 7px;border-radius:5px;font-size:0.75rem;" title="Eliminar">🗑️</button>
                </form>
            </td>
        </tr>
        """
    if not active_rows:
        active_rows = "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas activas asignadas actualmente.</td></tr>"

    # 2. Filas de Stock Libre
    stock_rows = ""
    for s in free_stock:
        perf = f" (Perf: {s['profile_name']})" if s.get("profile_name") else ""
        pin = f" [PIN: {s['profile_pin']}]" if s.get("profile_pin") else ""
        stock_rows += f"""
        <tr>
            <td><strong>{s['platform']}</strong>{perf}</td>
            <td><code>{s['email']}</code></td>
            <td><code>{s['password']}</code>{pin}</td>
            <td>{s.get('cost') or '-'}</td>
            <td><span class="badge badge-ok">Disponible</span></td>
            <td>
                <form action="/api/delete-account/{s['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar del stock?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;">🗑️</button>
                </form>
            </td>
        </tr>
        """
    if not stock_rows:
        stock_rows = "<tr><td colspan='6' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas libres en stock. Agrega cuentas o pídeselo a Gemini.</td></tr>"

    # Salud del Inventario y Semáforo de Stock
    stock_health = database.get_stock_health_summary()
    stock_health_html = ""
    for p in stock_health.get("platforms", []):
        st = p["status"]
        if st == "agotado":
            st_color = "#ef4444"
            st_bg = "#450a0a"
            st_border = "#991b1b"
            st_badge = "🔴 Agotado"
        elif st == "bajo":
            st_color = "#f59e0b"
            st_bg = "#451a03"
            st_border = "#92400e"
            st_badge = "🟡 Stock Bajo"
        else:
            st_color = "#10b981"
            st_bg = "#064e3b"
            st_border = "#047857"
            st_badge = "🟢 Óptimo"

        stock_health_html += f"""
        <div style="background:{st_bg};border:1px solid {st_border};border-radius:10px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <strong style="color:#f1f5f9;font-size:0.95rem;">{p['platform']}</strong>
                <span class="badge" style="background:{st_border};color:{st_color};">{st_badge}</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;">
                <span style="font-size:1.3rem;font-weight:800;color:{st_color};">{p['free_count']} <small style="font-size:0.75rem;color:#94a3b8;font-weight:normal;">libres</small></span>
                <small style="color:#94a3b8;">Activas: {p['occupied_count']}</small>
            </div>
            <form action="/api/set-stock-threshold" method="POST" style="display:flex;align-items:center;gap:6px;font-size:0.75rem;border-top:1px solid {st_border};padding-top:8px;">
                <input type="hidden" name="platform" value="{p['platform']}">
                <span style="color:#94a3b8;">Mín:</span>
                <input type="number" name="min_stock" value="{p['min_threshold']}" min="0" max="99" style="width:45px;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:4px;padding:2px 4px;text-align:center;">
                <button type="submit" class="btn-action" style="padding:2px 6px;font-size:0.7rem;background:#1e293b;color:#38bdf8;border-color:#334155;">Guardar</button>
            </form>
        </div>
        """
    if not stock_health_html:
        stock_health_html = "<div style='color:#64748b;padding:15px;grid-column:1/-1;'>No hay plataformas registradas en el inventario aún.</div>"

    # 3. Filas de Cuentas Caídas
    fallen_rows = ""
    for f in fallen_accounts:
        client_name = f.get("client_name") or "Sin cliente asignado"
        fallen_rows += f"""
        <tr>
            <td><strong>{f['platform']}</strong></td>
            <td><code>{f['email']}</code></td>
            <td><strong>{client_name}</strong></td>
            <td><small style="color:#fca5a5;">{f.get('notes') or 'Reportada'}</small></td>
            <td>
                <form action="/api/auto-replace/{f['id']}" method="POST" style="display:inline;">
                    <button type="submit" class="btn-action btn-replace" title="Buscar reemplazo en stock de la misma plataforma">🔄 Reemplazar Automáticamente</button>
                </form>
            </td>
        </tr>
        """
    if not fallen_rows:
        fallen_rows = "<tr><td colspan='5' style='text-align:center;color:#10b981;padding:20px;'>🎉 ¡No hay cuentas caídas! Todo el sistema está funcionando.</td></tr>"

    # 4. Filas de Transacciones Financieras
    tx_rows = ""
    for t in transactions:
        c_name = t.get("client_name") or "Venta General"
        plat = t.get("platform") or "Streaming"
        c_type = "👔 Revendedor" if "revend" in (t.get("client_type") or "").lower() else "👤 Final"
        tx_rows += f"""
        <tr>
            <td><small style="color:#94a3b8;">{t['created_at'][:16]}</small></td>
            <td><strong>{c_name}</strong> ({c_type})</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{plat}</span></td>
            <td><strong style="color:#10b981;">+{database.format_ars(t['amount'])}</strong></td>
            <td><span style="color:#f59e0b;">-{database.format_ars(t['cost'])}</span></td>
            <td><strong style="color:#38bdf8;">+{database.format_ars(t['profit'])}</strong></td>
            <td><small>{t.get('payment_method') or 'Transf.'}</small></td>
        </tr>
        """
    if not tx_rows:
        tx_rows = "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay transacciones registradas este mes aún.</td></tr>"

    # 5. Cuentas Madre y Pantallas Compartidas
    screens_overview = database.get_shared_screens_overview()
    screens_html = ""
    for s in screens_overview:
        fill_pct = s['occupancy_rate']
        chips_html = ""
        for p in s['profiles']:
            st = p['status']
            pin_label = f" (PIN: {p['profile_pin']})" if p.get('profile_pin') else ""
            if st == 'ocupada':
                c_name = p.get('client_name') or 'Cliente'
                wa_cobro = database.generate_whatsapp_message(p, "cobro").get("wa_link", "#")
                wa_datos = database.generate_whatsapp_message(p, "entrega").get("wa_link", "#")
                chips_html += f"""
                <div class="chip chip-occupied">
                    <div>
                        <strong>{p['profile_name']}</strong>{pin_label}: <span>{c_name}</span>
                        <small style='color:#94a3b8;margin-left:6px;'>(Vence: {p.get('expiry_date')})</small>
                    </div>
                    <div style="display:flex;gap:4px;">
                        <a href="{wa_cobro}" target="_blank" class="btn-action" style="background:#15803d;color:white;text-decoration:none;padding:2px 6px;font-size:0.7rem;" title="Cobrar WhatsApp">💬</a>
                        <a href="{wa_datos}" target="_blank" class="btn-action" style="background:#0284c7;color:white;text-decoration:none;padding:2px 6px;font-size:0.7rem;" title="Datos WhatsApp">📩</a>
                    </div>
                </div>
                """
            elif st == 'libre':
                chips_html += f"""
                <div class="chip chip-free">
                    <span><strong>{p['profile_name']}</strong>{pin_label}: <em>Disponible para venta</em></span>
                    <span class="badge badge-ok" style="font-size:0.7rem;">Libre</span>
                </div>
                """
            elif st == 'caida':
                chips_html += f"""
                <div class="chip chip-fallen">
                    <span><strong>{p['profile_name']}</strong>{pin_label}: 🚨 Caída ({p.get('client_name') or 'Sin cliente'})</span>
                    <span class="badge badge-danger" style="font-size:0.7rem;">Caída</span>
                </div>
                """

        screens_html += f"""
        <div class="screen-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <h4 style="margin:0; font-size:1.05rem; color:#f8fafc;">{s['platform']}</h4>
                <form action="/api/report-master-fallen" method="POST" style="display:inline;" onsubmit="return confirm('¿Marcar toda la cuenta madre como caída? Se afectarán todas las pantallas.');">
                    <input type="hidden" name="email" value="{s['email']}">
                    <button type="submit" class="btn-action btn-warn" style="font-size:0.75rem; padding:3px 8px;">🚨 Reportar Caída Madre</button>
                </form>
            </div>
            <p style="margin:0 0 10px 0; font-size:0.85rem; color:#94a3b8;">
                Correo: <code>{s['email']}</code> | Clave: <code>{s['password']}</code>
            </p>
            <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:4px;">
                <span>Ocupación: {s['occupied_count']}/{s['total_profiles']} ({fill_pct}%)</span>
                <span>{s['free_count']} libres</span>
            </div>
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" style="width:{fill_pct}%;"></div>
            </div>
            <div class="profile-chips">
                {chips_html}
            </div>
        </div>
        """
    if not screens_html:
        screens_html = "<div style='grid-column:1/-1;text-align:center;color:#64748b;padding:30px;'>No hay cuentas registradas con pantallas múltiples aún. Puedes pedirle a Gemini: <em>'Crea una cuenta de Netflix con 4 pantallas'</em>.</div>"

    # 6. Catálogo de Precios ARS
    catalog_rows = ""
    for c in catalog_items:
        stype_badge = "📱 Pantalla" if c["service_type"] == "pantalla" else "👑 Completa"
        catalog_rows += f"""
        <tr>
            <td><strong>{c['platform']}</strong></td>
            <td><span class="badge" style="background:#1e293b;color:#94a3b8;">{stype_badge}</span></td>
            <td style="color:#f59e0b;">{c['cost_price_formatted']}</td>
            <td><strong style="color:#10b981;">{c['price_final_formatted']}</strong></td>
            <td><strong style="color:#38bdf8;">{c['price_reseller_formatted']}</strong></td>
            <td>
                <small style="color:#10b981;">+{database.format_ars(c['profit_final'])} (Final)</small><br>
                <small style="color:#38bdf8;">+{database.format_ars(c['profit_reseller'])} (Rev.)</small>
            </td>
            <td>
                <form action="/api/catalog/delete/{c['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar este precio del catálogo?');">
                    <button type="submit" class="btn-action" style="color:#ef4444;" title="Eliminar">🗑️</button>
                </form>
            </td>
        </tr>
        """
    if not catalog_rows:
        catalog_rows = "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay precios configurados en el catálogo aún.</td></tr>"

    # 7. Combos y Packs
    combos_html = ""
    for cb in combos_list:
        pills = "".join([f'<span class="badge" style="background:#0b0f19;border:1px solid #334155;color:#38bdf8;margin:2px;">{it["platform"]}</span>' for it in cb["items"]])
        combos_html += f"""
        <div style="background:#0b0f19;border:1px solid #1e293b;border-radius:12px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                    <h4 style="margin:0;color:#f8fafc;font-size:1.05rem;">{cb['name']}</h4>
                    <form action="/api/combos/delete/{cb['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar este combo?');">
                        <button type="submit" class="btn-action" style="background:transparent;border:none;color:#ef4444;cursor:pointer;font-size:1.1rem;padding:0;" title="Eliminar Combo">✕</button>
                    </form>
                </div>
                <p style="margin:0 0 10px 0;font-size:0.8rem;color:#94a3b8;">{cb.get('description') or 'Pack promocional'}</p>
                <div style="margin-bottom:12px;display:flex;flex-wrap:wrap;gap:4px;">{pills}</div>
            </div>
            <div style="border-top:1px solid #1e293b;padding-top:12px;">
                <div style="display:flex;justify-content:space-between;margin-bottom:10px;font-size:0.85rem;">
                    <span style="color:#94a3b8;">Final: <strong style="color:#10b981;">{cb['price_final_formatted']}</strong></span>
                    <span style="color:#94a3b8;">Rev: <strong style="color:#38bdf8;">{cb['price_reseller_formatted']}</strong></span>
                </div>
                <button type="button" onclick="openSellComboModal('{cb['id']}', '{cb['name']}')" class="btn" style="width:100%;background:#0284c7;padding:8px;font-size:0.85rem;font-weight:bold;">⚡ Vender Combo (1 Toque)</button>
            </div>
        </div>
        """
    if not combos_html:
        combos_html = "<div style='color:#64748b;padding:20px;grid-column:1/-1;'>No hay combos activos configurados aún.</div>"

    # 8. Filas de Proveedores Mayoristas
    suppliers_table_rows = ""
    for s in suppliers_list:
        clean_contact = re.sub(r'[^0-9]', '', s.get("contact") or "")
        contact_html = "-"
        if s.get("contact"):
            if "@" in s["contact"]:
                tg_user = s["contact"].replace("@", "").strip()
                contact_html = f'<a href="https://t.me/{tg_user}" target="_blank" style="color:#38bdf8;">@{tg_user}</a>'
            elif clean_contact:
                contact_html = f'<a href="https://wa.me/{clean_contact}" target="_blank" style="color:#22c55e;">{s["contact"]}</a>'
            else:
                contact_html = s["contact"]
        
        safe_name = s['name'].replace("'", "\\'")
        safe_contact = (s.get('contact') or '').replace("'", "\\'")
        safe_payment = (s.get('payment_info') or '').replace("'", "\\'")
        safe_notes = (s.get('notes') or '').replace("'", "\\'")

        suppliers_table_rows += f"""
        <tr>
            <td><strong>{s['name']}</strong></td>
            <td>{contact_html}</td>
            <td><code>{s.get('payment_info') or '-'}</code></td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{s['master_accounts_count']} cuentas ({s['profiles_count']} perfiles)</span></td>
            <td><strong style="color:#f59e0b;">{s['total_spent_formatted']}</strong></td>
            <td><small style="color:#94a3b8;">{s.get('notes') or '-'}</small></td>
            <td>
                <div style="display:flex;gap:6px;">
                    <button type="button" class="btn-action" style="color:#38bdf8;" onclick="openSupplierModal('{s['id']}', '{safe_name}', '{safe_contact}', '{safe_payment}', '{safe_notes}')" title="Editar">✏️</button>
                    <form action="/api/suppliers/delete/{s['id']}" method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar al proveedor {safe_name}?');">
                        <button type="submit" class="btn-action" style="color:#ef4444;" title="Eliminar">🗑️</button>
                    </form>
                </div>
            </td>
        </tr>
        """
    if not suppliers_table_rows:
        suppliers_table_rows = "<tr><td colspan='7' style='text-align:center;color:#64748b;padding:20px;'>No hay proveedores mayoristas registrados aún.</td></tr>"

    # 9. Filas de Cuentas Madre ante Proveedores
    master_accounts_table_rows = ""
    for m in master_accounts_list:
        sup_days = m.get("days_remaining_supplier")
        if sup_days is None:
            s_badge = '<span class="badge badge-warn">Sin Fecha</span>'
        elif sup_days < 0:
            s_badge = f'<span class="badge badge-danger">Vencida (-{abs(sup_days)}d)</span>'
        elif sup_days <= 3:
            s_badge = f'<span class="badge badge-warn">¡Vence en {sup_days}d!</span>'
        else:
            s_badge = f'<span class="badge badge-ok">En {sup_days}d</span>'

        if m["risk_mismatch"]:
            risk_cell = f'<span class="badge badge-danger">🚨 Desfase de Corte</span><br><small style="color:#f87171;font-size:0.75rem;">{m["mismatch_warning"]}</small>'
        else:
            risk_cell = '<span class="badge badge-ok" style="font-size:0.75rem;">✓ Sincronizado</span>'

        safe_email = m['email'].replace("'", "\\'")
        safe_plat = m['platform'].replace("'", "\\'")
        cur_expiry = m.get('supplier_expiry_date') or ''
        cur_cost = m.get('supplier_cost') or 0.0

        master_accounts_table_rows += f"""
        <tr style="{'background:rgba(239,68,68,0.06);' if m['risk_mismatch'] else ''}">
            <td><strong>{m['platform']}</strong></td>
            <td><code>{m['email']}</code></td>
            <td><span class="badge" style="background:#1e293b;color:#cbd5e1;">{m['supplier_name']}</span></td>
            <td><code>{cur_expiry or 'No fijada'}</code> {s_badge}</td>
            <td>{m.get('profiles_occupied', 0)} / {m.get('profiles_total', 0)} ({m.get('occupancy_rate', 0)}%)</td>
            <td style="color:#f59e0b;font-weight:600;">{m['supplier_cost_formatted']}</td>
            <td>{risk_cell}</td>
            <td>
                <button type="button" onclick="openRenewMasterModal('{safe_email}', '{safe_plat}', '{cur_expiry}', {cur_cost})" class="btn" style="background:#0284c7;padding:5px 10px;font-size:0.8rem;">🔄 Renovar</button>
            </td>
        </tr>
        """
    if not master_accounts_table_rows:
        master_accounts_table_rows = "<tr><td colspan='8' style='text-align:center;color:#64748b;padding:20px;'>No hay cuentas madre registradas.</td></tr>"

    # 10. Filas del Terminal de Logs
    initial_logs_html = ""
    for l in recent_logs:
        lvl = l.get("level", "INFO").upper()
        lvl_class = "error" if lvl in ("ERROR", "CRITICAL") else ("warn" if lvl == "WARNING" else "info")
        tb_str = l.get("traceback") or ""
        safe_tb = tb_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        tb_html = f'<div class="log-tb">{safe_tb}</div>' if safe_tb else ""
        safe_msg = l.get("message", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        initial_logs_html += f"""
        <div class="log-row log-{lvl_class}">
            <span class="log-time">{l['timestamp']}</span>
            <span class="log-badge log-badge-{lvl_class}">{lvl}</span>
            <span class="log-mod">[{l['module']}]</span>
            <span class="log-txt">{safe_msg}</span>
            {tb_html}
        </div>
        """
    if not initial_logs_html:
        initial_logs_html = "<div style='color:#64748b;padding:20px;text-align:center;'>No hay logs registrados en memoria.</div>"

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Streaming CRM & Finanzas</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f1f5f9; margin: 0; padding: 24px; }}
            .container {{ max-width: 1150px; margin: 0 auto; }}
            .header-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
            h1 {{ margin: 0; color: #38bdf8; font-size: 1.6rem; display: flex; align-items: center; gap: 10px; }}
            .card {{ background: #161e2e; border: 1px solid #1e293b; border-radius: 14px; padding: 20px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
            
            /* Tarjetas Financieras */
            .finance-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin-bottom: 24px; }}
            .fin-box {{ background: #0b0f19; border: 1px solid #1e293b; border-radius: 12px; padding: 18px; }}
            .fin-box h4 {{ margin: 0 0 6px 0; font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }}
            .fin-box p.amount {{ margin: 0; font-size: 1.6rem; font-weight: 800; }}
            .fin-box small {{ display: block; margin-top: 4px; font-size: 0.75rem; color: #64748b; }}
            .box-income {{ border-left: 4px solid #10b981; }}
            .box-income p.amount {{ color: #10b981; }}
            .box-costs {{ border-left: 4px solid #f59e0b; }}
            .box-costs p.amount {{ color: #f59e0b; }}
            .box-profit {{ border-left: 4px solid #38bdf8; background: #0c2135; }}
            .box-profit p.amount {{ color: #38bdf8; }}
            .box-pending {{ border-left: 4px solid #a855f7; }}
            .box-pending p.amount {{ color: #c084fc; }}

            /* Pantallas Compartidas */
            .screens-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }}
            .screen-card {{ background: #0b0f19; border: 1px solid #1e293b; border-radius: 12px; padding: 18px; }}
            .screen-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; border-bottom: 1px solid #1e293b; padding-bottom: 10px; }}
            .screen-title {{ font-weight: 700; font-size: 1rem; color: #38bdf8; }}
            .progress-bar-bg {{ background: #1e293b; border-radius: 6px; height: 8px; width: 100%; margin: 10px 0 14px 0; overflow: hidden; }}
            .progress-bar-fill {{ background: #10b981; height: 100%; border-radius: 6px; }}
            .profile-chips {{ display: flex; flex-direction: column; gap: 8px; }}
            .chip {{ padding: 8px 12px; border-radius: 8px; font-size: 0.8rem; display: flex; justify-content: space-between; align-items: center; }}
            .chip-occupied {{ background: #064e3b; border: 1px solid #047857; color: #a7f3d0; }}
            .chip-free {{ background: #111827; border: 1px dashed #334155; color: #94a3b8; }}
            .chip-fallen {{ background: #450a0a; border: 1px solid #7f1d1d; color: #fca5a5; }}

            .tabs {{ display: flex; gap: 10px; border-bottom: 1px solid #334155; margin-bottom: 20px; }}
            .tab-btn {{ background: none; border: none; color: #94a3b8; padding: 12px 18px; font-size: 0.95rem; font-weight: 600; cursor: pointer; border-bottom: 2px solid transparent; }}
            .tab-btn.active {{ color: #38bdf8; border-bottom-color: #38bdf8; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid #1e293b; font-size: 0.9rem; }}
            th {{ color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }}
            .badge {{ padding: 4px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 700; display: inline-block; }}
            .badge-ok {{ background: #064e3b; color: #6ee7b7; }}
            .badge-warn {{ background: #78350f; color: #fde047; }}
            .badge-danger {{ background: #7f1d1d; color: #fca5a5; }}
            code {{ font-family: monospace; background: #0b0f19; padding: 3px 6px; border-radius: 4px; font-size: 0.85rem; }}
            .btn {{ background: #0284c7; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 0.85rem; font-weight: 600; }}
            .btn:hover {{ background: #0369a1; }}
            .btn-action {{ background: transparent; border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; cursor: pointer; font-size: 0.8rem; font-weight: 600; }}
            .btn-warn {{ color: #f59e0b; border-color: #78350f; }}
            .btn-warn:hover {{ background: #451a03; }}
            .btn-replace {{ background: #059669; color: white; border: none; }}
            .btn-replace:hover {{ background: #047857; }}
            .btn-logout {{ background: #1e293b; color: #cbd5e1; text-decoration: none; padding: 8px 14px; border-radius: 8px; font-size: 0.85rem; }}
            .btn-logout:hover {{ background: #334155; color: #fff; }}
            .endpoint-banner {{ background: #0369a1; color: white; padding: 12px 18px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; margin-top: 15px; font-size: 0.9rem; }}

            /* Plantillas WhatsApp & Preview */
            .template-chip {{
                background: #1e293b;
                border: 1px solid #38bdf8;
                color: #38bdf8;
                padding: 5px 10px;
                border-radius: 6px;
                font-size: 0.78rem;
                font-family: monospace;
                cursor: pointer;
                transition: all 0.15s ease;
                display: inline-block;
                user-select: none;
            }}
            .template-chip:hover {{
                background: #0284c7;
                color: #ffffff;
                border-color: #0284c7;
            }}
            .wa-preview-card {{
                background: #0b141a;
                border: 1px solid #222e35;
                border-radius: 12px;
                padding: 16px;
                display: flex;
                flex-direction: column;
                min-height: 280px;
                background-image: radial-gradient(#1f2c34 1px, transparent 1px);
                background-size: 16px 16px;
            }}
            .wa-bubble {{
                background: #005c4b;
                color: #e9edef;
                padding: 12px 14px;
                border-radius: 8px 8px 0 8px;
                font-size: 0.84rem;
                line-height: 1.45;
                max-width: 95%;
                align-self: flex-end;
                box-shadow: 0 1px 2px rgba(0,0,0,0.3);
                white-space: pre-wrap;
                word-break: break-word;
            }}
            .wa-bubble-time {{
                display: flex;
                justify-content: flex-end;
                align-items: center;
                gap: 4px;
                font-size: 0.65rem;
                color: #8696a0;
                margin-top: 5px;
            }}
            .tpl-subtab-btn {{
                background: #1e293b;
                border: 1px solid #334155;
                color: #94a3b8;
                padding: 8px 14px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 0.85rem;
                font-weight: 600;
            }}
            .tpl-subtab-btn.active {{
                background: #0284c7;
                border-color: #0284c7;
                color: #ffffff;
            }}

            /* Terminal de Logs & Diagnóstico */
            .log-terminal {{
                background: #020617;
                border: 1px solid #1e293b;
                border-radius: 10px;
                padding: 14px;
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
                font-size: 0.82rem;
                line-height: 1.6;
                max-height: 520px;
                overflow-y: auto;
            }}
            .log-row {{
                padding: 4px 8px;
                border-radius: 4px;
                margin-bottom: 2px;
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                align-items: baseline;
                border-left: 3px solid transparent;
            }}
            .log-row:hover {{
                background: rgba(255,255,255,0.03);
            }}
            .log-error {{
                background: rgba(239, 68, 68, 0.12);
                border-left-color: #ef4444;
                color: #fca5a5;
            }}
            .log-warn {{
                background: rgba(245, 158, 11, 0.10);
                border-left-color: #f59e0b;
                color: #fde68a;
            }}
            .log-info {{
                border-left-color: #38bdf8;
                color: #cbd5e1;
            }}
            .log-time {{
                color: #64748b;
                font-size: 0.75rem;
                min-width: 145px;
            }}
            .log-badge {{
                padding: 1px 6px;
                border-radius: 4px;
                font-weight: 700;
                font-size: 0.7rem;
                text-transform: uppercase;
            }}
            .log-badge-error {{ background: #7f1d1d; color: #fecaca; }}
            .log-badge-warn {{ background: #78350f; color: #fef08a; }}
            .log-badge-info {{ background: #0c4a6e; color: #bae6fd; }}
            .log-mod {{
                color: #a78bfa;
                font-weight: 600;
            }}
            .log-txt {{
                flex: 1;
                word-break: break-word;
            }}
            .log-tb {{
                width: 100%;
                margin: 4px 0 6px 20px;
                padding: 8px;
                background: #000;
                border: 1px solid #7f1d1d;
                border-radius: 4px;
                color: #f87171;
                font-size: 0.75rem;
                white-space: pre-wrap;
            }}
            .filter-btn {{
                background: #1e293b;
                border: 1px solid #334155;
                color: #94a3b8;
                padding: 6px 12px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 0.8rem;
                font-weight: 600;
            }}
            .filter-btn.active {{
                background: #0284c7;
                border-color: #0284c7;
                color: #fff;
            }}
        </style>
        <script>
            // Modal Proveedores Mayoristas
            function openSupplierModal(id, name, contact, payment, notes) {{
                document.getElementById('sup-modal-id').value = id || '';
                document.getElementById('sup-modal-name').value = name || '';
                document.getElementById('sup-modal-contact').value = contact || '';
                document.getElementById('sup-modal-payment').value = payment || '';
                document.getElementById('sup-modal-notes').value = notes || '';
                document.getElementById('sup-modal-title').innerText = id ? '✏️ Editar Proveedor Mayorista' : '➕ Nuevo Proveedor Mayorista';
                document.getElementById('modal-supplier').style.display = 'flex';
            }}
            function closeSupplierModal() {{
                document.getElementById('modal-supplier').style.display = 'none';
            }}

            // Modal Renovación de Cuenta Madre
            function openRenewMasterModal(email, platform, curExpiry, curCost) {{
                document.getElementById('rm-modal-email').value = email;
                document.getElementById('rm-modal-plat').value = platform;
                document.getElementById('rm-display-account').innerText = platform + ' - ' + email;
                
                let baseDate = new Date();
                if (curExpiry) {{
                    const parsed = new Date(curExpiry + 'T00:00:00');
                    if (!isNaN(parsed) && parsed > baseDate) baseDate = parsed;
                }}
                baseDate.setDate(baseDate.getDate() + 30);
                const yyyy = baseDate.getFullYear();
                const mm = String(baseDate.getMonth() + 1).padStart(2, '0');
                const dd = String(baseDate.getDate()).padStart(2, '0');
                document.getElementById('rm-modal-expiry').value = yyyy + '-' + mm + '-' + dd;
                document.getElementById('rm-modal-cost').value = curCost || '';
                document.getElementById('modal-renew-master').style.display = 'flex';
            }}
            function closeRenewMasterModal() {{
                document.getElementById('modal-renew-master').style.display = 'none';
            }}

            // Terminal de Logs & Diagnóstico en Vivo
            let currentLogLevel = 'ALL';
            let cachedLogs = [];

            async function refreshSystemLogs() {{
                const term = document.getElementById('logs-terminal');
                if (!term) return;
                try {{
                    const res = await fetch('/api/logs/json');
                    if (!res.ok) return;
                    const data = await res.json();
                    cachedLogs = data.logs || [];
                    renderFilteredLogs();
                    
                    if (data.health) {{
                        const h = data.health;
                        const statEl = document.getElementById('health-overall-status');
                        if (statEl) {{
                            statEl.innerText = h.status;
                            statEl.className = 'badge ' + (h.status === 'OK' ? 'badge-ok' : (h.status === 'WARNING' ? 'badge-warn' : 'badge-danger'));
                        }}
                        const errsEl = document.getElementById('health-errors-count');
                        if (errsEl) errsEl.innerText = (h.logs_summary ? h.logs_summary.errors_count : 0);
                        const warnsEl = document.getElementById('health-warns-count');
                        if (warnsEl) warnsEl.innerText = (h.logs_summary ? h.logs_summary.warnings_count : 0);
                        const uptimeEl = document.getElementById('health-uptime');
                        if (uptimeEl) uptimeEl.innerText = 'Uptime: ' + (h.uptime || '-');
                    }}
                }} catch (e) {{
                    console.error('Error cargando logs:', e);
                }}
            }}

            function setLogFilterLevel(lvl) {{
                currentLogLevel = lvl;
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                const b = document.getElementById('filter-btn-' + lvl.toLowerCase());
                if (b) b.classList.add('active');
                renderFilteredLogs();
            }}

            function filterLogsLocally() {{
                renderFilteredLogs();
            }}

            function renderFilteredLogs() {{
                const term = document.getElementById('logs-terminal');
                const searchQ = (document.getElementById('log-search-input')?.value || '').toLowerCase().trim();
                if (!term) return;

                if (!cachedLogs || cachedLogs.length === 0) return;

                let filtered = cachedLogs.filter(l => {{
                    const lvl = (l.level || 'INFO').toUpperCase();
                    if (currentLogLevel === 'ERROR' && !(lvl === 'ERROR' || lvl === 'CRITICAL')) return false;
                    if (currentLogLevel === 'WARNING' && lvl !== 'WARNING') return false;
                    if (currentLogLevel === 'INFO' && lvl !== 'INFO') return false;

                    if (searchQ) {{
                        const text = (l.message + ' ' + l.module + ' ' + (l.traceback || '')).toLowerCase();
                        if (!text.includes(searchQ)) return false;
                    }}
                    return true;
                }});

                if (filtered.length === 0) {{
                    term.innerHTML = '<div style="color:#64748b;padding:20px;text-align:center;">No hay registros que coincidan con el filtro actual.</div>';
                    return;
                }}

                let html = '';
                filtered.forEach(l => {{
                    const lvl = (l.level || 'INFO').toUpperCase();
                    const lvlClass = (lvl === 'ERROR' || lvl === 'CRITICAL') ? 'error' : (lvl === 'WARNING' ? 'warn' : 'info');
                    const tbHtml = l.traceback ? '<div class="log-tb">' + l.traceback.replace(/</g, '&lt;') + '</div>' : '';
                    html += '<div class="log-row log-' + lvlClass + '">' +
                        '<span class="log-time">' + l.timestamp + '</span> ' +
                        '<span class="log-badge log-badge-' + lvlClass + '">' + lvl + '</span> ' +
                        '<span class="log-mod">[' + l.module + ']</span> ' +
                        '<span class="log-txt">' + (l.message || '').replace(/</g, '&lt;') + '</span>' +
                        tbHtml +
                    '</div>';
                }});
                term.innerHTML = html;
            }}
            function showTab(tabId) {{
                document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                const target = document.getElementById(tabId);
                const btn = document.getElementById('btn-' + tabId);
                if (target) target.style.display = 'block';
                if (btn) btn.classList.add('active');
            }}
            function openSellComboModal(comboId, comboName) {{
                document.getElementById('modal-combo-id').value = comboId;
                document.getElementById('modal-combo-title').innerText = '⚡ Vender ' + comboName;
                document.getElementById('modal-sell-combo').style.display = 'flex';
            }}
            function closeSellComboModal() {{
                document.getElementById('modal-sell-combo').style.display = 'none';
            }}
            async function openClient360Modal(clientId) {{
                const modal = document.getElementById('modal-client-360');
                if (!modal) return;
                modal.style.display = 'flex';
                document.getElementById('m360-name').innerText = 'Cargando perfil...';
                
                try {{
                    const res = await fetch('/api/client/360/' + encodeURIComponent(clientId));
                    if (!res.ok) {{
                        alert('No se pudo cargar la información del cliente.');
                        closeClient360Modal();
                        return;
                    }}
                    const data = await res.json();
                    const c = data.client;
                    const health = data.health_status;
                    const kpis = data.financial_kpis;
                    const billing = data.consolidated_billing;
                    const act = data.active_accounts || [];
                    const pays = data.payments_history || [];

                    document.getElementById('m360-name').innerText = c.name;
                    document.getElementById('m360-code').innerText = 'Código: ' + (c.client_code || '-');
                    
                    const typeBadge = document.getElementById('m360-type-badge');
                    typeBadge.innerText = c.client_type_label || 'Cliente';
                    typeBadge.style.background = c.client_type === 'revendedor' ? '#7c3aed' : '#0284c7';

                    const healthBadge = document.getElementById('m360-health-badge');
                    healthBadge.innerText = health.label;
                    healthBadge.className = 'badge ' + (health.badge_class || 'badge-ok');
                    document.getElementById('m360-health-summary').innerText = health.summary || '';

                    // Contactos
                    const waLink = document.getElementById('m360-wa-link');
                    if (c.clean_whatsapp) {{
                        waLink.href = 'https://wa.me/' + c.clean_whatsapp;
                        waLink.innerText = c.whatsapp || c.clean_whatsapp;
                        document.getElementById('m360-wa-wrap').style.display = 'inline';
                    }} else {{
                        document.getElementById('m360-wa-wrap').style.display = 'none';
                    }}

                    const tgLink = document.getElementById('m360-tg-link');
                    if (c.telegram) {{
                        const tgUser = c.telegram.replace('@', '');
                        tgLink.href = 'https://t.me/' + tgUser;
                        tgLink.innerText = '@' + tgUser;
                        document.getElementById('m360-tg-wrap').style.display = 'inline';
                    }} else {{
                        document.getElementById('m360-tg-wrap').style.display = 'none';
                    }}

                    // KPIs
                    document.getElementById('m360-ltv').innerText = kpis.ltv_formatted || '$ 0 ARS';
                    document.getElementById('m360-payments-count').innerText = (kpis.payments_count || 0) + ' cobros';
                    document.getElementById('m360-profit').innerText = kpis.total_profit_formatted || '$ 0 ARS';
                    document.getElementById('m360-monthly').innerText = kpis.monthly_committed_spend_formatted || '$ 0 ARS';
                    document.getElementById('m360-accounts-count').innerText = act.length + ' servicio(s)';

                    // Cobro consolidado
                    const consBox = document.getElementById('m360-consolidated-box');
                    const consBtn = document.getElementById('m360-consolidated-btn');
                    if (billing && billing.success && billing.wa_link) {{
                        consBox.style.display = 'flex';
                        consBtn.href = billing.wa_link;
                        document.getElementById('m360-consolidated-desc').innerText = 
                            'Total a renovar: ' + billing.total_amount_formatted + ' (' + billing.accounts_count + ' suscripciones)';
                    }} else {{
                        consBox.style.display = 'none';
                    }}

                    // Tabla de cuentas activas
                    let accHtml = '<table style="width:100%;font-size:0.8rem;border-collapse:collapse;">';
                    accHtml += '<thead><tr style="border-bottom:1px solid #334155;text-align:left;color:#94a3b8;">' +
                        '<th style="padding:6px 8px;">Servicio</th>' +
                        '<th style="padding:6px 8px;">Cuenta / Perfil</th>' +
                        '<th style="padding:6px 8px;">Vence</th>' +
                        '<th style="padding:6px 8px;">Estado</th>' +
                        '<th style="padding:6px 8px;">Precio</th>' +
                        '<th style="padding:6px 8px;">WhatsApp</th></tr></thead><tbody>';

                    if (act.length === 0) {{
                        accHtml += '<tr><td colspan="6" style="text-align:center;padding:12px;color:#64748b;">No posee suscripciones activas en este momento.</td></tr>';
                    }} else {{
                        act.forEach(a => {{
                            const perf = a.profile_name ? (' (Perf: ' + a.profile_name + ')') : '';
                            const pin = a.profile_pin ? (' [PIN: ' + a.profile_pin + ']') : '';
                            const cobroBtn = a.wa_cobro_link ? ('<a href="' + a.wa_cobro_link + '" target="_blank" class="btn-action" style="background:#15803d;color:#fff;text-decoration:none;padding:3px 6px;border-radius:4px;font-size:0.75rem;margin-right:4px;">💬 Cobro</a>') : '';
                            const entregaBtn = a.wa_entrega_link ? ('<a href="' + a.wa_entrega_link + '" target="_blank" class="btn-action" style="background:#0284c7;color:#fff;text-decoration:none;padding:3px 6px;border-radius:4px;font-size:0.75rem;">📩 Datos</a>') : '';
                            accHtml += '<tr style="border-bottom:1px solid #1e293b;">' +
                                '<td style="padding:6px 8px;"><strong>' + a.platform + '</strong></td>' +
                                '<td style="padding:6px 8px;"><code>' + a.email + '</code>' + perf + pin + '</td>' +
                                '<td style="padding:6px 8px;">' + (a.expiry_date || '-') + '</td>' +
                                '<td style="padding:6px 8px;"><span class="badge ' + (a.badge_class || '') + '">' + a.days_label + '</span></td>' +
                                '<td style="padding:6px 8px;"><strong>' + (a.price_formatted || a.price || '-') + '</strong></td>' +
                                '<td style="padding:6px 8px;white-space:nowrap;">' + cobroBtn + entregaBtn + '</td></tr>';
                        }});
                    }}
                    accHtml += '</tbody></table>';
                    document.getElementById('m360-accounts-table-wrap').innerHTML = accHtml;

                    // Tabla de pagos
                    let payHtml = '<table style="width:100%;font-size:0.8rem;border-collapse:collapse;">';
                    payHtml += '<thead><tr style="border-bottom:1px solid #334155;text-align:left;color:#94a3b8;">' +
                        '<th style="padding:6px 8px;">Fecha</th>' +
                        '<th style="padding:6px 8px;">Monto (ARS)</th>' +
                        '<th style="padding:6px 8px;">Ganancia</th>' +
                        '<th style="padding:6px 8px;">Método</th>' +
                        '<th style="padding:6px 8px;">Detalle</th></tr></thead><tbody>';

                    if (pays.length === 0) {{
                        payHtml += '<tr><td colspan="5" style="text-align:center;padding:12px;color:#64748b;">Aún no se registraron pagos previos para este cliente.</td></tr>';
                    }} else {{
                        pays.forEach(p => {{
                            const dStr = p.created_at ? p.created_at.substring(0, 16) : '-';
                            payHtml += '<tr style="border-bottom:1px solid #1e293b;">' +
                                '<td style="padding:6px 8px;color:#94a3b8;">' + dStr + '</td>' +
                                '<td style="padding:6px 8px;"><strong style="color:#38bdf8;">' + p.amount_formatted + '</strong></td>' +
                                '<td style="padding:6px 8px;color:#34d399;">' + p.profit_formatted + '</td>' +
                                '<td style="padding:6px 8px;">' + (p.payment_method || 'Transferencia') + '</td>' +
                                '<td style="padding:6px 8px;color:#cbd5e1;">' + (p.notes || (p.account_platform ? ('Renovación ' + p.account_platform) : '-')) + '</td>' +
                                '</tr>';
                        }});
                    }}
                    payHtml += '</tbody></table>';
                    document.getElementById('m360-payments-table-wrap').innerHTML = payHtml;

                }} catch (err) {{
                    console.error(err);
                    alert('Error al consultar datos del cliente.');
                    closeClient360Modal();
                }}
            }}

            function closeClient360Modal() {{
                const modal = document.getElementById('modal-client-360');
                if (modal) modal.style.display = 'none';
            }}

            function filterActiveTable() {{
                const q = (document.getElementById('filter-active-table').value || '').toLowerCase();
                const rows = document.querySelectorAll('#tab-active tbody tr');
                rows.forEach(r => {{
                    const txt = r.innerText.toLowerCase();
                    r.style.display = txt.includes(q) ? '' : 'none';
                }});
            }}

            let currentTemplateKey = 'cobro';
            let cachedTemplates = {{}};
            let cachedPaymentSettings = {{}};
            let cachedFormattedPayment = '';

            async function loadTemplatesManager() {{
                try {{
                    const res = await fetch('/api/templates/json');
                    if (!res.ok) return;
                    const data = await res.json();
                    cachedTemplates = data.templates || {{}};
                    cachedPaymentSettings = data.payment_settings || {{}};
                    cachedFormattedPayment = data.formatted_payment_methods || '';
                    switchTemplateTab(currentTemplateKey);
                }} catch (e) {{
                    console.error("Error cargando plantillas:", e);
                }}
            }}

            function switchTemplateTab(key) {{
                currentTemplateKey = key;
                document.querySelectorAll('.tpl-subtab-btn').forEach(b => b.classList.remove('active'));
                const activeBtn = document.getElementById('tpl-btn-' + key);
                if (activeBtn) activeBtn.classList.add('active');

                const tpl = cachedTemplates[key];
                if (!tpl) return;

                const keyInput = document.getElementById('editor-template-key');
                if (keyInput) keyInput.value = key;
                const titleInput = document.getElementById('editor-template-title');
                if (titleInput) titleInput.value = tpl.title || '';
                const descInput = document.getElementById('editor-template-desc');
                if (descInput) descInput.value = tpl.description || '';

                const titleDisp = document.getElementById('editor-title-display');
                if (titleDisp) titleDisp.innerText = tpl.title || key;
                const descDisp = document.getElementById('editor-desc-display');
                if (descDisp) descDisp.innerText = tpl.description || '';

                const contentArea = document.getElementById('editor-content');
                if (contentArea) contentArea.value = tpl.content || '';

                const resetForm = document.getElementById('form-reset-template');
                if (resetForm) resetForm.action = '/api/templates/reset/' + key;

                updateTemplatePreview();
            }}

            function insertTemplateTag(tag) {{
                const area = document.getElementById('editor-content');
                if (!area) return;
                const start = area.selectionStart;
                const end = area.selectionEnd;
                const text = area.value;
                area.value = text.substring(0, start) + tag + text.substring(end);
                area.selectionStart = area.selectionEnd = start + tag.length;
                area.focus();
                updateTemplatePreview();
            }}

            function updateTemplatePreview() {{
                const area = document.getElementById('editor-content');
                const previewEl = document.getElementById('wa-preview-text');
                if (!area || !previewEl) return;
                const rawText = area.value;

                const sampleContext = {{
                    "cliente": "Lucas Martínez",
                    "plataforma": "Netflix 4K",
                    "email": "netflix.ultra4k@gmail.com",
                    "password": "Password2026*",
                    "perfil": "Perfil 2",
                    "pin": "4421",
                    "vencimiento": "2026-10-15",
                    "dias_restantes": " (vence en 2 días)",
                    "monto": "$ 5.500 ARS",
                    "servicios_lista": "• Netflix 4K (Perfil 2) - Vence: 2026-10-15 ($ 5.500 ARS)\\n• Disney+ Premium (ESPN) - Vence: 2026-10-18 ($ 4.000 ARS)",
                    "metodos_pago": cachedFormattedPayment || "• Mercado Pago (Alias): mi.alias.mp\\n• CBU/CVU: 0000003100012345678901\\n• Titular: Juan Ortiz\\n• Banco: Mercado Pago",
                    "alias_mp": (cachedPaymentSettings && cachedPaymentSettings.alias_mp) || "mi.alias.mp",
                    "cbu": (cachedPaymentSettings && cachedPaymentSettings.cvu_cbu) || "0000003100012345678901",
                    "titular": (cachedPaymentSettings && cachedPaymentSettings.account_holder) || "Juan Ortiz",
                    "banco": (cachedPaymentSettings && cachedPaymentSettings.bank_name) || "Mercado Pago",
                    "usdt": (cachedPaymentSettings && cachedPaymentSettings.usdt_address) || "TYD2...BinanceUSDT",
                    "cuentas_cantidad": "2"
                }};

                let rendered = rawText;
                for (const [k, v] of Object.entries(sampleContext)) {{
                    rendered = rendered.split('{{' + k + '}}').join(v || '');
                }}

                const cleanLines = rendered.split('\\n').filter(line => {{
                    const t = line.trim();
                    return !(t === '👤 *Perfil:*' || t === '👤 *Perfil Asignado:*' || t === '🔒 *PIN:*' || t === '🔒 *PIN de Perfil:*');
                }});
                rendered = cleanLines.join('\\n');

                let formatted = rendered
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/\\*(.*?)\\*/g, "<strong>$1</strong>")
                    .replace(/`(.*?)`/g, "<code style='background:rgba(255,255,255,0.12);padding:2px 4px;border-radius:3px;'>$1</code>");

                previewEl.innerHTML = formatted;
            }}

            let waQrPollInterval = null;

            async function checkWaStatus() {{
                const badge = document.getElementById('wa-connection-badge');
                const btnQr = document.getElementById('btn-wa-open-qr');
                const btnLogout = document.getElementById('btn-wa-logout');
                if (!badge) return;

                try {{
                    const res = await fetch('/api/whatsapp/status');
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    const data = await res.json();
                    const st = data.status || {{}};
                    if (st.connected) {{
                        badge.className = 'badge badge-ok';
                        badge.innerHTML = '🟢 Conectado (' + (st.instance || 'bot') + ')';
                        if (btnQr) btnQr.style.display = 'none';
                        if (btnLogout) btnLogout.style.display = 'inline-block';
                    }} else if (st.state === 'connecting') {{
                        badge.className = 'badge badge-warn';
                        badge.innerHTML = '🟡 Conectando...';
                        if (btnQr) btnQr.style.display = 'inline-block';
                        if (btnLogout) btnLogout.style.display = 'none';
                    }} else {{
                        badge.className = 'badge badge-danger';
                        badge.innerHTML = '🔴 Desconectado (' + (st.state || 'close') + ')';
                        if (btnQr) btnQr.style.display = 'inline-block';
                        if (btnLogout) btnLogout.style.display = 'none';
                    }}
                }} catch (e) {{
                    badge.className = 'badge badge-danger';
                    badge.innerHTML = '⚠️ Evolution API Desconectado';
                    if (btnQr) btnQr.style.display = 'inline-block';
                    if (btnLogout) btnLogout.style.display = 'none';
                }}
            }}

            async function openWaQrModal() {{
                const modal = document.getElementById('modal-wa-qr');
                if (!modal) return;
                modal.style.display = 'flex';
                loadWaQr();
                if (waQrPollInterval) clearInterval(waQrPollInterval);
                waQrPollInterval = setInterval(async () => {{
                    try {{
                        const res = await fetch('/api/whatsapp/status');
                        if (res.ok) {{
                            const data = await res.json();
                            if (data.status && data.status.connected) {{
                                clearInterval(waQrPollInterval);
                                closeWaQrModal();
                                alert('🎉 ¡WhatsApp Conectado Exitosamente!');
                                checkWaStatus();
                            }}
                        }}
                    }} catch (e) {{}}
                }}, 3000);
            }}

            function closeWaQrModal() {{
                const modal = document.getElementById('modal-wa-qr');
                if (modal) modal.style.display = 'none';
                if (waQrPollInterval) clearInterval(waQrPollInterval);
                checkWaStatus();
            }}

            async function loadWaQr() {{
                const img = document.getElementById('wa-qr-img');
                const loading = document.getElementById('wa-qr-loading');
                const pcode = document.getElementById('wa-qr-pairing');
                if (loading) loading.innerHTML = '🔄 Generando código QR con Evolution API...';
                if (img) img.style.display = 'none';
                if (pcode) pcode.innerHTML = '';

                try {{
                    const res = await fetch('/api/whatsapp/qr');
                    const data = await res.json();
                    if (data.connected) {{
                        if (loading) loading.innerHTML = '✅ ¡WhatsApp ya está conectado!';
                        setTimeout(closeWaQrModal, 1500);
                        return;
                    }}
                    if (data.base64) {{
                        if (img) {{
                            img.src = data.base64.startsWith('data:') ? data.base64 : 'data:image/png;base64,' + data.base64;
                            img.style.display = 'block';
                        }}
                        if (loading) loading.innerHTML = '📲 Escanea este código desde WhatsApp > Dispositivos Vinculados:';
                        if (data.pairingCode && pcode) {{
                            pcode.innerHTML = 'Código de vinculación: <strong>' + data.pairingCode + '</strong>';
                        }}
                    }} else {{
                        if (loading) loading.innerHTML = '⚠️ ' + (data.error || 'No se pudo obtener el QR. Verifica que Evolution API esté iniciado.');
                    }}
                }} catch (e) {{
                    if (loading) loading.innerHTML = '❌ Error al contactar al servidor: ' + e.message;
                }}
            }}

            window.addEventListener('DOMContentLoaded', () => {{
                const hash = window.location.hash.replace('#', '');
                if (hash && document.getElementById(hash)) {{
                    showTab(hash);
                }}
                loadTemplatesManager();
                checkWaStatus();
            }});
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header-bar">
                <h1>⚡ Streaming CRM & Finanzas</h1>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-size:0.85rem; color:#94a3b8;">Admin: <strong>{user}</strong></span>
                    <a href="/logout" class="btn-logout">Cerrar Sesión</a>
                </div>
            </div>

            {msg_banner}

            <!-- Dashboard de Finanzas y Balance -->
            <div class="finance-grid">
                <div class="fin-box box-income">
                    <h4>Ingresos Cobrados (Mes)</h4>
                    <p class="amount">{database.format_ars(finance['collected_income'])}</p>
                    <small>{finance['transactions_count']} cobros registrados</small>
                </div>
                <div class="fin-box box-costs">
                    <h4>Costo Proveedores</h4>
                    <p class="amount">{database.format_ars(finance['collected_costs'])}</p>
                    <small>Costo base de cuentas</small>
                </div>
                <div class="fin-box box-profit">
                    <h4>Ganancia Neta Real</h4>
                    <p class="amount">{database.format_ars(finance['collected_profit'])}</p>
                    <small>Beneficio líquido en el bolsillo</small>
                </div>
                <div class="fin-box box-pending">
                    <h4>Por Cobrar (Próximos 7d)</h4>
                    <p class="amount">{database.format_ars(finance['pending_receivables_7d'])}</p>
                    <small>{finance['pending_accounts_count']} cuentas por vencer</small>
                </div>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                        <form action="/api/test-telegram" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#475569;">📲 Test Telegram</button>
                        </form>
                        <form action="/api/check-now" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#059669;">🔍 Escanear Vencimientos</button>
                        </form>
                        <form action="/api/check-stock-alert" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#ea580c;">📦 Alerta Stock Telegram</button>
                        </form>
                    </div>
                </div>
                <div class="endpoint-banner">
                    <span><strong>Conexión Gemini Spark:</strong> <code>https://mcp.joif.net/mcp/</code> {'<span style="color:#10b981;">(OAuth 2.0 Protegido 🔒)</span>' if oauth_enabled else '<span style="color:#eab308;">(Público)</span>'}</span>
                    <span>Modo Financiero & CRM Activo</span>
                </div>
            </div>

            <div class="card">
                <div class="tabs">
                    <button id="btn-tab-active" class="tab-btn active" onclick="showTab('tab-active')">👥 Clientes & Activas ({len(active_accounts)})</button>
                    <button id="btn-tab-screens" class="tab-btn" onclick="showTab('tab-screens')">📺 Pantallas ({len(screens_overview)})</button>
                    <button id="btn-tab-suppliers" class="tab-btn" onclick="showTab('tab-suppliers')">🏢 Proveedores & Cuentas ({len(suppliers_list)}/{len(master_accounts_list)})</button>
                    <button id="btn-tab-catalog" class="tab-btn" onclick="showTab('tab-catalog')">🏷️ Precios & Combos ({len(catalog_items)}/{len(combos_list)})</button>
                    <button id="btn-tab-finance" class="tab-btn" onclick="showTab('tab-finance')">💵 Historial de Cobros ({len(transactions)})</button>
                    <button id="btn-tab-templates" class="tab-btn" onclick="showTab('tab-templates')">💬 WhatsApp & Mensajería</button>
                    <button id="btn-tab-stock" class="tab-btn" onclick="showTab('tab-stock')">📦 Stock Libre ({len(free_stock)})</button>
                    <button id="btn-tab-fallen" class="tab-btn" onclick="showTab('tab-fallen')">🚨 Cuentas Caídas ({len(fallen_accounts)})</button>
                    <button id="btn-tab-backup" class="tab-btn" onclick="showTab('tab-backup')">📁 Excel & Backups</button>
                    <button id="btn-tab-logs" class="tab-btn" onclick="showTab('tab-logs'); refreshSystemLogs();">📋 Logs & Diagnóstico</button>
                    <button id="btn-tab-oauth" class="tab-btn" onclick="showTab('tab-oauth')">🤖 Gemini Spark (OAuth)</button>
                </div>

                <div id="tab-active" class="tab-content" style="display:block;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; gap:12px; flex-wrap:wrap;">
                        <input type="text" id="filter-active-table" onkeyup="filterActiveTable()" placeholder="🔍 Filtrar por cliente, servicio, correo, WhatsApp..." style="flex:1; min-width:240px; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 12px; font-size:0.85rem;">
                        <div style="display:flex; gap:8px; align-items:center;">
                            <select onchange="if(this.value) openClient360Modal(this.value); this.value='';" style="background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 12px; font-size:0.85rem;">
                                <option value="">👤 Abrir Ficha 360° de...</option>
                                {client_select_options}
                            </select>
                        </div>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th>Cliente</th>
                                <th>Contacto</th>
                                <th>Servicio</th>
                                <th>Credenciales</th>
                                <th>Vence</th>
                                <th>Estado</th>
                                <th>Precio</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {active_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-screens" class="tab-content" style="display:none;">
                    <div class="screens-grid">
                        {screens_html}
                    </div>
                </div>

                <div id="tab-catalog" class="tab-content" style="display:none;">
                    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 24px;">
                        <!-- Card: Agregar o Modificar Precio al Catálogo -->
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:18px;">
                            <h3 style="margin:0 0 12px 0; font-size:1rem; color:#38bdf8;">➕ Agregar o Actualizar Precio (ARS)</h3>
                            <form action="/api/catalog/save" method="POST" style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                                <div style="grid-column: 1 / -1;">
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Plataforma / Servicio *</label>
                                    <input type="text" name="platform" placeholder="Ej: Netflix 4K, Max, Disney+" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Tipo de Servicio</label>
                                    <select name="service_type" style="width:100%;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px;font-size:0.85rem;">
                                        <option value="pantalla">📱 Pantalla (Perfil)</option>
                                        <option value="cuenta_completa">👑 Cuenta Completa</option>
                                    </select>
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Costo Proveedor ($ ARS)</label>
                                    <input type="number" name="cost_price" step="any" placeholder="3200" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Precio Final ($ ARS) *</label>
                                    <input type="number" name="price_final" step="any" placeholder="5500" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Precio Revendedor ($ ARS) *</label>
                                    <input type="number" name="price_reseller" step="any" placeholder="4200" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div style="grid-column: 1 / -1;">
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Notas / Observaciones</label>
                                    <input type="text" name="notes" placeholder="Perfil UHD con PIN individual" style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div style="grid-column: 1 / -1; margin-top:4px;">
                                    <button type="submit" class="btn" style="background:#059669;width:100%;padding:8px;">💾 Guardar Precio en Catálogo</button>
                                </div>
                            </form>
                        </div>

                        <!-- Card: Crear Nuevo Combo -->
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:18px;">
                            <h3 style="margin:0 0 12px 0; font-size:1rem; color:#38bdf8;">📦 Crear Nuevo Pack o Combo (ARS)</h3>
                            <form action="/api/combos/save" method="POST" style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                                <div style="grid-column: 1 / -1;">
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Nombre del Pack *</label>
                                    <input type="text" name="name" placeholder="Ej: Combo Dúo Cine (Netflix + Disney+)" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div style="grid-column: 1 / -1;">
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Plataformas Requeridas (separadas por coma) *</label>
                                    <input type="text" name="platforms" placeholder="Netflix 4K, Disney+ Premium" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Precio Pack Final ($ ARS) *</label>
                                    <input type="number" name="price_final" step="any" placeholder="8500" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Precio Pack Revendedor ($ ARS) *</label>
                                    <input type="number" name="price_reseller" step="any" placeholder="6800" required style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div style="grid-column: 1 / -1;">
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:3px;">Descripción Promocional</label>
                                    <input type="text" name="description" placeholder="1 Pantalla Netflix 4K + 1 Pantalla Disney+ con ESPN" style="width:100%;box-sizing:border-box;background:#161e2e;border:1px solid #334155;color:#fff;border-radius:6px;padding:6px 10px;font-size:0.85rem;">
                                </div>
                                <div style="grid-column: 1 / -1; margin-top:4px;">
                                    <button type="submit" class="btn" style="background:#0284c7;width:100%;padding:8px;">✨ Crear Pack / Combo</button>
                                </div>
                            </form>
                        </div>
                    </div>

                    <h3 style="margin:20px 0 12px 0; font-size:1.05rem; color:#e2e8f0;">📦 Packs y Combos Disponibles ({len(combos_list)})</h3>
                    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; margin-bottom:30px;">
                        {combos_html}
                    </div>

                    <h3 style="margin:20px 0 12px 0; font-size:1.05rem; color:#e2e8f0;">🏷️ Lista de Precios Oficiales ({len(catalog_items)} plataformas)</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Plataforma</th>
                                <th>Tipo</th>
                                <th>Costo Prov.</th>
                                <th>Precio Final (ARS)</th>
                                <th>Precio Revendedor (ARS)</th>
                                <th>Margen Ganancia</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {catalog_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-finance" class="tab-content" style="display:none;">
                    <table>
                        <thead>
                            <tr>
                                <th>Fecha</th>
                                <th>Cliente</th>
                                <th>Servicio</th>
                                <th>Cobrado</th>
                                <th>Costo Prov.</th>
                                <th>Ganancia Neta</th>
                                <th>Método</th>
                            </tr>
                        </thead>
                        <tbody>
                            {tx_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-stock" class="tab-content" style="display:none;">
                    <div style="margin-bottom: 24px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                            <h3 style="margin:0;font-size:1.05rem;color:#38bdf8;">🚦 Semáforo de Salud del Inventario</h3>
                            <span style="font-size:0.8rem;color:#94a3b8;">Umbrales mínimos configurables por plataforma</span>
                        </div>
                        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:12px;">
                            {stock_health_html}
                        </div>
                    </div>
                    <h3 style="margin:20px 0 10px 0;font-size:1rem;color:#e2e8f0;">📋 Listado de Cuentas y Perfiles Libres</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Plataforma</th>
                                <th>Correo</th>
                                <th>Contraseña</th>
                                <th>Costo</th>
                                <th>Estado</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {stock_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-fallen" class="tab-content" style="display:none;">
                    <table>
                        <thead>
                            <tr>
                                <th>Plataforma</th>
                                <th>Correo Caído</th>
                                <th>Cliente</th>
                                <th>Detalle</th>
                                <th>Acción Inmediata</th>
                            </tr>
                        </thead>
                        <tbody>
                            {fallen_rows}
                        </tbody>
                    </table>
                </div>

                <div id="tab-backup" class="tab-content" style="display:none;">
                    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px;">
                        <!-- Card 1: Descargas Excel/CSV -->
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px; display:flex; flex-direction:column; justify-content:space-between;">
                            <div>
                                <h3 style="margin:0 0 10px 0; color:#38bdf8; font-size:1.1rem; display:flex; align-items:center; gap:8px;">
                                    📥 Exportar a Excel (CSV)
                                </h3>
                                <p style="font-size:0.85rem; color:#94a3b8; margin-bottom:16px;">
                                    Descarga tus reportes en formato universal compatible con Excel y Google Sheets (codificación UTF-8 con BOM):
                                </p>
                                <div style="display:flex; flex-direction:column; gap:10px;">
                                    <a href="/api/export/active.csv" class="btn" style="background:#15803d; text-align:center; padding:10px; font-weight:600;" download>
                                        📊 Descargar Clientes & Cuentas Activas
                                    </a>
                                    <a href="/api/export/stock.csv" class="btn" style="background:#0284c7; text-align:center; padding:10px; font-weight:600;" download>
                                        📦 Descargar Inventario de Stock Libre
                                    </a>
                                    <a href="/api/export/transactions.csv" class="btn" style="background:#4338ca; text-align:center; padding:10px; font-weight:600;" download>
                                        💵 Descargar Balance y Cobros
                                    </a>
                                </div>
                            </div>
                            <form action="/api/trigger-backup" method="POST" style="margin-top:16px; border-top:1px solid #1e293b; padding-top:14px;">
                                <button type="submit" class="btn" style="width:100%; background:#7c3aed; padding:10px; font-weight:600;" title="Enviar los 3 archivos CSV directo a tu Telegram">
                                    📲 Enviar Copia de Seguridad a Telegram
                                </button>
                            </form>
                        </div>

                        <!-- Card 2: Carga Masiva de Stock -->
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                                <h3 style="margin:0; color:#10b981; font-size:1.1rem;">📦 Importar Stock Libre</h3>
                                <a href="/api/export/template-stock.csv" style="font-size:0.75rem; color:#38bdf8; text-decoration:none;" download>📥 Plantilla Ejemplo</a>
                            </div>
                            <p style="font-size:0.85rem; color:#94a3b8; margin-bottom:12px;">
                                Carga cuentas y perfiles libres subiendo un archivo <code>.csv</code> o pegando filas de Excel:
                            </p>
                            <form action="/api/import/stock" method="POST" enctype="multipart/form-data">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px;">Archivo CSV:</label>
                                <input type="file" name="file" accept=".csv,.txt" style="display:block; width:100%; font-size:0.8rem; margin-bottom:12px; color:#cbd5e1;">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px;">O pegar texto directo:</label>
                                <textarea name="csv_text" placeholder="Plataforma, Correo, Clave, Perfil, PIN, Costo..." style="width:100%; height:75px; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px; font-size:0.8rem; box-sizing:border-box; margin-bottom:12px;"></textarea>
                                <button type="submit" class="btn" style="width:100%; background:#059669; padding:10px; font-weight:600;">
                                    🚀 Procesar Carga de Stock
                                </button>
                            </form>
                        </div>

                        <!-- Card 3: Migración de Ventas / Clientes -->
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                                <h3 style="margin:0; color:#f59e0b; font-size:1.1rem;">👥 Importar Clientes & Ventas</h3>
                                <a href="/api/export/template-sales.csv" style="font-size:0.75rem; color:#38bdf8; text-decoration:none;" download>📥 Plantilla Ejemplo</a>
                            </div>
                            <p style="font-size:0.85rem; color:#94a3b8; margin-bottom:12px;">
                                Migra tu cartera con nombres, WhatsApp, vencimientos y precios asignados:
                            </p>
                            <form action="/api/import/sales" method="POST" enctype="multipart/form-data">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px;">Archivo CSV:</label>
                                <input type="file" name="file" accept=".csv,.txt" style="display:block; width:100%; font-size:0.8rem; margin-bottom:12px; color:#cbd5e1;">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px;">O pegar texto directo:</label>
                                <textarea name="csv_text" placeholder="Cliente, WhatsApp, Tipo, Plataforma, Correo, Vencimiento, Precio..." style="width:100%; height:75px; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px; font-size:0.8rem; box-sizing:border-box; margin-bottom:12px;"></textarea>
                                <button type="submit" class="btn" style="width:100%; background:#d97706; padding:10px; font-weight:600;">
                                    🚀 Procesar Migración de Ventas
                                </button>
                            </form>
                        </div>
                    </div>
                </div>

                <!-- Pestaña 💬 Plantillas WhatsApp y Datos de Cobro -->
                <div id="tab-templates" class="tab-content" style="display:none;">
                    <!-- 0. Conexión Evolution API & Bot Automático WhatsApp -->
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px; margin-bottom:24px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; border-bottom:1px solid #1e293b; padding-bottom:12px; flex-wrap:wrap; gap:12px;">
                            <div>
                                <h3 style="margin:0; color:#25d366; font-size:1.2rem; display:flex; align-items:center; gap:8px;">
                                    💬 WhatsApp Evolution API v2 & Bot de Mensajería
                                </h3>
                                <p style="margin:4px 0 0 0; font-size:0.85rem; color:#94a3b8;">
                                    Envío automático de cobros (09:00 AM), entrega de credenciales en ventas y auto-respuesta con Webhooks.
                                </p>
                            </div>
                            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                                <span id="wa-connection-badge" class="badge badge-warn">⏳ Verificando...</span>
                                <button type="button" id="btn-wa-open-qr" onclick="openWaQrModal()" class="btn" style="background:#25d366; color:#000; font-weight:bold; padding:8px 14px; border-radius:6px; cursor:pointer;">
                                    📲 Vincular WhatsApp (QR)
                                </button>
                                <button type="button" onclick="checkWaStatus()" class="btn-action" style="background:#1e293b; border:1px solid #334155; color:#38bdf8; padding:8px 12px; cursor:pointer;" title="Refrescar estado de conexión">
                                    🔄
                                </button>
                                <form action="/api/whatsapp/logout" method="POST" style="display:inline;" onsubmit="return confirm('¿Seguro que deseas cerrar la sesión de WhatsApp?');">
                                    <button type="submit" id="btn-wa-logout" class="btn-action btn-warn" style="display:none; padding:8px 12px; cursor:pointer;" title="Cerrar sesión de WhatsApp">
                                        🚪 Desconectar
                                    </button>
                                </form>
                            </div>
                        </div>

                        <!-- Formulario de Configuración y Toggles -->
                        <form action="/api/whatsapp/settings" method="POST" style="margin-bottom:20px;">
                            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:14px; margin-bottom:16px;">
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">URL Servidor Evolution API</label>
                                    <input type="text" name="api_url" value="{wa_settings.get('api_url', 'http://evolution-api:8080')}" placeholder="http://evolution-api:8080" required style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Global API Key (Token)</label>
                                    <input type="text" name="api_key" value="{wa_settings.get('api_key', 'mcp-evolution-key-2026')}" placeholder="mcp-evolution-key-2026" required style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Nombre de Instancia</label>
                                    <input type="text" name="instance_name" value="{wa_settings.get('instance_name', 'streaming-bot')}" placeholder="streaming-bot" required style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                            </div>

                            <!-- Toggles de Automatización -->
                            <div style="background:#161e2e; border:1px solid #334155; border-radius:8px; padding:14px; margin-bottom:16px;">
                                <div style="font-size:0.85rem; color:#f8fafc; font-weight:bold; margin-bottom:10px;">
                                    ⚙️ Automatizaciones Inteligentes:
                                </div>
                                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:12px;">
                                    <label style="display:flex; align-items:flex-start; gap:10px; cursor:pointer;">
                                        <input type="checkbox" name="auto_send_expiry" value="1" {'checked' if wa_settings.get('auto_send_expiry') == 1 else ''} style="margin-top:3px; transform:scale(1.2);">
                                        <div>
                                            <strong style="color:#38bdf8; font-size:0.85rem;">⏰ Auto-Cobro 09:00 AM</strong>
                                            <span style="display:block; font-size:0.75rem; color:#94a3b8;">Envía recordatorios diarios con delay anti-ban a clientes que vencen hoy o en 2 días.</span>
                                        </div>
                                    </label>

                                    <label style="display:flex; align-items:flex-start; gap:10px; cursor:pointer;">
                                        <input type="checkbox" name="auto_send_sales" value="1" {'checked' if wa_settings.get('auto_send_sales') == 1 else ''} style="margin-top:3px; transform:scale(1.2);">
                                        <div>
                                            <strong style="color:#10b981; font-size:0.85rem;">🚀 Auto-Envío en Ventas</strong>
                                            <span style="display:block; font-size:0.75rem; color:#94a3b8;">Despacha credenciales y accesos por WhatsApp automáticamente al vender combos o perfiles.</span>
                                        </div>
                                    </label>

                                    <label style="display:flex; align-items:flex-start; gap:10px; cursor:pointer;">
                                        <input type="checkbox" name="auto_reply_enabled" value="1" {'checked' if wa_settings.get('auto_reply_enabled', 1) == 1 else ''} style="margin-top:3px; transform:scale(1.2);">
                                        <div>
                                            <strong style="color:#a78bfa; font-size:0.85rem;">🤖 Bot de Auto-Respuesta</strong>
                                            <span style="display:block; font-size:0.75rem; color:#94a3b8;">Responde consultas de vencimientos, claves, datos CBU y notifica comprobantes a Telegram.</span>
                                        </div>
                                    </label>
                                </div>
                            </div>

                            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                                <button type="submit" class="btn" style="background:#059669; padding:9px 18px; font-weight:bold;">
                                    💾 Guardar Configuración de WhatsApp
                                </button>
                                <span style="font-size:0.8rem; color:#94a3b8;">
                                    Actualizado: <strong>{wa_settings.get('updated_at', 'Predeterminado')}</strong>
                                </span>
                            </div>
                        </form>

                        <!-- Fila: Webhook & Prueba Rápida -->
                        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:16px; border-top:1px solid #1e293b; padding-top:16px;">
                            <!-- Webhook Box -->
                            <div style="background:#161e2e; border:1px solid #334155; border-radius:8px; padding:14px;">
                                <h4 style="margin:0 0 8px 0; font-size:0.9rem; color:#38bdf8; display:flex; align-items:center; gap:6px;">
                                    🔗 Webhook para Auto-Respuesta (MESSAGES_UPSERT)
                                </h4>
                                <p style="font-size:0.75rem; color:#94a3b8; margin:0 0 10px 0;">
                                    Evolution API enviará los mensajes entrantes a este endpoint para procesar auto-respuestas y comprobantes:
                                </p>
                                <form action="/api/whatsapp/setup-webhook" method="POST" style="display:flex; gap:8px; flex-wrap:wrap;">
                                    <input type="text" name="webhook_url" value="https://mcp.juanconnect.online/api/webhook/whatsapp" style="flex:1; min-width:220px; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 10px; font-size:0.8rem;">
                                    <button type="submit" class="btn" style="background:#0284c7; padding:6px 12px; font-size:0.8rem; font-weight:600;">
                                        ⚡ Vincular Webhook
                                    </button>
                                </form>
                            </div>

                            <!-- Prueba Rápida de Envío -->
                            <div style="background:#161e2e; border:1px solid #334155; border-radius:8px; padding:14px;">
                                <h4 style="margin:0 0 8px 0; font-size:0.9rem; color:#10b981; display:flex; align-items:center; gap:6px;">
                                    🧪 Enviar Mensaje de Prueba
                                </h4>
                                <form action="/api/whatsapp/test" method="POST" style="display:grid; grid-template-columns: 1fr 1fr; gap:8px;">
                                    <input type="text" name="test_phone" placeholder="Teléfono (ej: 5491112345678)" required style="background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 8px; font-size:0.8rem;">
                                    <input type="text" name="test_message" placeholder="Texto de prueba..." value="Hola! Prueba de conexion exitosa con Streaming CRM." required style="background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 8px; font-size:0.8rem;">
                                    <div style="grid-column: 1 / -1;">
                                        <button type="submit" class="btn" style="width:100%; background:#059669; padding:6px 12px; font-size:0.8rem; font-weight:600;">
                                            📤 Enviar WhatsApp de Prueba
                                        </button>
                                    </div>
                                </form>
                            </div>
                        </div>

                        <!-- Fila 2: Chatwoot CRM Mobile Inbox -->
                        <div style="margin-top:16px; background:#161e2e; border:1px solid #334155; border-radius:8px; padding:16px;">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px; margin-bottom:10px;">
                                <div>
                                    <h4 style="margin:0 0 4px 0; font-size:0.95rem; color:#818cf8; display:flex; align-items:center; gap:6px;">
                                        📱 Chatwoot (Atención de Clientes desde la App Móvil)
                                    </h4>
                                    <p style="font-size:0.78rem; color:#94a3b8; margin:0;">
                                        Sincroniza WhatsApp con la app de Chatwoot (iOS/Android) para responder desde el celular con respuestas rápidas y notas privadas.
                                    </p>
                                </div>
                                <span style="background:#1e1b4b; color:#a5b4fc; border:1px solid #6366f1; font-size:0.7rem; font-weight:600; padding:2px 8px; border-radius:12px;">
                                    Bandeja Unificada
                                </span>
                            </div>
                            <form action="/api/whatsapp/setup-chatwoot" method="POST" style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:10px; align-items:end;">
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">URL de Chatwoot</label>
                                    <input type="text" name="chatwoot_url" value="http://chatwoot-rails:3000" required style="width:100%; box-sizing:border-box; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 10px; font-size:0.8rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Token de Acceso (Chatwoot)</label>
                                    <input type="password" name="chatwoot_token" placeholder="Ajustes de Perfil -> Access Token" required style="width:100%; box-sizing:border-box; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 10px; font-size:0.8rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">ID de Cuenta</label>
                                    <input type="text" name="account_id" value="1" required style="width:100%; box-sizing:border-box; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 10px; font-size:0.8rem;">
                                </div>
                                <div>
                                    <button type="submit" class="btn" style="width:100%; background:#4f46e5; padding:7px 12px; font-size:0.8rem; font-weight:600;">
                                        ⚡ Vincular con Chatwoot
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>

                    <!-- 1. Configuración de Cobro y CBU -->
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px; margin-bottom:20px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; border-bottom:1px solid #1e293b; padding-bottom:10px;">
                            <div>
                                <h3 style="margin:0; color:#38bdf8; font-size:1.15rem; display:flex; align-items:center; gap:8px;">
                                    💳 Configuración de Datos de Cobro (CBU / Alias / MP)
                                </h3>
                                <p style="margin:4px 0 0 0; font-size:0.85rem; color:#94a3b8;">
                                    Estos datos se inyectarán en tus mensajes en la etiqueta <code>{{metodos_pago}}</code> o en sus variables individuales.
                                </p>
                            </div>
                        </div>

                        <form action="/api/settings/payment" method="POST">
                            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; margin-bottom:16px;">
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Alias Mercado Pago / Billetera</label>
                                    <input type="text" name="alias_mp" value="{payment_settings.get('alias_mp', '')}" placeholder="ej: juan.streaming.mp" style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">CBU / CVU Bancario (22 dígitos)</label>
                                    <input type="text" name="cvu_cbu" value="{payment_settings.get('cvu_cbu', '')}" placeholder="ej: 0000003100012345678901" style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Titular de la Cuenta</label>
                                    <input type="text" name="account_holder" value="{payment_settings.get('account_holder', '')}" placeholder="ej: Juan Manuel Ortiz" style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Banco / Entidad Receptora</label>
                                    <input type="text" name="bank_name" value="{payment_settings.get('bank_name', 'Mercado Pago / Transferencia Bancaria')}" placeholder="ej: Mercado Pago / Banco Galicia" style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Binance Pay / USDT Address (Opcional)</label>
                                    <input type="text" name="usdt_address" value="{payment_settings.get('usdt_address', '')}" placeholder="ej: Binance Pay ID o red TRC20" style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:4px; font-weight:600;">Instrucciones Adicionales</label>
                                    <input type="text" name="extra_instructions" value="{payment_settings.get('extra_instructions', '')}" placeholder="ej: Enviar comprobante por WhatsApp" style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                            </div>
                            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                                <button type="submit" class="btn" style="background:#059669; padding:9px 18px; font-weight:bold;">
                                    💾 Guardar Datos de Cobro
                                </button>
                                <span style="font-size:0.8rem; color:#94a3b8;">
                                    Última actualización: <strong>{payment_settings.get('updated_at', 'Predeterminado')}</strong>
                                </span>
                            </div>
                        </form>
                    </div>

                    <!-- 2. Editor de Plantillas de WhatsApp -->
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom:1px solid #1e293b; padding-bottom:10px; flex-wrap:wrap; gap:10px;">
                            <div>
                                <h3 id="editor-title-display" style="margin:0; color:#38bdf8; font-size:1.15rem;">
                                    Cobro / Recordatorio Individual
                                </h3>
                                <p id="editor-desc-display" style="margin:4px 0 0 0; font-size:0.85rem; color:#94a3b8;">
                                    Plantilla enviada cuando vence una suscripción individual.
                                </p>
                            </div>
                            <!-- Selector de Plantillas -->
                            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                                <button type="button" id="tpl-btn-cobro" class="tpl-subtab-btn active" onclick="switchTemplateTab('cobro')">🔔 Cobro Individual</button>
                                <button type="button" id="tpl-btn-cobro_consolidado" class="tpl-subtab-btn" onclick="switchTemplateTab('cobro_consolidado')">🧾 Cobro Consolidado</button>
                                <button type="button" id="tpl-btn-entrega" class="tpl-subtab-btn" onclick="switchTemplateTab('entrega')">🍿 Entrega de Accesos</button>
                                <button type="button" id="tpl-btn-reemplazo" class="tpl-subtab-btn" onclick="switchTemplateTab('reemplazo')">🛠️ Reemplazo por Caída</button>
                            </div>
                        </div>

                        <!-- Barra de Etiquetas Dinámicas (Chips interactivos) -->
                        <div style="margin-bottom:14px; background:#161e2e; border:1px solid #334155; border-radius:8px; padding:12px;">
                            <div style="font-size:0.8rem; color:#cbd5e1; margin-bottom:8px; font-weight:600; display:flex; align-items:center; gap:6px;">
                                <span>🏷️ Etiquetas Dinámicas (Haz clic para insertar en la posición del cursor):</span>
                            </div>
                            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{cliente}}')">+ {{cliente}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{plataforma}}')">+ {{plataforma}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{email}}')">+ {{email}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{password}}')">+ {{password}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{perfil}}')">+ {{perfil}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{pin}}')">+ {{pin}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{vencimiento}}')">+ {{vencimiento}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{dias_restantes}}')">+ {{dias_restantes}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{monto}}')">+ {{monto}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{metodos_pago}}')" style="border-color:#10b981; color:#10b981;">+ {{metodos_pago}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{alias_mp}}')">+ {{alias_mp}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{cbu}}')">+ {{cbu}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{titular}}')">+ {{titular}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{banco}}')">+ {{banco}}</button>
                                <button type="button" class="template-chip" onclick="insertTemplateTag('{{servicios_lista}}')" style="border-color:#f59e0b; color:#f59e0b;">+ {{servicios_lista}}</button>
                            </div>
                        </div>

                        <!-- Editor a 2 Columnas: Textarea y Vista Previa WhatsApp -->
                        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap:20px; align-items:start;">
                            <!-- Columna 1: Editor Formulario -->
                            <div>
                                <form action="/api/templates/save" method="POST" id="form-save-template">
                                    <input type="hidden" name="template_key" id="editor-template-key" value="cobro">
                                    <input type="hidden" name="title" id="editor-template-title" value="">
                                    <input type="hidden" name="description" id="editor-template-desc" value="">
                                    <textarea name="content" id="editor-content" rows="18" oninput="updateTemplatePreview()" placeholder="Escribe el texto de la plantilla aquí..." style="width:100%; box-sizing:border-box; background:#161e2e; border:1px solid #334155; color:#f8fafc; border-radius:8px; padding:12px; font-family:monospace; font-size:0.85rem; line-height:1.5; resize:vertical;"></textarea>
                                    <div style="display:flex; gap:10px; margin-top:12px; flex-wrap:wrap; align-items:center;">
                                        <button type="submit" class="btn" style="background:#059669; padding:10px 20px; font-weight:bold; font-size:0.9rem;">
                                            💾 Guardar Cambios en Plantilla
                                        </button>
                                        <button type="submit" form="form-reset-template" class="btn btn-warn" style="padding:10px 14px; font-size:0.85rem;" onclick="return confirm('¿Restaurar esta plantilla a los textos predeterminados de fábrica?')">
                                            🔄 Restaurar Predeterminada
                                        </button>
                                    </div>
                                </form>
                                <form id="form-reset-template" action="/api/templates/reset/cobro" method="POST" style="display:none;"></form>
                            </div>

                            <!-- Columna 2: Vista Previa en Vivo WhatsApp -->
                            <div>
                                <div class="wa-preview-card">
                                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; border-bottom:1px solid #222e35; padding-bottom:8px;">
                                        <span style="color:#aebac1; font-size:0.85rem; font-weight:600;">💬 Vista Previa en Vivo (Estilo WhatsApp)</span>
                                        <span style="background:#1f2c34; color:#25d366; font-size:0.75rem; padding:3px 8px; border-radius:12px; font-weight:bold;">● Simulación</span>
                                    </div>
                                    <div class="wa-bubble">
                                        <div id="wa-preview-text">Cargando vista previa...</div>
                                        <div class="wa-bubble-time">
                                            <span>19:45</span>
                                            <span style="color:#53bdeb;">✓✓</span>
                                        </div>
                                    </div>
                                    <div style="margin-top:14px; font-size:0.75rem; color:#8696a0; text-align:center;">
                                        💡 Las variables se sustituirán automáticamente con los datos reales de cada cuenta y cliente al enviar el mensaje.
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div id="tab-suppliers" class="tab-content" style="display:none;">
                    <!-- 1. Cuentas Madre ante Proveedores Mayoristas -->
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px; margin-bottom:24px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom:1px solid #1e293b; padding-bottom:10px; flex-wrap:wrap; gap:10px;">
                            <div>
                                <h3 style="margin:0; color:#38bdf8; font-size:1.15rem; display:flex; align-items:center; gap:8px;">
                                    📺 Cuentas Madre & Vencimientos Mayoristas
                                </h3>
                                <p style="margin:4px 0 0 0; font-size:0.85rem; color:#94a3b8;">
                                    Monitoreo centralizado para prevenir cortes masivos: compara el vencimiento con tu proveedor vs el vencimiento de tus clientes.
                                </p>
                            </div>
                            <div>
                                <span class="badge badge-ok" style="font-size:0.8rem;">{len(master_accounts_list)} Cuentas Madre</span>
                            </div>
                        </div>

                        <div style="overflow-x:auto;">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Plataforma</th>
                                        <th>Correo Cuenta Madre</th>
                                        <th>Proveedor</th>
                                        <th>Vence Proveedor</th>
                                        <th>Perfiles Asignados</th>
                                        <th>Costo Mayorista</th>
                                        <th>Riesgo Desfase</th>
                                        <th>Acción</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {master_accounts_table_rows}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- 2. Directorio de Proveedores Mayoristas -->
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:20px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom:1px solid #1e293b; padding-bottom:10px; flex-wrap:wrap; gap:10px;">
                            <div>
                                <h3 style="margin:0; color:#38bdf8; font-size:1.15rem; display:flex; align-items:center; gap:8px;">
                                    🏢 Directorio de Proveedores Mayoristas
                                </h3>
                                <p style="margin:4px 0 0 0; font-size:0.85rem; color:#94a3b8;">
                                    Gestiona contactos directos, medios de pago (Alias / CBU / USDT) y gastos acumulados con cada mayorista.
                                </p>
                            </div>
                            <button type="button" onclick="openSupplierModal()" class="btn" style="background:#059669; font-weight:bold;">
                                ➕ Registrar Proveedor Mayorista
                            </button>
                        </div>

                        <div style="overflow-x:auto;">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Proveedor</th>
                                        <th>Contacto</th>
                                        <th>Datos de Pago</th>
                                        <th>Cuentas / Perfiles</th>
                                        <th>Total Abonado</th>
                                        <th>Notas / Garantía</th>
                                        <th>Acciones</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {suppliers_table_rows}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <div id="tab-logs" class="tab-content" style="display:none;">
                    <!-- Tarjetas de Diagnóstico y Salud -->
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:14px; margin-bottom:20px;">
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:10px; padding:14px;">
                            <small style="color:#94a3b8; text-transform:uppercase; font-size:0.7rem; font-weight:700;">Estado General</small>
                            <div style="margin-top:6px; display:flex; align-items:center; gap:8px;">
                                <span id="health-overall-status" class="badge {'badge-ok' if system_health['status'] == 'OK' else ('badge-warn' if system_health['status'] == 'WARNING' else 'badge-danger')}" style="font-size:0.95rem; padding:4px 10px;">
                                    {system_health['status']}
                                </span>
                            </div>
                            <small id="health-uptime" style="color:#64748b; margin-top:4px; display:block;">Uptime: {system_health['uptime']}</small>
                        </div>
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:10px; padding:14px;">
                            <small style="color:#94a3b8; text-transform:uppercase; font-size:0.7rem; font-weight:700;">Base de Datos SQLite</small>
                            <div style="margin-top:6px; font-size:1.1rem; font-weight:700; color:#38bdf8;">
                                {system_health['database']['total_accounts']} cuentas
                            </div>
                            <small style="color:#64748b; margin-top:4px; display:block;">{system_health['database']['size']} | {system_health['database']['status']}</small>
                        </div>
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:10px; padding:14px;">
                            <small style="color:#94a3b8; text-transform:uppercase; font-size:0.7rem; font-weight:700;">Bot de Telegram</small>
                            <div style="margin-top:6px; font-size:1.1rem; font-weight:700; color:#10b981;">
                                {system_health['telegram']['status']}
                            </div>
                            <small style="color:#64748b; margin-top:4px; display:block;">Polling interactivo activo</small>
                        </div>
                        <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:10px; padding:14px;">
                            <small style="color:#94a3b8; text-transform:uppercase; font-size:0.7rem; font-weight:700;">Errores / Advertencias</small>
                            <div style="margin-top:6px; font-size:1.1rem; font-weight:700; color:{'#ef4444' if system_health['logs_summary']['errors_count'] > 0 else '#10b981'};">
                                <span id="health-errors-count">{system_health['logs_summary']['errors_count']}</span> err / <span id="health-warns-count">{system_health['logs_summary']['warnings_count']}</span> warn
                            </div>
                            <small style="color:#64748b; margin-top:4px; display:block;">{system_health['logs_summary']['total_buffered']} eventos en buffer</small>
                        </div>
                    </div>

                    <!-- Barra de Controles del Terminal de Logs -->
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:18px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:12px;">
                            <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
                                <span style="font-size:0.8rem; color:#94a3b8; font-weight:600; margin-right:4px;">Nivel:</span>
                                <button type="button" id="filter-btn-all" class="filter-btn active" onclick="setLogFilterLevel('ALL')">TODOS</button>
                                <button type="button" id="filter-btn-error" class="filter-btn" onclick="setLogFilterLevel('ERROR')">🚨 ERRORES</button>
                                <button type="button" id="filter-btn-warning" class="filter-btn" onclick="setLogFilterLevel('WARNING')">⚠️ ADVERTENCIAS</button>
                                <button type="button" id="filter-btn-info" class="filter-btn" onclick="setLogFilterLevel('INFO')">ℹ️ INFO</button>
                            </div>
                            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                                <input type="text" id="log-search-input" onkeyup="filterLogsLocally()" placeholder="🔍 Buscar texto o excepción..." style="background:#161e2e; border:1px solid #334155; color:#fff; border-radius:6px; padding:6px 10px; font-size:0.8rem; min-width:200px;">
                                <button type="button" onclick="refreshSystemLogs()" class="btn" style="background:#1e293b; border:1px solid #334155; padding:6px 12px; font-size:0.8rem;">🔄 Actualizar</button>
                                <a href="/api/logs/download" class="btn" style="background:#0284c7; padding:6px 12px; font-size:0.8rem; text-decoration:none;">📥 Descargar TXT</a>
                                <form action="/api/logs/clear" method="POST" style="display:inline;" onsubmit="return confirm('¿Limpiar buffer de eventos en memoria? El archivo en disco se mantendrá.');">
                                    <button type="submit" class="btn" style="background:#7f1d1d; padding:6px 12px; font-size:0.8rem;">🧹 Limpiar</button>
                                </form>
                            </div>
                        </div>

                        <!-- Terminal Negro Interactivo -->
                        <div id="logs-terminal" class="log-terminal">
                            {initial_logs_html}
                        </div>
                    </div>
                </div>

                <div id="tab-oauth" class="tab-content" style="display:none;">
                    <div style="background:#0b0f19; border:1px solid #1e293b; border-radius:12px; padding:24px; margin-bottom:20px;">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:16px; margin-bottom:20px; border-bottom:1px solid #1e293b; padding-bottom:16px;">
                            <div>
                                <h3 style="margin:0 0 6px 0; color:#38bdf8; font-size:1.2rem; display:flex; align-items:center; gap:8px;">
                                    🤖 Conexión Segura con Gemini Spark (OAuth 2.0)
                                    <span class="badge {'badge-ok' if oauth_enabled else 'badge-warn'}" style="font-size:0.75rem;">
                                        {'🔒 Protección OAuth Activa' if oauth_enabled else '⚠️ Protección Desactivada'}
                                    </span>
                                </h3>
                                <p style="margin:0; color:#94a3b8; font-size:0.85rem; line-height:1.4;">
                                    Tu servidor MCP exige autenticación estándar OAuth 2.0 (Client ID y Client Secret). Nadie en internet puede usar tus herramientas MCP sin estas credenciales.
                                </p>
                            </div>
                            <form action="/api/oauth/regenerate" method="POST" onsubmit="return confirm('¿Seguro que deseas regenerar el Client Secret? Deberás actualizarlo en Gemini Spark.');">
                                <button type="submit" class="btn" style="background:#1e293b; border:1px solid #eab308; color:#facc15; font-size:0.82rem; padding:8px 14px; cursor:pointer; border-radius:6px;">
                                    🔄 Regenerar Secreto de Cliente
                                </button>
                            </form>
                        </div>

                        <!-- Campos para Copiar en Gemini Spark -->
                        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px; margin-bottom:24px;">
                            <div style="background:#161e2e; border:1px solid #334155; border-radius:8px; padding:14px;">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:6px; font-weight:600; text-transform:uppercase;">
                                    1. Enlace de aplicación (MCP URL)
                                </label>
                                <div style="display:flex; gap:8px;">
                                    <input type="text" id="copy-mcp-url" value="https://mcp.joif.net/mcp/" readonly style="flex:1; background:#0b0f19; border:1px solid #475569; color:#38bdf8; border-radius:6px; padding:8px 10px; font-size:0.88rem; font-family:monospace;">
                                    <button type="button" onclick="navigator.clipboard.writeText('https://mcp.joif.net/mcp/'); alert('¡Enlace MCP copiado!');" style="background:#0284c7; color:#fff; border:none; border-radius:6px; padding:8px 12px; cursor:pointer; font-weight:600; font-size:0.8rem;">Copiar</button>
                                </div>
                            </div>

                            <div style="background:#161e2e; border:1px solid #334155; border-radius:8px; padding:14px;">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:6px; font-weight:600; text-transform:uppercase;">
                                    2. ID de cliente (OAuth)
                                </label>
                                <div style="display:flex; gap:8px;">
                                    <input type="text" id="copy-client-id" value="{oauth_client_id}" readonly style="flex:1; background:#0b0f19; border:1px solid #475569; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.88rem; font-family:monospace;">
                                    <button type="button" onclick="navigator.clipboard.writeText('{oauth_client_id}'); alert('¡ID de cliente copiado!');" style="background:#0284c7; color:#fff; border:none; border-radius:6px; padding:8px 12px; cursor:pointer; font-weight:600; font-size:0.8rem;">Copiar</button>
                                </div>
                            </div>

                            <div style="background:#161e2e; border:1px solid #334155; border-radius:8px; padding:14px;">
                                <label style="display:block; font-size:0.75rem; color:#94a3b8; margin-bottom:6px; font-weight:600; text-transform:uppercase;">
                                    3. Secreto de cliente (OAuth)
                                </label>
                                <div style="display:flex; gap:8px;">
                                    <input type="password" id="copy-client-secret" value="{oauth_client_secret}" readonly style="flex:1; background:#0b0f19; border:1px solid #475569; color:#facc15; border-radius:6px; padding:8px 10px; font-size:0.88rem; font-family:monospace;">
                                    <button type="button" onclick="var el=document.getElementById('copy-client-secret'); el.type = el.type==='password'?'text':'password';" style="background:#334155; color:#cbd5e1; border:none; border-radius:6px; padding:8px 10px; cursor:pointer; font-size:0.8rem;">👁️</button>
                                    <button type="button" onclick="navigator.clipboard.writeText('{oauth_client_secret}'); alert('¡Secreto de cliente copiado!');" style="background:#0284c7; color:#fff; border:none; border-radius:6px; padding:8px 12px; cursor:pointer; font-weight:600; font-size:0.8rem;">Copiar</button>
                                </div>
                            </div>
                        </div>

                        <!-- Configuración Avanzada -->
                        <form action="/api/oauth/settings" method="POST" style="background:#161e2e; border:1px solid #1e293b; border-radius:10px; padding:20px;">
                            <h4 style="margin:0 0 14px 0; color:#cbd5e1; font-size:0.95rem;">⚙️ Configuración del Servidor OAuth 2.0</h4>
                            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:16px; margin-bottom:16px;">
                                <div>
                                    <label style="display:block; font-size:0.78rem; color:#94a3b8; margin-bottom:4px;">ID de Cliente</label>
                                    <input type="text" name="client_id" value="{oauth_client_id}" required style="width:100%; box-sizing:border-box; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.78rem; color:#94a3b8; margin-bottom:4px;">Secreto de Cliente</label>
                                    <input type="text" name="client_secret" value="{oauth_client_secret}" required style="width:100%; box-sizing:border-box; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                                <div>
                                    <label style="display:block; font-size:0.78rem; color:#94a3b8; margin-bottom:4px;">URI de Redirección Autorizadas</label>
                                    <input type="text" name="redirect_uris" value="{oauth_redirect_uris}" style="width:100%; box-sizing:border-box; background:#0b0f19; border:1px solid #334155; color:#fff; border-radius:6px; padding:8px 10px; font-size:0.85rem;">
                                </div>
                            </div>

                            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                                <label style="display:flex; align-items:center; gap:8px; font-size:0.85rem; color:#cbd5e1; cursor:pointer;">
                                    <input type="checkbox" name="enabled" value="1" {'checked' if oauth_enabled else ''} style="width:16px; height:16px;">
                                    <b>Exigir autenticación OAuth 2.0 para el servidor MCP</b>
                                </label>
                                <button type="submit" class="btn" style="background:#0284c7; color:#fff; padding:8px 16px; border:none; border-radius:6px; font-weight:600; cursor:pointer;">Guardar Ajustes OAuth</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>

        <!-- Modal para Vender Combo Rápido -->
        <div id="modal-sell-combo" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:9999;align-items:center;justify-content:center;">
            <div style="background:#1e293b;border:1px solid #475569;border-radius:12px;padding:24px;width:90%;max-width:480px;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;border-bottom:1px solid #334155;padding-bottom:10px;">
                    <h3 id="modal-combo-title" style="margin:0;color:#38bdf8;font-size:1.15rem;">⚡ Vender Combo</h3>
                    <button type="button" onclick="closeSellComboModal()" style="background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;">✕</button>
                </div>
                <form action="/api/combos/sell" method="POST">
                    <input type="hidden" id="modal-combo-id" name="combo_id" value="">
                    
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Nombre del Cliente *</label>
                        <input type="text" name="client_name" required placeholder="Ej: Lucas Martínez" style="width:100%;box-sizing:border-box;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;font-size:0.9rem;">
                    </div>
                    
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">WhatsApp (con código de país) *</label>
                        <input type="text" name="whatsapp" required placeholder="+54 9 11 2233 4455" style="width:100%;box-sizing:border-box;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;font-size:0.9rem;">
                    </div>

                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                        <div>
                            <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Tipo de Cliente</label>
                            <select name="client_type" style="width:100%;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;font-size:0.85rem;">
                                <option value="consumidor_final">👤 Final</option>
                                <option value="revendedor">👔 Revendedor</option>
                            </select>
                        </div>
                        <div>
                            <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Medio de Pago</label>
                            <select name="payment_method" style="width:100%;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;font-size:0.85rem;">
                                <option value="Transferencia">Transferencia / CVU</option>
                                <option value="Mercado Pago">Mercado Pago</option>
                                <option value="Efectivo">Efectivo</option>
                                <option value="Binance USDT">Binance USDT</option>
                            </select>
                        </div>
                    </div>

                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px;">
                        <div>
                            <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Días de Validez</label>
                            <input type="number" name="duration_days" value="30" min="1" max="365" style="width:100%;box-sizing:border-box;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;font-size:0.85rem;">
                        </div>
                        <div>
                            <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Telegram (opcional)</label>
                            <input type="text" name="telegram" placeholder="@usuario" style="width:100%;box-sizing:border-box;background:#0b0f19;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px;font-size:0.85rem;">
                        </div>
                    </div>

                    <div style="display:flex;justify-content:flex-end;gap:10px;border-top:1px solid #334155;padding-top:14px;">
                        <button type="button" onclick="closeSellComboModal()" class="btn" style="background:#475569;padding:8px 16px;">Cancelar</button>
                        <button type="submit" class="btn" style="background:#059669;padding:8px 16px;font-weight:bold;">🚀 Confirmar Venta de Combo</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Modal Ficha 360° del Cliente -->
        <div id="modal-client-360" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;">
            <div style="background:#1e293b;border:1px solid #475569;border-radius:14px;padding:22px;width:100%;max-width:760px;box-shadow:0 25px 50px -12px rgba(0,0,0,0.7);max-height:90vh;overflow-y:auto;box-sizing:border-box;">
                <!-- Header del Modal -->
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;border-bottom:1px solid #334155;padding-bottom:12px;">
                    <div>
                        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                            <h2 id="m360-name" style="margin:0;color:#f8fafc;font-size:1.3rem;">Cargando perfil...</h2>
                            <span id="m360-type-badge" class="badge" style="background:#0284c7;color:#fff;">Tipo</span>
                            <span id="m360-health-badge" class="badge">Estado</span>
                        </div>
                        <div style="display:flex;gap:14px;margin-top:6px;font-size:0.8rem;color:#94a3b8;flex-wrap:wrap;">
                            <span id="m360-code">Código: -</span>
                            <span id="m360-wa-wrap">WhatsApp: <a id="m360-wa-link" href="#" target="_blank" style="color:#22c55e;">-</a></span>
                            <span id="m360-tg-wrap">Telegram: <a id="m360-tg-link" href="#" target="_blank" style="color:#38bdf8;">-</a></span>
                        </div>
                    </div>
                    <button type="button" onclick="closeClient360Modal()" style="background:none;border:none;color:#94a3b8;font-size:1.5rem;cursor:pointer;padding:0 6px;">✕</button>
                </div>

                <!-- Botón Destacado: Cobro Consolidado WhatsApp (1 Clic) -->
                <div id="m360-consolidated-box" style="background:linear-gradient(90deg, #064e3b, #047857);border:1px solid #059669;border-radius:10px;padding:12px 16px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
                    <div>
                        <div style="font-weight:bold;color:#ecfdf5;font-size:0.95rem;">📲 Cobro Consolidado WhatsApp (1 Clic)</div>
                        <div id="m360-consolidated-desc" style="font-size:0.8rem;color:#a7f3d0;margin-top:2px;">Envía un recordatorio único agrupando todas sus suscripciones en ARS.</div>
                    </div>
                    <a id="m360-consolidated-btn" href="#" target="_blank" class="btn" style="background:#10b981;color:#022c22;font-weight:bold;padding:8px 14px;text-decoration:none;border-radius:7px;display:inline-block;white-space:nowrap;">
                        💬 Enviar Cobro Consolidado
                    </a>
                </div>

                <!-- Grid de KPIs Financieros -->
                <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));gap:10px;margin-bottom:16px;">
                    <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;text-align:center;">
                        <span style="font-size:0.75rem;color:#94a3b8;display:block;">LTV (Total Cobrado)</span>
                        <strong id="m360-ltv" style="font-size:1.1rem;color:#38bdf8;">$ 0 ARS</strong>
                        <small id="m360-payments-count" style="display:block;font-size:0.7rem;color:#64748b;">0 pagos</small>
                    </div>
                    <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;text-align:center;">
                        <span style="font-size:0.75rem;color:#94a3b8;display:block;">Ganancia Neta Real</span>
                        <strong id="m360-profit" style="font-size:1.1rem;color:#34d399;">$ 0 ARS</strong>
                        <small style="display:block;font-size:0.7rem;color:#64748b;">margen neto</small>
                    </div>
                    <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;text-align:center;">
                        <span style="font-size:0.75rem;color:#94a3b8;display:block;">Facturación Mensual</span>
                        <strong id="m360-monthly" style="font-size:1.1rem;color:#facc15;">$ 0 ARS</strong>
                        <small style="display:block;font-size:0.7rem;color:#64748b;">por mes</small>
                    </div>
                    <div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;text-align:center;">
                        <span style="font-size:0.75rem;color:#94a3b8;display:block;">Suscripciones Activas</span>
                        <strong id="m360-accounts-count" style="font-size:1.1rem;color:#e2e8f0;">0</strong>
                        <small id="m360-health-summary" style="display:block;font-size:0.7rem;color:#94a3b8;">-</small>
                    </div>
                </div>

                <!-- Secciones: Cuentas Activas e Historial -->
                <div style="margin-bottom:16px;">
                    <h4 style="margin:0 0 8px 0;font-size:0.9rem;color:#e2e8f0;">📺 Suscripciones Activas</h4>
                    <div id="m360-accounts-table-wrap" style="overflow-x:auto;"></div>
                </div>

                <div>
                    <h4 style="margin:14px 0 8px 0;font-size:0.9rem;color:#e2e8f0;">💵 Historial de Pagos Anteriores</h4>
                    <div id="m360-payments-table-wrap" style="overflow-x:auto;"></div>
                </div>

                <div style="display:flex;justify-content:flex-end;margin-top:16px;border-top:1px solid #334155;padding-top:12px;">
                    <button type="button" onclick="closeClient360Modal()" class="btn" style="background:#475569;padding:8px 16px;">Cerrar Ficha</button>
                </div>
            </div>
        </div>

        <!-- Modal: Registrar / Editar Proveedor Mayorista -->
        <div id="modal-supplier" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:9999;align-items:center;justify-content:center;">
            <div style="background:#1e293b;border:1px solid #475569;border-radius:12px;padding:24px;width:90%;max-width:500px;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;border-bottom:1px solid #334155;padding-bottom:10px;">
                    <h3 id="sup-modal-title" style="margin:0;color:#38bdf8;font-size:1.15rem;">🏢 Proveedor Mayorista</h3>
                    <button type="button" onclick="closeSupplierModal()" style="background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;">✕</button>
                </div>
                <form action="/api/suppliers/save" method="POST">
                    <input type="hidden" id="sup-modal-id" name="supplier_id" value="">
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Nombre del Proveedor *</label>
                        <input type="text" id="sup-modal-name" name="name" required placeholder="Ej: Streaming Mayorista ARG" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Contacto (WhatsApp o @Telegram)</label>
                        <input type="text" id="sup-modal-contact" name="contact" placeholder="+54911... o @proveedor_streaming" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Datos de Pago (CBU / Alias / USDT)</label>
                        <input type="text" id="sup-modal-payment" name="payment_info" placeholder="Alias MP, CVU o Binance Pay" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                    </div>
                    <div style="margin-bottom:16px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Condiciones / Notas de Garantía</label>
                        <textarea id="sup-modal-notes" name="notes" rows="3" placeholder="Garantía de 30 días, horario de reposición 9 a 21hs..." style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;resize:vertical;"></textarea>
                    </div>
                    <div style="display:flex;justify-content:flex-end;gap:10px;">
                        <button type="button" onclick="closeSupplierModal()" class="btn" style="background:#475569;">Cancelar</button>
                        <button type="submit" class="btn" style="background:#059669;font-weight:bold;">💾 Guardar Proveedor</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Modal: Renovar Cuenta Madre ante Proveedor -->
        <div id="modal-renew-master" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:9999;align-items:center;justify-content:center;">
            <div style="background:#1e293b;border:1px solid #475569;border-radius:12px;padding:24px;width:90%;max-width:480px;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;border-bottom:1px solid #334155;padding-bottom:10px;">
                    <h3 style="margin:0;color:#38bdf8;font-size:1.15rem;">🔄 Renovar Cuenta Madre</h3>
                    <button type="button" onclick="closeRenewMasterModal()" style="background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;">✕</button>
                </div>
                <form action="/api/master-accounts/renew" method="POST">
                    <input type="hidden" id="rm-modal-email" name="email" value="">
                    <input type="hidden" id="rm-modal-plat" name="platform" value="">
                    <div style="margin-bottom:14px;background:#0f172a;padding:10px 14px;border-radius:8px;border:1px solid #334155;">
                        <span style="font-size:0.75rem;color:#94a3b8;display:block;">Cuenta a Renovar:</span>
                        <strong id="rm-display-account" style="color:#f8fafc;font-size:0.95rem;">-</strong>
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Nueva Fecha de Vencimiento Mayorista *</label>
                        <input type="date" id="rm-modal-expiry" name="new_supplier_expiry" required style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Costo Pagado al Proveedor ($ ARS)</label>
                        <input type="number" step="any" id="rm-modal-cost" name="cost" placeholder="3200" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Método de Pago Empleado</label>
                        <select name="payment_method" style="width:100%;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                            <option value="Transferencia Bancaria">Transferencia Bancaria / CBU</option>
                            <option value="Mercado Pago">Mercado Pago</option>
                            <option value="Binance USDT">Binance / USDT</option>
                            <option value="Efectivo">Efectivo</option>
                        </select>
                    </div>
                    <div style="margin-bottom:16px;">
                        <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;">Notas o Comprobante</label>
                        <input type="text" name="notes" placeholder="Ej: Comprobante MP #827391" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;padding:8px 10px;font-size:0.85rem;">
                    </div>
                    <div style="display:flex;justify-content:flex-end;gap:10px;">
                        <button type="button" onclick="closeRenewMasterModal()" class="btn" style="background:#475569;">Cancelar</button>
                        <button type="submit" class="btn" style="background:#0284c7;font-weight:bold;">⚡ Confirmar Renovación</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Modal: Escanear QR WhatsApp Evolution API -->
        <div id="modal-wa-qr" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;align-items:center;justify-content:center;">
            <div style="background:#161e2e;border:1px solid #334155;border-radius:16px;padding:28px;width:90%;max-width:440px;box-shadow:0 25px 50px -12px rgba(0,0,0,0.7);text-align:center;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;border-bottom:1px solid #1e293b;padding-bottom:12px;">
                    <h3 style="margin:0;color:#25d366;font-size:1.2rem;display:flex;align-items:center;gap:8px;">
                        📲 Vincular WhatsApp
                    </h3>
                    <button type="button" onclick="closeWaQrModal()" style="background:none;border:none;color:#94a3b8;font-size:1.4rem;cursor:pointer;">✕</button>
                </div>
                <p id="wa-qr-loading" style="font-size:0.9rem;color:#cbd5e1;margin-bottom:16px;">
                    🔄 Obteniendo código QR...
                </p>
                <div style="display:flex;justify-content:center;margin-bottom:16px;">
                    <img id="wa-qr-img" src="" alt="WhatsApp QR Code" style="display:none;width:260px;height:260px;border-radius:12px;background:#ffffff;padding:12px;box-shadow:0 4px 12px rgba(0,0,0,0.4);" />
                </div>
                <div id="wa-qr-pairing" style="font-size:0.85rem;color:#38bdf8;margin-bottom:16px;"></div>
                <p style="font-size:0.8rem;color:#94a3b8;line-height:1.4;margin-bottom:20px;">
                    1. Abre WhatsApp en tu celular.<br>
                    2. Ve a <strong>Ajustes > Dispositivos vinculados</strong>.<br>
                    3. Toca en <strong>Vincular un dispositivo</strong> y apunta al código.
                </p>
                <div style="display:flex;justify-content:center;gap:10px;">
                    <button type="button" onclick="loadWaQr()" class="btn" style="background:#1e293b;border:1px solid #334155;color:#38bdf8;">🔄 Recargar QR</button>
                    <button type="button" onclick="closeWaQrModal()" class="btn" style="background:#475569;">Cerrar</button>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# Acciones Rápidas de API
@app.get("/api/client/360/{client_id}")
async def api_get_client_360(client_id: str, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    profile = database.get_client_360_profile(client_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return profile

@app.get("/api/templates/json")
async def api_get_templates_json(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")
    return {
        "templates": database.get_whatsapp_templates(),
        "payment_settings": database.get_payment_settings(),
        "formatted_payment_methods": database.get_formatted_payment_methods()
    }

@app.post("/api/settings/payment")
async def api_save_payment_settings(
    request: Request,
    alias_mp: str = Form(""),
    cvu_cbu: str = Form(""),
    account_holder: str = Form(""),
    bank_name: str = Form(""),
    usdt_address: str = Form(""),
    extra_instructions: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_payment_settings(
        alias_mp=alias_mp,
        cvu_cbu=cvu_cbu,
        account_holder=account_holder,
        bank_name=bank_name,
        usdt_address=usdt_address,
        extra_instructions=extra_instructions
    )
    return RedirectResponse(url="/?msg=payment_settings_saved#tab-templates", status_code=302)

@app.post("/api/templates/save")
async def api_save_template(
    request: Request,
    template_key: str = Form(...),
    content: str = Form(...),
    title: str = Form(""),
    description: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_whatsapp_template(
        template_key=template_key,
        content=content,
        title=title,
        description=description
    )
    return RedirectResponse(url="/?msg=template_saved#tab-templates", status_code=302)

@app.post("/api/templates/reset/{template_key}")
async def api_reset_template(template_key: str, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.reset_whatsapp_template(template_key)
    return RedirectResponse(url="/?msg=template_reset#tab-templates", status_code=302)

@app.post("/api/collect-payment/{account_id}")
async def collect_payment_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.register_customer_payment(email_or_id=str(account_id), payment_method="Panel Web")
    if res.get("success"):
        wa_data = database.generate_whatsapp_message(str(account_id), message_type="entrega")
        wa_url = wa_data.get("wa_link", "")
        wa_link_html = f"\n\n📲 <a href=\"{wa_url}\"><b>👉 ENVIAR COMPROBANTE Y DATOS POR WHATSAPP (1 Clic)</b></a>" if wa_url else ""
        await send_telegram_message(
            f"💵 <b>Cobro y Renovación Registrados</b>\n\n"
            f"• Cliente: {res['client_name']}\n"
            f"• Servicio: {res['platform']}\n"
            f"• Monto cobrado: {database.format_ars(res['amount'])}\n"
            f"• Ganancia Neta: +{database.format_ars(res['profit'])}\n"
            f"• Próximo vencimiento: {res['new_expiry']}"
            f"{wa_link_html}"
        )
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/catalog/save")
async def api_catalog_save(
    request: Request,
    platform: str = Form(...),
    service_type: str = Form("pantalla"),
    cost_price: float = Form(0.0),
    price_final: float = Form(0.0),
    price_reseller: float = Form(0.0),
    notes: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.upsert_catalog_price(
        platform=platform,
        service_type=service_type,
        cost_price=cost_price,
        price_final=price_final,
        price_reseller=price_reseller,
        notes=notes
    )
    return RedirectResponse(url="/?msg=catalog_saved#tab-catalog", status_code=302)

@app.post("/api/catalog/delete/{price_id}")
async def api_catalog_delete(price_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_catalog_price(price_id)
    return RedirectResponse(url="/?msg=catalog_deleted#tab-catalog", status_code=302)

@app.post("/api/combos/save")
async def api_combos_save(
    request: Request,
    name: str = Form(...),
    platforms: str = Form(...),
    price_final: float = Form(...),
    price_reseller: float = Form(...),
    description: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    plat_list = [p.strip() for p in platforms.split(",") if p.strip()]
    database.create_or_update_combo(
        name=name,
        description=description,
        price_final=price_final,
        price_reseller=price_reseller,
        platforms=plat_list
    )
    return RedirectResponse(url="/?msg=combo_saved#tab-catalog", status_code=302)

@app.post("/api/combos/delete/{combo_id}")
async def api_combos_delete(combo_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_combo(combo_id)
    return RedirectResponse(url="/?msg=combo_deleted#tab-catalog", status_code=302)

@app.post("/api/combos/sell")
async def api_combos_sell(
    request: Request,
    combo_id: int = Form(...),
    client_name: str = Form(...),
    whatsapp: str = Form(""),
    telegram: str = Form(""),
    client_type: str = Form("consumidor_final"),
    payment_method: str = Form("Transferencia"),
    duration_days: int = Form(30),
    notes: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.sell_combo(
        combo_name_or_id=combo_id,
        client_name=client_name,
        whatsapp=whatsapp,
        telegram=telegram,
        client_type=client_type,
        payment_method=payment_method,
        duration_days=duration_days,
        notes=notes
    )
    if res.get("success"):
        wa_url = res.get("wa_link", "")
        wa_text = res.get("whatsapp_message", "")
        wa_auto_sent = False
        if whatsapp:
            wa_settings = database.get_whatsapp_api_settings()
            if wa_settings.get("auto_send_sales") == 1:
                try:
                    wa_st = await whatsapp_client.check_connection_status()
                    if wa_st.get("connected"):
                        send_res = await whatsapp_client.send_text_message(whatsapp, wa_text, delay_seconds=2.0)
                        if send_res.get("success"):
                            wa_auto_sent = True
                            logger.info(f"Accesos de combo despachados automáticamente a {whatsapp}")
                except Exception as e:
                    logger.error(f"Error despachando combo automáticamente por WhatsApp: {e}")

        wa_info_telegram = "\n📲 <b>WhatsApp:</b> ✅ Entregado automáticamente al cliente" if wa_auto_sent else f"\n\n📲 <a href=\"{wa_url}\"><b>👉 ENVIAR ACCESOS POR WHATSAPP (1 Clic)</b></a>"
        await send_telegram_message(
            f"🎉 <b>¡Combo Vendido desde el Panel Web!</b>\n\n"
            f"• Pack: <b>{res['combo_name']}</b>\n"
            f"• Cliente: {res['client_name']} ({client_type})\n"
            f"• Total Cobrado: <b>{database.format_ars(res['amount'])}</b>\n"
            f"• Ganancia Neta: +{database.format_ars(res['profit'])}\n"
            f"• Cuentas asignadas: {len(res['accounts'])}\n"
            f"• Vencimiento: <code>{res['expiry_date']}</code>"
            f"{wa_info_telegram}"
        )
        return RedirectResponse(url=f"/?msg=combo_sold&wa={urllib.parse.quote(wa_url)}#tab-active", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error al vender combo"))
        return RedirectResponse(url=f"/?err={err}#tab-catalog", status_code=302)

@app.post("/api/mark-fallen/{account_id}")
async def mark_fallen_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.mark_account_fallen(str(account_id), reason="Marcada desde el Panel")
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/auto-replace/{account_id}")
async def auto_replace_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = database.replace_fallen_account(str(account_id))
    if res and res.get("success"):
        new_a = res["new_account"]
        wa_data = database.generate_whatsapp_message(new_a, message_type="reemplazo")
        wa_url = wa_data.get("wa_link", "")
        wa_link_html = f"\n\n📲 <a href=\"{wa_url}\"><b>👉 ENVIAR NUEVA CUENTA POR WHATSAPP (1 Clic)</b></a>" if wa_url else ""
        msg = (
            f"✅ Reemplazo exitoso para <b>{new_a.get('client_name')}</b>:\n"
            f"• Plataforma: {new_a['platform']}\n"
            f"• Nueva cuenta: <code>{new_a['email']}</code>\n"
            f"• Clave: <code>{new_a['password']}</code>"
        )
        await send_telegram_message(f"🔄 <b>Reemplazo de Cuenta</b>\n\n{msg}{wa_link_html}")
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/delete-account/{account_id}")
async def delete_account_api(account_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_account(account_id)
    return RedirectResponse(url="/", status_code=302)

@app.post("/api/test-telegram")
async def test_telegram_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    ok = await send_telegram_message("🔔 <b>Prueba de Telegram exitosa desde el Panel Web de Streaming CRM</b>")
    return JSONResponse({"ok": ok})

@app.post("/api/check-now")
async def check_now_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    sent = await check_and_send_alerts(days_window=7, force=True)
    return JSONResponse({"ok": True, "alertas_enviadas": sent})

@app.post("/api/check-stock-alert")
async def check_stock_alert_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    from telegram_bot import format_and_send_stock_alert
    await format_and_send_stock_alert()
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/set-stock-threshold")
async def set_stock_threshold_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    form = await request.form()
    platform = str(form.get("platform", "")).strip()
    try:
        min_stock = int(form.get("min_stock", 2))
    except ValueError:
        min_stock = 2
    if platform:
        database.set_platform_min_stock(platform, min_stock)
    return RedirectResponse(url="/", status_code=303)

# ==========================================
# Endpoints de Exportación e Importación (Excel / CSV)
# ==========================================
@app.get("/api/export/active.csv")
async def export_active_csv(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.export_active_accounts_csv()
    filename = f"crm_cuentas_activas_{date.today().strftime('%Y%m%d')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/export/stock.csv")
async def export_stock_csv(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.export_free_stock_csv()
    filename = f"crm_stock_libre_{date.today().strftime('%Y%m%d')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/export/transactions.csv")
async def export_transactions_csv_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.export_transactions_csv()
    filename = f"crm_balance_transacciones_{date.today().strftime('%Y%m%d')}.csv"
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/export/template-stock.csv")
async def export_template_stock(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.get_csv_template_stock()
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_stock_modelo.csv"}
    )

@app.get("/api/export/template-sales.csv")
async def export_template_sales(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = database.get_csv_template_sales()
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_ventas_modelo.csv"}
    )

@app.post("/api/import/stock")
async def import_stock_api(request: Request, file: Optional[UploadFile] = None, csv_text: Optional[str] = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    
    content = ""
    if file and file.filename:
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif csv_text and csv_text.strip():
        content = csv_text.strip()
    
    if not content:
        msg = urllib.parse.quote("⚠️ No se proporcionó ningún archivo ni texto CSV.")
        return RedirectResponse(url=f"/?msg={msg}", status_code=303)
    
    res = database.import_free_stock_csv(content)
    if res.get("success"):
        msg = urllib.parse.quote(f"✅ Se importaron con éxito {res['imported']} cuenta(s) al stock libre ({res['skipped']} omitidas).")
    else:
        msg = urllib.parse.quote(f"❌ Error al importar: {res.get('error')}")
    return RedirectResponse(url=f"/?msg={msg}", status_code=303)

@app.post("/api/import/sales")
async def import_sales_api(request: Request, file: Optional[UploadFile] = None, csv_text: Optional[str] = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    
    content = ""
    if file and file.filename:
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif csv_text and csv_text.strip():
        content = csv_text.strip()
    
    if not content:
        msg = urllib.parse.quote("⚠️ No se proporcionó ningún archivo ni texto CSV.")
        return RedirectResponse(url=f"/?msg={msg}", status_code=303)
    
    res = database.import_sales_csv(content)
    if res.get("success"):
        msg = urllib.parse.quote(f"✅ Se migraron con éxito {res['imported']} venta(s) y cliente(s) ({res['skipped']} omitidas).")
    else:
        msg = urllib.parse.quote(f"❌ Error al importar: {res.get('error')}")
    return RedirectResponse(url=f"/?msg={msg}", status_code=303)

@app.post("/api/trigger-backup")
async def trigger_backup_api(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    from telegram_bot import send_full_backup_to_telegram
    await send_full_backup_to_telegram()
    msg = urllib.parse.quote("📲 Copia de seguridad enviada exitosamente a tu Telegram.")
    return RedirectResponse(url=f"/?msg={msg}", status_code=303)

# API Proveedores y Cuentas Madre (Paso 4)
@app.post("/api/suppliers/save")
async def api_save_supplier(
    request: Request,
    name: str = Form(...),
    contact: str = Form(""),
    payment_info: str = Form(""),
    notes: str = Form(""),
    supplier_id: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    
    sid = int(supplier_id) if supplier_id and supplier_id.strip() else None
    database.save_supplier(
        supplier_id=sid,
        name=name.strip(),
        contact=contact.strip(),
        payment_info=payment_info.strip(),
        notes=notes.strip()
    )
    return RedirectResponse(url="/?msg=supplier_saved#tab-suppliers", status_code=303)

@app.post("/api/suppliers/delete/{supplier_id}")
async def api_delete_supplier(supplier_id: int, request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.delete_supplier(supplier_id)
    return RedirectResponse(url="/?msg=supplier_deleted#tab-suppliers", status_code=303)

@app.post("/api/master-accounts/renew")
async def api_renew_master(
    request: Request,
    email: str = Form(...),
    platform: str = Form(...),
    new_supplier_expiry: str = Form(...),
    cost: float = Form(0.0),
    payment_method: str = Form("Transferencia"),
    notes: str = Form("")
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.renew_master_account(
        email=email.strip(),
        platform=platform.strip(),
        new_supplier_expiry=new_supplier_expiry.strip(),
        cost=cost,
        payment_method=payment_method.strip(),
        notes=notes.strip()
    )
    return RedirectResponse(url="/?msg=master_renewed#tab-suppliers", status_code=303)

# API Logs y Diagnóstico del Sistema
@app.get("/api/logs/json")
async def api_get_logs_json(request: Request, level: str = "ALL", query: str = ""):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    logs = system_logger.get_recent_logs(level=level, query=query, limit=120)
    health = system_logger.get_system_health_report()
    return {"logs": logs, "health": health}

@app.get("/api/logs/download")
async def api_download_logs(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    content = system_logger.get_raw_log_file(max_lines=3000)
    today_str = datetime.now().strftime("%Y%m%d_%H%M")
    return Response(
        content=content.encode("utf-8", errors="replace"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=system_logs_{today_str}.txt"}
    )

@app.post("/api/logs/clear")
async def api_clear_logs(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    system_logger.clear_memory_logs()
    return RedirectResponse(url="/?msg=logs_cleared#tab-logs", status_code=303)

# ==========================================
# 12. Endpoints Evolution API WhatsApp & Webhooks
# ==========================================
@app.get("/api/whatsapp/status")
async def api_whatsapp_status(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    st = await whatsapp_client.check_connection_status()
    cfg = whatsapp_client.get_evolution_config()
    return {"status": st, "config": cfg}

@app.get("/api/whatsapp/qr")
async def api_whatsapp_qr(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    qr_data = await whatsapp_client.get_qr_code()
    return qr_data

@app.post("/api/whatsapp/settings")
async def api_whatsapp_settings(
    request: Request,
    api_url: str = Form("http://evolution-api:8080"),
    api_key: str = Form("mcp-evolution-key-2026"),
    instance_name: str = Form("streaming-bot"),
    auto_send_expiry: Optional[str] = Form(None),
    auto_send_sales: Optional[str] = Form(None),
    auto_reply_enabled: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_whatsapp_api_settings(
        api_url=api_url.strip(),
        api_key=api_key.strip(),
        instance_name=instance_name.strip(),
        auto_send_expiry=1 if auto_send_expiry in ("1", "on", "true") else 0,
        auto_send_sales=1 if auto_send_sales in ("1", "on", "true") else 0,
        auto_reply_enabled=1 if auto_reply_enabled in ("1", "on", "true") else 0
    )
    return RedirectResponse(url="/?msg=wa_settings_saved#tab-templates", status_code=302)

@app.post("/api/whatsapp/setup-webhook")
async def api_whatsapp_setup_webhook(request: Request, webhook_url: str = Form("")):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    target_url = webhook_url.strip() or "https://mcp.juanconnect.online/api/webhook/whatsapp"
    res = await whatsapp_client.configure_webhook(target_url)
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_webhook_configured#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error configurando webhook"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@app.post("/api/whatsapp/setup-chatwoot")
async def api_whatsapp_setup_chatwoot(
    request: Request,
    chatwoot_url: str = Form("http://chatwoot-rails:3000"),
    chatwoot_token: str = Form(...),
    account_id: str = Form("1"),
    sign_msg: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.configure_chatwoot(
        chatwoot_url=chatwoot_url.strip(),
        chatwoot_token=chatwoot_token.strip(),
        account_id=account_id.strip() or "1",
        sign_msg=True if sign_msg in ("1", "on", "true") else False
    )
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_chatwoot_configured#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Error vinculando Chatwoot con Evolution API"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@app.post("/api/whatsapp/test")
async def api_whatsapp_test(request: Request, test_phone: str = Form(...), test_message: str = Form(...)):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    res = await whatsapp_client.send_text_message(test_phone, test_message, delay_seconds=1.0)
    if res.get("success"):
        return RedirectResponse(url="/?msg=wa_test_sent#tab-templates", status_code=302)
    else:
        err = urllib.parse.quote(res.get("error", "Fallo al enviar mensaje de prueba"))
        return RedirectResponse(url=f"/?err={err}#tab-templates", status_code=302)

@app.post("/api/whatsapp/logout")
async def api_whatsapp_logout(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token"))
    if not user:
        raise HTTPException(status_code=401)
    await whatsapp_client.logout_instance()
    return RedirectResponse(url="/?msg=wa_logged_out#tab-templates", status_code=302)

# ==========================================
# Endpoints de Configuración OAuth 2.0 (Gemini Spark)
# ==========================================
@app.get("/api/oauth/settings")
async def api_oauth_settings_get(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    return JSONResponse(database.get_oauth_settings())

@app.post("/api/oauth/settings")
async def api_oauth_settings_post(
    request: Request,
    client_id: str = Form("gemini-spark-joif"),
    client_secret: str = Form(...),
    redirect_uris: str = Form("https://gemini.google.com"),
    enabled: Optional[str] = Form(None)
):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    database.save_oauth_settings(
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        redirect_uris=redirect_uris.strip(),
        enabled=1 if enabled in ("1", "on", "true") else 0
    )
    return RedirectResponse(url="/?msg=oauth_saved#tab-oauth", status_code=302)

@app.post("/api/oauth/regenerate")
async def api_oauth_regenerate_post(request: Request):
    user = verify_session_cookie(request.cookies.get("session_token") or request.cookies.get("mcp_session"))
    if not user:
        raise HTTPException(status_code=401)
    database.regenerate_oauth_secret()
    return RedirectResponse(url="/?msg=oauth_secret_regenerated#tab-oauth", status_code=302)

@app.post("/api/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Webhook receptor de eventos de Evolution API v2 (Baileys).
    Procesa mensajes entrantes de clientes, auto-responde consultas de vencimientos/claves/CBU y
    alerta a Telegram ante el envío de comprobantes de pago.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored", "reason": "invalid_json"})

    event = body.get("event") or body.get("type", "")
    data = body.get("data", {}) or {}

    key = data.get("key", {}) or body.get("key", {})
    from_me = key.get("fromMe", False)
    remote_jid = key.get("remoteJid", "")

    # 1. Ignorar mensajes salientes propios o grupos para evitar bucles
    if from_me or not remote_jid or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
        return JSONResponse({"status": "ignored"})

    # 2. Extraer número de teléfono limpio
    phone_raw = remote_jid.split("@")[0]
    sender_phone = re.sub(r'[^0-9]', '', phone_raw)
    if not sender_phone or len(sender_phone) < 8:
        return JSONResponse({"status": "ignored", "reason": "invalid_phone"})

    # 3. Extraer contenido de texto o caption de imagen/documento
    msg_obj = data.get("message", {}) or body.get("message", {}) or {}
    text = (
        msg_obj.get("conversation") or
        msg_obj.get("extendedTextMessage", {}).get("text") or
        msg_obj.get("imageMessage", {}).get("caption") or
        msg_obj.get("documentMessage", {}).get("caption") or
        ""
    ).strip()
    is_media = bool(msg_obj.get("imageMessage") or msg_obj.get("documentMessage"))

    # 4. Verificar si la auto-respuesta está habilitada
    settings = database.get_whatsapp_api_settings()
    if not settings.get("auto_reply_enabled"):
        return JSONResponse({"status": "disabled"})

    push_name = data.get("pushName") or body.get("pushName") or "Cliente"
    client_profile = database.get_client_by_phone(sender_phone)
    client_name = client_profile["client"]["name"] if client_profile else push_name

    text_lower = text.lower()

    # REGLA A: Detección de comprobantes de pago (Imágenes/Docs o palabras clave de pago)
    receipt_keywords = ["comprobante", "pague", "pagué", "transferi", "transferí", "adjunto", "constancia", "abone", "aboné"]
    is_receipt = is_media or any(k in text_lower for k in receipt_keywords)

    if is_receipt:
        logger.info(f"Comprobante recibido de {client_name} ({sender_phone})")
        caption_txt = f"<i>\"{text}\"</i>" if text else "(Archivo multimedia adjunto)"
        await send_telegram_message(
            f"🧾 <b>¡COMPROBANTE RECIBIDO POR WHATSAPP!</b>\n\n"
            f"• Cliente: <b>{client_name}</b>\n"
            f"• WhatsApp: <code>{sender_phone}</code>\n"
            f"• Mensaje: {caption_txt}\n\n"
            f"👉 Por favor verifica el ingreso en tu cuenta bancaria y confirma el cobro en el panel."
        )

        reply = (
            f"¡Hola {client_name}! 🙌 Recibimos tu comprobante correctamente.\n\n"
            f"Nuestro equipo lo verificará en el sistema a la brevedad y extenderá tu servicio. ¡Muchas gracias por tu pago! ✨"
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        return JSONResponse({"status": "ok", "action": "receipt_acknowledged"})

    # REGLA B: Consultas de Vencimiento o Credenciales ("vence", "vencimiento", "clave", "pin", "acceso", "contraseña")
    expiry_keywords = ["vence", "vencimiento", "cuando vence", "cuándo vence", "clave", "contraseña", "contrasena", "pin", "acceso", "accesos", "cuenta"]
    if any(k in text_lower for k in expiry_keywords):
        if client_profile and client_profile.get("active_accounts"):
            accs = client_profile["active_accounts"]
            lines = [f"¡Hola {client_name}! 🍿 Aquí tienes el estado de tus servicios activos:\n"]
            for a in accs:
                perf = f" (Perfil: {a['profile_name']})" if a.get("profile_name") else ""
                pin = f" | PIN: {a['profile_pin']}" if a.get("profile_pin") else ""
                lines.append(
                    f"📺 *{a['platform']}*{perf}\n"
                    f"📧 Usuario: `{a['email']}`\n"
                    f"🔑 Clave: `{a['password']}`{pin}\n"
                    f"📅 Vence: *{a.get('expiry_date')}* ({a.get('days_label')})\n"
                )
            lines.append("¡Cualquier consulta o renovación estamos a tu disposición!")
            reply = "\n".join(lines)
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            return JSONResponse({"status": "ok", "action": "expiry_info_sent"})
        else:
            reply = (
                f"¡Hola {client_name}! En este momento no registramos suscripciones activas a tu nombre en el sistema. "
                f"Si deseas contratar Netflix, Disney+, Max u otra plataforma, avísanos y te enviamos los planes disponibles."
            )
            await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
            return JSONResponse({"status": "ok", "action": "no_active_services"})

    # REGLA C: Consulta de Medios de Pago / CBU / Alias
    payment_keywords = ["alias", "cbu", "cvu", "como pago", "cómo pago", "datos de pago", "medios de pago", "transferir", "donde transfiero", "dónde transfiero", "pagar", "cuenta bancaria"]
    if any(k in text_lower for k in payment_keywords):
        pm = database.get_formatted_payment_methods()
        reply = (
            f"¡Hola {client_name}! Aquí tienes nuestros datos de cobro oficiales:\n\n"
            f"{pm}\n\n"
            f"Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu renovación. ¡Muchas gracias! 🙌"
        )
        await whatsapp_client.send_text_message(sender_phone, reply, delay_seconds=2.0)
        return JSONResponse({"status": "ok", "action": "payment_info_sent"})

    return JSONResponse({"status": "ok", "action": "none"})

@app.get("/health")
async def health():
    return {"status": "ok", "service": "streaming-crm-interactive-bot", "version": "3.1.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
