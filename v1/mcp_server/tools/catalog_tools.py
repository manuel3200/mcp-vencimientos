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

