import re
import logging
from datetime import date, timedelta
from typing import Dict, Any, Optional, Tuple

import database
import whatsapp_client
from telegram_bot import send_telegram_message

logger = logging.getLogger("chatwoot.bot")


def resolve_platform_and_modality(raw_text: str) -> Tuple[Optional[str], str]:
    """Interpreta el comando de Chatwoot y retorna (plataforma, modalidad)."""
    t = raw_text.lower().replace("/", "").strip()

    # 1. Netflix Casa Extra (1 Pantalla)
    if any(k in t for k in [
        "n_casaextra", "n_casa_extra", "n_extra", "netflix_casaextra",
        "netflix_casa_extra", "nc n casaextra", "nc n casa extra",
        "nc netflix casa extra", "nc netflix casaextra", "casaextra", "casa_extra"
    ]):
        return "Netflix (Casa Extra)", "pantalla"

    # 2. Netflix Cuenta Completa (4 Pantallas)
    if any(k in t for k in [
        "n_full", "n_completa", "netflix_full", "netflix_completa",
        "nc n full", "nc n completa", "nc netflix full", "nc netflix completa",
        "full", "completa"
    ]) and ("netflix" in t or "_n" in t or "n_" in t or "nc_n" in t):
        return "Netflix (Cuenta Completa)", "cuenta_completa"

    # 3. Disney+
    if any(k in t for k in ["disney", "_d", "d_"]) and not "netflix" in t:
        return "Disney+ Premium", "pantalla"

    # 4. Max (HBO)
    if any(k in t for k in ["max", "hbo", "_m", "m_"]):
        return "Max (HBO)", "pantalla"

    # 5. Amazon Prime Video
    if any(k in t for k in ["prime", "amazon", "_p", "p_"]) and not "paramount" in t:
        return "Amazon Prime Video", "pantalla"

    # 6. Paramount+
    if any(k in t for k in ["paramount", "_pa", "pa_"]):
        return "Paramount+", "pantalla"

    # 7. Spotify Premium
    if any(k in t for k in ["spotify", "_s", "s_"]):
        return "Spotify Premium", "cuenta_completa"

    # 8. YouTube Premium
    if any(k in t for k in ["youtube", "_y", "y_"]):
        return "YouTube Premium", "cuenta_completa"

    # 9. Crunchyroll
    if any(k in t for k in ["crunchy", "crunchyroll", "_c", "c_"]):
        return "Crunchyroll Mega Fan", "pantalla"

    # Fallback si solo puso /nc_n o /nc netflix -> Por defecto Netflix (Casa Extra)
    if "netflix" in t or t in ("nc_n", "nc n", "n"):
        return "Netflix (Casa Extra)", "pantalla"

    return None, "pantalla"


