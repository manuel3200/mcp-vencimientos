import os
import re
import json
import secrets
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Union

from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import pyotp

import database
from telegram_bot import send_telegram_message, format_and_send_alert
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
        f"💰 <b>Ingresos Cobrados:</b> ${b['collected_income']:.2f} USD ({b['transactions_count']} cobros registrados)",
        f"📉 <b>Costos de Proveedor:</b> ${b['collected_costs']:.2f} USD",
        f"💵 <b>GANANCIA NETA REAL:</b> ${b['collected_profit']:.2f} USD",
        "\n━━━━━━━━━━━━━━━━━━━━━━",
        f"⏳ <b>POR COBRAR PRÓXIMAMENTE (7 días):</b>",
        f"• Total a cobrar: <b>${b['pending_receivables_7d']:.2f} USD</b> ({b['pending_accounts_count']} cuentas)",
        f"🎯 <b>Proyección Mensual Total (Todas las cuentas):</b> ${b['projected_monthly_profit']:.2f} USD de ganancia neta",
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
    """Registra el cobro de una mensualidad o renovación de un cliente:
    Suma el dinero a tus ingresos cobrados, calcula la ganancia neta y extiende la fecha de vencimiento 30 días automáticamente.
    - correo_o_id: Correo o ID de la cuenta que pagó.
    - monto: Monto recibido (si no se especifica, toma el precio habitual de la cuenta).
    - metodo_pago: 'Transferencia', 'MercadoPago', 'Binance / USDT', 'Efectivo'.
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
        f"• Monto cobrado: ${res['amount']:.2f} USD ({metodo_pago})\n"
        f"• Ganancia neta de este cobro: +${res['profit']:.2f} USD\n"
        f"• Nuevo vencimiento: <code>{res['new_expiry']}</code> (30 días extendidos)\n"
        f"🎉 El balance financiero ha sido actualizado automáticamente."
    )

@mcp.tool()
def consultar_cuentas_por_cobrar(dias_anticipacion: int = 7) -> str:
    """Muestra todas las cuentas que vencen en los próximos días con el monto que debes cobrar y los datos del cliente."""
    b = database.get_financial_balance()
    pending = b.get("pending_accounts", [])
    if not pending:
        return f"🎉 ¡Al día! No hay cobros pendientes para los próximos {dias_anticipacion} días."

    lines = [
        f"⏳ <b>Cobros Pendientes ({len(pending)} cuentas - Total: ${b['pending_receivables_7d']:.2f} USD):</b>\n"
    ]
    for p in pending:
        d_txt = "HOY" if p['days_remaining'] == 0 else (f"en {p['days_remaining']}d" if p['days_remaining'] > 0 else f"VENCIDA hace {abs(p['days_remaining'])}d")
        wa_data = database.generate_whatsapp_message(p['email'], message_type="cobro")
        wa_url = wa_data.get('wa_link', '')
        wa_line = f"\n  📲 Link WhatsApp (1 Clic): {wa_url}" if wa_url else ""
        lines.append(
            f"• <b>{p['client']}</b> - {p['platform']} ({p['email']})\n"
            f"  A cobrar: <b>${p['price']:.2f} USD</b> | Vence: {d_txt}\n"
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
async def verificar_vencimientos_ahora(dias_anticipacion: int = 2) -> str:
    """Ejecuta una comprobación inmediata de vencimientos de streaming y envía alertas con datos de contacto por Telegram."""
    enviadas = await check_and_send_alerts(days_window=dias_anticipacion)
    return f"Comprobación manual completada. Se enviaron {enviadas} alerta(s) de vencimiento por Telegram."


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
    logger.info("Aplicación y tareas programadas iniciadas.")
    async with mcp_app.lifespan(app):
        yield
    stop_scheduler()
    logger.info("Aplicación detenida.")

app = FastAPI(
    title="Gemini Streaming CRM & Financial Bot",
    description="Servidor MCP para Gemini Spark y CRM de Streaming con Finanzas y 2FA",
    version="2.2.0",
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
            <td><strong style="color:#10b981;">+${t['amount']:.2f} USD</strong></td>
            <td><span style="color:#f59e0b;">-${t['cost']:.2f}</span></td>
            <td><strong style="color:#38bdf8;">+${t['profit']:.2f} USD</strong></td>
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
            <div class="screen-header">
                <div>
                    <span class="badge" style="background:#1e3a8a;color:#93c5fd;margin-bottom:6px;">{s['platform']}</span>
                    <div class="screen-title"><code>{s['email']}</code></div>
                    <small style="color:#64748b;">Clave: <code>{s['password']}</code></small>
                </div>
                <div style="text-align:right;">
                    <span style="font-size:1.1rem;font-weight:800;color:#38bdf8;">{s['occupied_count']}/{s['total_profiles']}</span>
                    <br><small style="color:#10b981;font-weight:600;">{s['free_count']} libres</small>
                </div>
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
                document.getElementById(tabId).style.display = 'block';
                document.getElementById('btn-' + tabId).classList.add('active');
            }}
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

            <!-- Dashboard de Finanzas y Balance -->
            <div class="finance-grid">
                <div class="fin-box box-income">
                    <h4>Ingresos Cobrados (Mes)</h4>
                    <p class="amount">${finance['collected_income']:.2f} USD</p>
                    <small>{finance['transactions_count']} cobros registrados</small>
                </div>
                <div class="fin-box box-costs">
                    <h4>Costo Proveedores</h4>
                    <p class="amount">${finance['collected_costs']:.2f} USD</p>
                    <small>Costo base de cuentas</small>
                </div>
                <div class="fin-box box-profit">
                    <h4>Ganancia Neta Real</h4>
                    <p class="amount">${finance['collected_profit']:.2f} USD</p>
                    <small>Beneficio líquido en el bolsillo</small>
                </div>
                <div class="fin-box box-pending">
                    <h4>Por Cobrar (Próximos 7d)</h4>
                    <p class="amount">${finance['pending_receivables_7d']:.2f} USD</p>
                    <small>{finance['pending_accounts_count']} cuentas por vencer</small>
                </div>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; gap: 10px;">
                        <form action="/api/test-telegram" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#475569;">📲 Test Telegram</button>
                        </form>
                        <form action="/api/check-now" method="POST" style="display: inline;">
                            <button type="submit" class="btn" style="background:#059669;">🔍 Escanear Vencimientos Ahora</button>
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
                    <button id="btn-tab-finance" class="tab-btn" onclick="showTab('tab-finance')">💵 Historial de Cobros ({len(transactions)})</button>
                    <button id="btn-tab-stock" class="tab-btn" onclick="showTab('tab-stock')">📦 Stock Libre ({len(free_stock)})</button>
                    <button id="btn-tab-fallen" class="tab-btn" onclick="showTab('tab-fallen')">🚨 Cuentas Caídas ({len(fallen_accounts)})</button>
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
            f"• Monto cobrado: ${res['amount']:.2f} USD\n"
            f"• Ganancia Neta: +${res['profit']:.2f} USD\n"
            f"• Próximo vencimiento: {res['new_expiry']}"
            f"{wa_link_html}"
        )
    return RedirectResponse(url="/", status_code=302)

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
    sent = await check_and_send_alerts()
    return JSONResponse({"ok": True, "alertas_enviadas": sent})

@app.get("/health")
async def health():
    return {"status": "ok", "service": "streaming-crm-screens", "version": "2.4.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
