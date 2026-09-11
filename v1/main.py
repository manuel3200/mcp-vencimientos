import os
import re
import json
import secrets
import logging
import urllib.parse
from datetime import date, datetime
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, Request, Response, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import pyotp

import database
from telegram_bot import send_telegram_message, format_and_send_alert, start_telegram_polling, stop_telegram_polling, send_full_backup_to_telegram
from scheduler import start_scheduler, stop_scheduler, check_and_send_alerts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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
        else:
            msg_text = msg_raw
        msg_banner = f"""
        <div style="background:#065f46; border:1px solid #10b981; color:#d1fae5; padding:12px 18px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center;">
            <span>{msg_text}</span>
            <a href="/" style="color:#a7f3d0; text-decoration:none; font-weight:bold; cursor:pointer;">✕</a>
        </div>
        """

    # 1. Filas de Cuentas Activas con botón de Cobrar
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

        perf = f"<br><small style='color:#94a3b8;'>Perf: {a['profile_name']}</small>" if a.get("profile_name") else ""
        pin = f"<small style='color:#94a3b8;'>PIN: {a['profile_pin']}</small>" if a.get("profile_pin") else ""

        wa_cobro = database.generate_whatsapp_message(a, message_type="cobro")
        wa_link_cobro = wa_cobro.get("wa_link", "#")
        wa_entrega = database.generate_whatsapp_message(a, message_type="entrega")
        wa_link_entrega = wa_entrega.get("wa_link", "#")

        active_rows += f"""
        <tr>
            <td><strong>{client_tag}</strong><br><small style="color:#64748b;">{a.get('client_code') or ''}</small></td>
            <td>{wa_link}<br>{tg_link}</td>
            <td><span class="badge" style="background:#1e3a8a;color:#93c5fd;">{a['platform']}</span>{perf}</td>
            <td><code>{a['email']}</code><br><code>{a['password']}</code> {pin}</td>
            <td><code>{a['expiry_date']}</code></td>
            <td><span class="badge {badge}">{badge_txt}</span></td>
            <td><strong>{a.get('price') or '-'}</strong></td>
            <td style="white-space: nowrap;">
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
        </style>
        <script>
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
            window.addEventListener('DOMContentLoaded', () => {{
                const hash = window.location.hash.replace('#', '');
                if (hash && document.getElementById(hash)) {{
                    showTab(hash);
                }}
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
                    <span><strong>Conexión Gemini Spark:</strong> <code>https://mcp.juanconnect.online/mcp</code></span>
                    <span>Modo Financiero & CRM Activo</span>
                </div>
            </div>

            <div class="card">
                <div class="tabs">
                    <button id="btn-tab-active" class="tab-btn active" onclick="showTab('tab-active')">👥 Clientes & Activas ({len(active_accounts)})</button>
                    <button id="btn-tab-screens" class="tab-btn" onclick="showTab('tab-screens')">📺 Pantallas ({len(screens_overview)})</button>
                    <button id="btn-tab-catalog" class="tab-btn" onclick="showTab('tab-catalog')">🏷️ Precios & Combos ({len(catalog_items)}/{len(combos_list)})</button>
                    <button id="btn-tab-finance" class="tab-btn" onclick="showTab('tab-finance')">💵 Historial de Cobros ({len(transactions)})</button>
                    <button id="btn-tab-stock" class="tab-btn" onclick="showTab('tab-stock')">📦 Stock Libre ({len(free_stock)})</button>
                    <button id="btn-tab-fallen" class="tab-btn" onclick="showTab('tab-fallen')">🚨 Cuentas Caídas ({len(fallen_accounts)})</button>
                    <button id="btn-tab-backup" class="tab-btn" onclick="showTab('tab-backup')">📁 Excel & Backups</button>
                </div>

                <div id="tab-active" class="tab-content" style="display:block;">
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
    </body>
    </html>
    """
    return html

# Acciones Rápidas de API
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
        await send_telegram_message(
            f"🎉 <b>¡Combo Vendido desde el Panel Web!</b>\n\n"
            f"• Pack: <b>{res['combo_name']}</b>\n"
            f"• Cliente: {res['client_name']} ({client_type})\n"
            f"• Total Cobrado: <b>{database.format_ars(res['amount'])}</b>\n"
            f"• Ganancia Neta: +{database.format_ars(res['profit'])}\n"
            f"• Cuentas asignadas: {len(res['accounts'])}\n"
            f"• Vencimiento: <code>{res['expiry_date']}</code>\n\n"
            f"📲 <a href=\"{wa_url}\"><b>👉 ENVIAR ACCESOS POR WHATSAPP (1 Clic)</b></a>"
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

@app.get("/health")
async def health():
    return {"status": "ok", "service": "streaming-crm-interactive-bot", "version": "2.7.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