async def process_chatwoot_command(body: Dict[str, Any]) -> Dict[str, Any]:
    """Procesa eventos de webhook de Chatwoot y ejecuta comandos de agentes como /nc_n_casaextra."""
    event = body.get("event")
    if event != "message_created":
        return {"status": "ignored", "reason": "unhandled_event"}

    content = (body.get("content") or "").strip()
    if not content.startswith("/"):
        return {"status": "ignored", "reason": "not_a_command"}

    logger.info(f"Comando de Chatwoot recibido: '{content}'")

    # Seguridad: Sólo procesar comandos emitidos por un agente o notas privadas internas
    is_private = bool(body.get("private", False))
    message_type = (body.get("message_type") or "").lower()
    sender = body.get("sender") or {}
    sender_type = (sender.get("type") or "").lower()

    is_agent = is_private or sender_type in ("user", "agent") or message_type == "outgoing"
    if not is_agent:
        logger.info(f"Comando Chatwoot omitido: no es de agente (sender_type={sender_type}, is_private={is_private})")
        return {"status": "ignored", "reason": "not_from_agent"}

    conversation = body.get("conversation") or {}
    conv_id = conversation.get("id") or body.get("conversation_id")
    if not conv_id:
        logger.warning("Comando Chatwoot omitido: falta conversation_id")
        return {"status": "ignored", "reason": "no_conversation_id"}

    meta = conversation.get("meta") or body.get("meta") or {}
    sender_meta = meta.get("sender") or conversation.get("contact") or {}
    contact_name = (sender_meta.get("name") or "").strip() or "Cliente"
    contact_phone_raw = (sender_meta.get("phone_number") or sender_meta.get("identifier") or "").strip()
    clean_phone = database.clean_whatsapp_phone(contact_phone_raw) if contact_phone_raw else ""

    # Localizar o registrar al cliente en el CRM
    client = None
    if clean_phone:
        client = database.search_client(clean_phone)
    if not client and contact_name:
        client = database.search_client(contact_name)
    if not client:
        client = database.find_or_create_client(
            name=contact_name,
            whatsapp=clean_phone,
            client_type="consumidor_final",
            notes=f"Contacto sincronizado de Chatwoot (Conv #{conv_id})"
        )

    clean_cmd = content.strip().lower()

    # -------------------------------------------------------------
    # 1. COMANDOS DE NUEVA CUENTA / VENTA (/nc, /vender, /venta)
    # -------------------------------------------------------------
    if clean_cmd.startswith("/nc") or clean_cmd.startswith("/vender") or clean_cmd.startswith("/venta"):
        platform_name, service_type = resolve_platform_and_modality(clean_cmd)
        if not platform_name:
            await whatsapp_client.send_chatwoot_message(
                conv_id,
                "⚠️ [StreamVault CRM] Plataforma no reconocida.\n\n"
                "💡 Ejemplos válidos:\n"
                "• `/nc_n_casaextra` (Netflix Casa Extra - 1 Pantalla)\n"
                "• `/nc_n_full` (Netflix Cuenta Completa - 4 Pantallas)\n"
                "• `/nc_disney` (Disney+ Premium)\n"
                "• `/nc_max` (Max HBO)\n"
                "• `/nc_prime` (Prime Video)\n"
                "• `/nc_spotify` (Spotify Premium)\n"
                "• `/nc_youtube` (YouTube Premium)\n"
                "O escribe `/ayuda` para ver todos los comandos.",
                private=True
            )
            return {"status": "ok", "action": "unknown_platform"}

        expiry_date = (date.today() + timedelta(days=30)).isoformat()
        client_type = client.get("client_type") or "consumidor_final"

        # Asignar próximo casillero o cuenta libre disponible
        acc = database.assign_next_free_profile(
            client_name=client["name"],
            platform=platform_name,
            expiry_date=expiry_date,
            whatsapp=client.get("whatsapp") or clean_phone,
            client_type=client_type
        )

        if acc:
            # 1. Enviar mensaje oficial de entrega al cliente por WhatsApp
            wa_data = database.generate_whatsapp_message(acc, message_type="entrega")
            delivery_text = wa_data.get("message_text", "")
            await whatsapp_client.send_chatwoot_message(conv_id, delivery_text, private=False)

            # 2. Enviar nota privada para el agente en Chatwoot
            badge_type = "👔 Revendedor" if acc.get("client_type") == "revendedor" else "👤 Consumidor Final"
            cost_str = acc.get("cost") or "-"
            price_str = acc.get("price") or "-"
            await whatsapp_client.send_chatwoot_message(
                conv_id,
                f"✅ <b>[StreamVault CRM] Suscripción Asignada con Éxito:</b>\n"
                f"• Cliente: <b>{acc['client_name']}</b> ({badge_type})\n"
                f"• Plataforma: <b>{acc['platform']}</b>" + (f" - <b>{acc.get('profile_name')}</b>" if acc.get('profile_name') else "") + "\n"
                f"• Correo: <code>{acc['email']}</code>\n"
                f"• Contraseña: <code>{acc['password']}</code>" + (f" | PIN: <code>{acc['profile_pin']}</code>" if acc.get('profile_pin') else "") + "\n"
                f"• Vencimiento: <code>{acc['expiry_date']}</code>\n"
                f"• Tarifa cobrada: <b>{price_str}</b> (Costo prov: {cost_str})\n"
                f"• Los accesos fueron enviados al cliente y la ganancia registrada en el balance financiero.",
                private=True
            )

            # 3. Notificar a Telegram
            await send_telegram_message(
                f"⚡ <b>¡VENTA RÁPIDA DESDE CHATWOOT!</b>\n\n"
                f"• Cliente: <b>{acc['client_name']}</b> ({badge_type})\n"
                f"• Servicio: <b>{acc['platform']}</b>\n"
                f"• Cuenta: <code>{acc['email']}</code>\n"
                f"• Vencimiento: <code>{acc['expiry_date']}</code> | Cobrado: <b>{price_str}</b>\n"
                f"• Atajo ejecutado en Chatwoot: <code>{content}</code>"
            )
            return {"status": "ok", "action": "account_assigned", "account": acc}
        else:
            await whatsapp_client.send_chatwoot_message(
                conv_id,
                f"⚠️ <b>[StreamVault CRM] ¡SIN STOCK DISPONIBLE!</b>\n\n"
                f"No se encontraron cuentas o pantallas libres para <b>'{platform_name}'</b>.\n"
                f"Por favor ingresa al panel web para cargar nuevas cuentas en stock o crear una cuenta madre antes de asignar.",
                private=True
            )
            return {"status": "ok", "action": "out_of_stock", "platform": platform_name}

    # -------------------------------------------------------------
    # 2. CONSULTA DE STOCK EN TIEMPO REAL (/stock)
    # -------------------------------------------------------------
    elif clean_cmd.startswith("/stock"):
        health = database.get_stock_health_summary()
        platforms = health.get("platforms", [])
        lines = ["📦 <b>[StreamVault CRM] Stock Libre en Inventario:</b>\n"]
        if not platforms:
            lines.append("No hay cuentas ni plataformas registradas en stock.")
        else:
            for p in platforms:
                ico = "🔴" if p["status"] == "agotado" else ("🟡" if p["status"] == "bajo" else "🟢")
                lines.append(f"{ico} <b>{p['platform']}:</b> {p['free_count']} libres (Mín: {p['min_threshold']})")
        lines.append(f"\n📊 <b>Total Unidades Libres:</b> {health.get('total_free_units', 0)}")
        await whatsapp_client.send_chatwoot_message(conv_id, "\n".join(lines), private=True)
        return {"status": "ok", "action": "stock_reported"}

    # -------------------------------------------------------------
    # 3. FICHA Y SUSCRIPCIONES ACTIVAS DEL CLIENTE (/info, /servicios, /cuenta)
    # -------------------------------------------------------------
    elif clean_cmd in ("/info", "/servicios", "/cuenta", "/cuentas"):
        c_info = database.get_client_360(client["id"])
        active_accs = c_info.get("active_accounts", []) if c_info else []
        badge_type = "👔 Revendedor" if client.get("client_type") == "revendedor" else "👤 Consumidor Final"

        if not active_accs:
            await whatsapp_client.send_chatwoot_message(
                conv_id,
                f"ℹ️ [StreamVault CRM] <b>{client['name']}</b> ({badge_type}) no posee suscripciones activas en este momento.",
                private=True
            )
        else:
            lines = [f"👤 <b>[StreamVault CRM] Suscripciones de {client['name']} ({badge_type}):</b>\n"]
            for a in active_accs:
                perf = f" ({a['profile_name']})" if a.get("profile_name") else ""
                pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
                lines.append(
                    f"• <b>{a['platform']}{perf}</b>\n"
                    f"  📧 Correo: <code>{a['email']}</code> | Clave: <code>{a['password']}</code>{pin}\n"
                    f"  📅 Vence: <code>{a['expiry_date']}</code> ({a.get('days_label', '')}) | Precio: {a.get('price') or '-'}\n"
                )
            await whatsapp_client.send_chatwoot_message(conv_id, "\n".join(lines), private=True)
        return {"status": "ok", "action": "info_reported"}

    # -------------------------------------------------------------
    # 4. ENVIAR DATOS DE COBRO Y ALIAS AL CLIENTE (/cbu, /alias, /pago)
    # -------------------------------------------------------------
    elif clean_cmd in ("/cbu", "/alias", "/pago", "/pagos", "/transferir"):
        pm = database.get_formatted_payment_methods()
        text = (
            f"¡Hola {client['name']}! Aquí tienes nuestros datos de cobro oficiales:\n\n"
            f"{pm}\n\n"
            f"Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu activación o renovación. ¡Muchas gracias! 🙌"
        )
        await whatsapp_client.send_chatwoot_message(conv_id, text, private=False)
        return {"status": "ok", "action": "payment_info_sent"}

    # -------------------------------------------------------------
    # 5. MENÚ DE AYUDA Y COMANDOS DISPONIBLES (/ayuda, /comandos, /help)
    # -------------------------------------------------------------
    elif clean_cmd in ("/ayuda", "/comandos", "/help"):
        help_text = (
            "🛠️ <b>COMANDOS RÁPIDOS STREAMVAULT EN CHATWOOT:</b>\n\n"
            "<b>Ventas Rápidas (Asignación Automática):</b>\n"
            "• <code>/nc_n_casaextra</code> : Asigna Netflix Casa Extra (1 Pantalla)\n"
            "• <code>/nc_n_full</code> : Asigna Netflix Cuenta Completa (4 Pantallas)\n"
            "• <code>/nc_disney</code> : Asigna Disney+ Premium\n"
            "• <code>/nc_max</code> : Asigna Max (HBO)\n"
            "• <code>/nc_prime</code> : Asigna Amazon Prime Video\n"
            "• <code>/nc_spotify</code> : Asigna Spotify Premium\n"
            "• <code>/nc_youtube</code> : Asigna YouTube Premium\n"
            "• <code>/nc_paramount</code> : Asigna Paramount+\n"
            "• <code>/nc_crunchyroll</code> : Asigna Crunchyroll\n\n"
            "<b>Consultas & Operaciones:</b>\n"
            "• <code>/stock</code> : Ver stock libre en tiempo real\n"
            "• <code>/info</code> : Ver suscripciones activas del cliente actual\n"
            "• <code>/cbu</code> : Enviar datos bancarios y alias al cliente\n\n"
            "💡 <b>Consejo Pro:</b> Puedes escribir el comando en la pestaña <b>'Nota privada'</b> (caja amarilla en Chatwoot). Así el cliente no verá el comando y recibirá únicamente el mensaje final con sus accesos."
        )
        await whatsapp_client.send_chatwoot_message(conv_id, help_text, private=True)
        return {"status": "ok", "action": "help_sent"}

    return {"status": "ignored", "reason": "unknown_command"}
