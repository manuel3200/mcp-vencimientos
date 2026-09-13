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
    # Verificación proactiva: Si el cliente ya existe en el CRM como revendedor, aplicar tarifa mayorista
    existing_client = database.search_client(cliente)
    if existing_client and "revend" in (existing_client.get("client_type") or "").lower():
        tipo_cliente = "revendedor"

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
        # Verificación proactiva: Si el cliente ya existe en el CRM como revendedor, aplicar tarifa mayorista
        existing_client = database.search_client(cliente)
        if existing_client and "revend" in (existing_client.get("client_type") or "").lower():
            tipo_cliente = "revendedor"

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
@mcp.tool()
def eliminar_cuenta_individual(id_cuenta: int) -> str:
    """Elimina definitivamente una cuenta de streaming o pantalla por su ID numérico."""
    ok = database.delete_account(id_cuenta)
    if ok:
        return f"✅ Cuenta con ID #{id_cuenta} eliminada correctamente de la base de datos."
    return f"❌ No se encontró ninguna cuenta con el ID #{id_cuenta}."


@mcp.tool()
def eliminar_todas_las_cuentas_excepto_cliente(nombre_cliente_a_conservar: str = "Samuel Martinez") -> str:
    """Elimina de forma masiva todas las cuentas de streaming de la base de datos (caídas, libres, o de otros clientes), preservando únicamente las cuentas pertenecientes al cliente especificado (por defecto 'Samuel Martinez')."""
    res = database.purge_accounts_except_client(nombre_cliente_a_conservar)
    if not res.get("success"):
        return f"❌ Error al purgar las cuentas: {res.get('error', 'Error desconocido')}"
    
    del_count = res["deleted_count"]
    kept = res["kept_accounts"]
    
    lines = [
        f"🗑️ <b>PURGA DE CUENTAS COMPLETADA</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🧹 Cuentas eliminadas: <b>{del_count}</b>",
        f"🛡️ Cliente protegido: <b>{nombre_cliente_a_conservar}</b>",
        f"📦 Cuentas conservadas en el sistema: <b>{len(kept)}</b>\n"
    ]
    for k in kept:
        perf = f" (Perfil: {k['profile_name']})" if k.get("profile_name") else ""
        lines.append(f"• [{k['id']}] {k['platform']}{perf} - <code>{k['email']}</code> (Cliente: {k.get('client_name') or 'N/A'})")
    
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"✨ El sistema y el dashboard han quedado limpios manteniendo intacto a {nombre_cliente_a_conservar}.")
    return "\n".join(lines)



@mcp.tool()
async def verificar_vencimientos_ahora(dias_anticipacion: int = 7) -> str:
    """Ejecuta una comprobación inmediata de vencimientos de streaming y envía alertas con datos de contacto y botones de acción rápida por Telegram."""
    enviadas = await check_and_send_alerts(days_window=dias_anticipacion, force=True)
    return f"Comprobación manual completada. Se enviaron {enviadas} alerta(s) de vencimiento interactivas por Telegram."


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


@mcp.tool()
def corregir_o_modificar_precio_cuenta(
    cliente_o_cuenta: str,
    nuevo_precio: str,
    plataforma: str = "",
    marcar_como_revendedor: bool = True
) -> str:
    """Modifica o corrige el precio cobrado por una suscripción activa y recalcula las ganancias en el balance financiero.
    
    Casos de uso principales:
    - Se vendió una cuenta a precio normal ($8.500) a un revendedor por error y hay que cambiarla al precio de mayorista ($6.500).
    - Se acordó un descuento o tarifa especial con un cliente.
    
    Argumentos:
    - cliente_o_cuenta: Nombre del cliente (ej: 'Mateo', 'Juan'), número de WhatsApp, correo de la cuenta o ID numérico.
    - nuevo_precio: El nuevo precio en ARS (ej: '6500' o '$6.500').
    - plataforma: (Opcional) Si el cliente tiene múltiples servicios, especifica cuál (ej: 'Netflix', 'Disney+').
    - marcar_como_revendedor: (Por defecto True) Guarda permanentemente al cliente como 'revendedor' en el CRM para que futuras ventas usen precio mayorista.
    """
    res = database.update_account_price(
        identifier=cliente_o_cuenta,
        new_price=nuevo_precio,
        platform=plataforma,
        mark_as_reseller=marcar_como_revendedor
    )
    if not res:
        return f"❌ No se encontró ninguna cuenta activa asignada para '{cliente_o_cuenta}'" + (f" en la plataforma '{plataforma}'." if plataforma else ".")

    c_type_label = "👔 Revendedor" if res.get("client_type") == "revendedor" else "👤 Consumidor Final"
    perf = f" (Perfil: {res['profile_name']})" if res.get("profile_name") else ""
    return (
        f"✅ PRECIO CORREGIDO EXITOSAMENTE:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Cliente:</b> {res.get('client_name') or 'Cliente'} ({res.get('client_code') or ''}) - {c_type_label}\n"
        f"📺 <b>Servicio:</b> {res['platform']}{perf}\n"
        f"📧 <b>Cuenta:</b> <code>{res['email']}</code>\n"
        f"💰 <b>Precio anterior:</b> {res.get('old_price') or '-'}\n"
        f"💵 <b>Nuevo precio cobrado:</b> <b>{res['new_price']}</b>\n"
        f"📈 <b>Ganancia neta recalculada:</b> +{database.format_ars(res['profit_num'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <i>El balance financiero, Ficha 360° y libro contable fueron actualizados en tiempo real.</i>"
    )





# --- Herramientas Evolution API & WhatsApp Bot (Paso 5) ---
