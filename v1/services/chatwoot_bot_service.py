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

    # Seguridad: Sólo procesar comandos o mensajes emitidos por un agente o notas privadas internas
    is_private = bool(body.get("private", False))
    message_type = (body.get("message_type") or "").lower()
    sender = body.get("sender") or {}
    sender_type = (sender.get("type") or "").lower()

    is_agent = is_private or sender_type in ("user", "agent") or message_type == "outgoing"

    if not content.startswith("/"):
        if is_agent:
            conversation = body.get("conversation") or {}
            sender_meta = (conversation.get("meta") or {}).get("sender") or conversation.get("contact") or {}
            contact_phone_raw = (sender_meta.get("phone_number") or sender_meta.get("identifier") or "").strip()
            clean_phone = database.clean_whatsapp_phone(contact_phone_raw) if contact_phone_raw else ""
            res = await database.process_http_custom_outgoing_message(clean_phone, content, source="Chatwoot")
            if res.get("status") == "success":
                return {"status": "processed_http_custom", "data": res}
        return {"status": "ignored", "reason": "not_a_command"}

    logger.info(f"Comando de Chatwoot recibido: '{content}'")

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

    # Sincronización proactiva de nombre si el CRM tiene un nombre más completo agendado
    contact_id = sender_meta.get("id") or conversation.get("contact_id")
    if contact_id and client and client.get("name"):
        crm_n = client["name"].strip()
        if crm_n and crm_n != contact_name and not crm_n.lower().startswith("whatsapp"):
            try:
                await whatsapp_client.update_chatwoot_contact(contact_id=int(contact_id), name=crm_n)
            except Exception as e:
                logger.debug(f"No se pudo sincronizar nombre de contacto proactivamente: {e}")

    clean_cmd = content.strip().lower()

    try:
        # -------------------------------------------------------------
        # 1. COMANDOS DE NUEVA CUENTA / VENTA (/nc, /vender, /venta)
        # -------------------------------------------------------------
        if clean_cmd.startswith(("/nc", "/vender", "/venta")):
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
                    f"✅ **[StreamVault CRM] Suscripción Asignada con Éxito:**\n"
                    f"• Cliente: **{acc['client_name']}** ({badge_type})\n"
                    f"• Plataforma: **{acc['platform']}**" + (f" - **{acc.get('profile_name')}**" if acc.get('profile_name') else "") + "\n"
                    f"• Correo: `{acc['email']}`\n"
                    f"• Contraseña: `{acc['password']}`" + (f" | PIN: `{acc['profile_pin']}`" if acc.get('profile_pin') else "") + "\n"
                    f"• Vencimiento: `{acc['expiry_date']}`\n"
                    f"• Tarifa cobrada: **{price_str}** (Costo prov: {cost_str})\n"
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
                    f"⚠️ **[StreamVault CRM] ¡SIN STOCK DISPONIBLE!**\n\n"
                    f"No se encontraron cuentas o pantallas libres para **'{platform_name}'**.\n"
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
            lines = ["📦 **[StreamVault CRM] Stock Libre en Inventario:**\n"]
            if not platforms:
                lines.append("No hay cuentas ni plataformas registradas en stock.")
            else:
                for p in platforms:
                    ico = "🔴" if p["status"] == "agotado" else ("🟡" if p["status"] == "bajo" else "🟢")
                    lines.append(f"{ico} **{p['platform']}:** {p['free_count']} libres (Mín: {p['min_threshold']})")
            lines.append(f"\n📊 **Total Unidades Libres:** {health.get('total_free_units', 0)}")
            await whatsapp_client.send_chatwoot_message(conv_id, "\n".join(lines), private=True)
            return {"status": "ok", "action": "stock_reported"}

        # -------------------------------------------------------------
        # 3. FICHA Y SUSCRIPCIONES ACTIVAS DEL CLIENTE (/info, /servicios, /cuenta)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/info", "/servicios", "/cuenta", "/cuentas")):
            c_info = database.get_client_360_profile(client["id"])
            active_accs = c_info.get("active_accounts", []) if c_info else []
            badge_type = "👔 Revendedor" if client.get("client_type") == "revendedor" else "👤 Consumidor Final"

            if not active_accs:
                await whatsapp_client.send_chatwoot_message(
                    conv_id,
                    f"ℹ️ [StreamVault CRM] **{client['name']}** ({badge_type}) no posee suscripciones activas en este momento.",
                    private=True
                )
            else:
                lines = [f"👤 **[StreamVault CRM] Suscripciones de {client['name']} ({badge_type}):**\n"]
                for a in active_accs:
                    perf = f" ({a['profile_name']})" if a.get("profile_name") else ""
                    pin = f" [PIN: {a['profile_pin']}]" if a.get("profile_pin") else ""
                    lines.append(
                        f"• **{a['platform']}{perf}**\n"
                        f"  📧 Correo: `{a['email']}` | Clave: `{a['password']}`{pin}\n"
                        f"  📅 Vence: `{a['expiry_date']}` ({a.get('days_label', '')}) | Precio: {a.get('price') or '-'}\n"
                    )
                await whatsapp_client.send_chatwoot_message(conv_id, "\n".join(lines), private=True)
            return {"status": "ok", "action": "info_reported"}

        # -------------------------------------------------------------
        # 4. REGISTRAR PAGO Y RENOVAR SERVICIO (/pago, /pagado, /renovar, /cobrado, /confirmar)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/pago", "/pagado", "/renovar", "/cobrado", "/confirmar")):
            # Extraer monto opcional si el agente puso por ejemplo /pago 6500
            parts = clean_cmd.split()
            custom_amount = None
            if len(parts) > 1:
                clean_num = re.sub(r'[^0-9.]', '', parts[1].replace(",", "."))
                if clean_num:
                    try:
                        custom_amount = float(clean_num)
                    except ValueError:
                        pass

            c_info = database.get_client_360_profile(client["id"])
            active_accs = c_info.get("active_accounts", []) if c_info else []
            if not active_accs:
                await whatsapp_client.send_chatwoot_message(
                    conv_id,
                    f"⚠️ [StreamVault CRM] **{client['name']}** no registra suscripciones activas para renovar.",
                    private=True
                )
                return {"status": "ok", "action": "no_active_accounts"}

            target_acc = active_accs[0]
            res = database.register_customer_payment(
                email_or_id=str(target_acc["id"]),
                amount=custom_amount,
                payment_method="Chatwoot / Transferencia"
            )

            if res.get("success"):
                amt_fmt = database.format_ars(res['amount'])
                is_initial = res.get("is_initial", False)
                badge_type = "👔 Revendedor" if client.get("client_type") == "revendedor" else "👤 Consumidor Final"

                if is_initial:
                    # 1. Mensaje al cliente para pago de compra inicial
                    msg_client = (
                        f"🎉 ¡Hola {client['name']}! Confirmamos la recepción de tu pago de *{amt_fmt}* "
                        f"para tu compra de *{res['platform']}*.\n\n"
                        f"Tu suscripción está confirmada y activa hasta el *{res['new_expiry']}*. "
                        f"¡Muchas gracias por tu compra y preferencia! 🙌✨"
                    )
                    # 2. Nota privada para el agente
                    note_agent = (
                        f"✅ **[StreamVault CRM] ¡Pago de Compra Registrado!**\n"
                        f"• Cliente: **{res['client_name']}** ({badge_type})\n"
                        f"• Servicio: **{res['platform']}** (`{res['email']}`)\n"
                        f"• Cobrado: **+{amt_fmt}** (Ganancia: +{database.format_ars(res['profit'])})\n"
                        f"• Vencimiento: `{res['new_expiry']}` (Suscripción al día)\n"
                        f"• Transacción registrada en el libro contable de finanzas."
                    )
                    tg_title = "💵 <b>¡PAGO DE COMPRA CONFIRMADO DESDE CHATWOOT!</b>"
                else:
                    # 1. Mensaje al cliente para renovación mensual
                    msg_client = (
                        f"🎉 ¡Hola {client['name']}! Confirmamos la recepción de tu pago de *{amt_fmt}* "
                        f"para la renovación de *{res['platform']}*.\n\n"
                        f"Tu suscripción ha sido renovada con éxito hasta el *{res['new_expiry']}* (30 días extendidos). "
                        f"¡Muchas gracias por tu pago y preferencia! 🙌✨"
                    )
                    # 2. Nota privada para el agente
                    note_agent = (
                        f"✅ **[StreamVault CRM] ¡Pago y Renovación Registrados!**\n"
                        f"• Cliente: **{res['client_name']}** ({badge_type})\n"
                        f"• Servicio: **{res['platform']}** (`{res['email']}`)\n"
                        f"• Cobrado: **+{amt_fmt}** (Ganancia: +{database.format_ars(res['profit'])})\n"
                        f"• Nuevo Vencimiento: `{res['new_expiry']}` (+30 días)\n"
                        f"• Transacción registrada en el libro contable de finanzas."
                    )
                    tg_title = "🔄 <b>¡RENOVACIÓN (+30D) REGISTRADA DESDE CHATWOOT!</b>"

                await whatsapp_client.send_chatwoot_message(conv_id, msg_client, private=False)
                await whatsapp_client.send_chatwoot_message(conv_id, note_agent, private=True)

                # 3. Notificar a Telegram
                await send_telegram_message(
                    f"{tg_title}\n\n"
                    f"• Cliente: <b>{res['client_name']}</b> ({badge_type})\n"
                    f"• Servicio: <b>{res['platform']}</b>\n"
                    f"• Cobrado: <b>+{amt_fmt}</b>\n"
                    f"• Vencimiento: <code>{res['new_expiry']}</code>"
                )
                return {"status": "ok", "action": "payment_collected", "details": res}
            else:
                await whatsapp_client.send_chatwoot_message(
                    conv_id,
                    f"❌ [StreamVault CRM] Error al registrar pago: {res.get('error')}",
                    private=True
                )
                return {"status": "error", "error": res.get("error")}

        # -------------------------------------------------------------
        # 5. ENVIAR DATOS DE COBRO Y ALIAS AL CLIENTE (/cbu, /alias, /datos, /transferir)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/cbu", "/alias", "/datos", "/banco", "/transferir")):
            pm = database.get_formatted_payment_methods()
            text = (
                f"¡Hola {client['name']}! Aquí tienes nuestros datos de cobro oficiales:\n\n"
                f"{pm}\n\n"
                f"Una vez realizada la transferencia, envíanos el comprobante por este mismo chat para procesar tu activación o renovación. ¡Muchas gracias! 🙌"
            )
            await whatsapp_client.send_chatwoot_message(conv_id, text, private=False)
            return {"status": "ok", "action": "payment_info_sent"}

        # -------------------------------------------------------------
        # 5.1 ENVIAR O CONSULTAR CATÁLOGO Y PRECIOS (/catalogo, /precios, /planes)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/catalogo", "/precios", "/precio", "/planes", "/combos")):
            parts = content.strip().split(maxsplit=1)
            plat_arg = parts[1].strip() if len(parts) > 1 else None

            client_type_val = client.get("client_type") or "consumidor_final"
            catalog_text = database.generate_catalog_message(
                client_type=client_type_val,
                platform_filter=plat_arg,
                include_payment_methods=True
            )
            await whatsapp_client.send_chatwoot_message(conv_id, catalog_text, private=is_private)
            return {"status": "ok", "action": "catalog_sent", "platform_filter": plat_arg}

        # -------------------------------------------------------------
        # 6. MENÚ DE AYUDA Y COMANDOS DISPONIBLES (/ayuda, /comandos, /help)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/ayuda", "/comandos", "/help")):
            help_text = (
                "🛠️ **COMANDOS RÁPIDOS STREAMVAULT EN CHATWOOT:**\n\n"
                "**Ventas Rápidas (Asignación Automática):**\n"
                "• `/nc_n_casaextra` : Asigna Netflix Casa Extra (1 Pantalla)\n"
                "• `/nc_n_full` : Asigna Netflix Cuenta Completa (4 Pantallas)\n"
                "• `/nc_disney` : Asigna Disney+ Premium\n"
                "• `/nc_max` : Asigna Max (HBO)\n"
                "• `/nc_prime` : Asigna Amazon Prime Video\n"
                "• `/nc_spotify` : Asigna Spotify Premium\n"
                "• `/nc_youtube` : Asigna YouTube Premium\n"
                "• `/nc_paramount` : Asigna Paramount+\n"
                "• `/nc_crunchyroll` : Asigna Crunchyroll\n\n"
                "**Gestión de Comprobantes:**\n"
                "• `/pagoapro_<ID>` : Aprueba el pago #ID, renueva el servicio y confirma al cliente\n"
                "• `/pagodene_<ID>` : Deniega el pago #ID y notifica al cliente que revise el envío\n\n"
                "**Gestión de Cuentas Caídas:**\n"
                "• `/cambiar_<ID>` : Autoriza reemplazo inmediato del reporte #C<ID> con stock libre\n"
                "• `/esperar_<ID>` : Pone en espera prioritaria al cliente del reporte #C<ID>\n"
                "• `/caida` : Reemplazo instantáneo en 1 clic de cuenta caída del cliente actual\n\n"
                "**Consultas & Operaciones:**\n"
                "• `/catalogo` o `/precios` : Enviar lista de precios, combos y stock actualizado\n"
                "• `/stock` : Ver stock libre en tiempo real\n"
                "• `/info` : Ver suscripciones activas del cliente actual\n"
                "• `/cbu` : Enviar datos bancarios y alias al cliente\n"
                "• `/nombre <Nombre>` : Renombrar el contacto en Chatwoot y CRM\n\n"
                "💡 **Consejo Pro:** Puedes escribir el comando en la pestaña **'Nota privada'** (caja amarilla en Chatwoot). Así el cliente no verá el comando y recibirá únicamente el mensaje final con sus accesos."
            )
            await whatsapp_client.send_chatwoot_message(conv_id, help_text, private=True)
            return {"status": "ok", "action": "help_sent"}

        # -------------------------------------------------------------
        # 7. RENOMBRAR CONTACTO EN CHATWOOT Y CRM (/nombre, /renombrar, /name)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/nombre", "/renombrar", "/name")):
            parts = content.strip().split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await whatsapp_client.send_chatwoot_message(
                    conv_id,
                    "⚠️ [StreamVault CRM] Debes indicar el nuevo nombre para este contacto.\n\n"
                    "💡 Ejemplo: `/nombre Samuel Martinez`",
                    private=True
                )
                return {"status": "ok", "action": "missing_name"}

            new_name = parts[1].strip()
            contact_id = sender_meta.get("id") or conversation.get("contact_id")
            if contact_id:
                await whatsapp_client.update_chatwoot_contact(contact_id=int(contact_id), name=new_name)

            database.register_or_update_client(
                name=new_name,
                whatsapp=client.get("whatsapp") or clean_phone,
                client_type=client.get("client_type") or "consumidor_final"
            )

            await whatsapp_client.send_chatwoot_message(
                conv_id,
                f"✅ **[StreamVault CRM] ¡Contacto renombrado con éxito!**\n\n"
                f"• Nuevo Nombre: **{new_name}**\n"
                f"• Teléfono: `{clean_phone or client.get('whatsapp')}`\n"
                f"• Actualizado tanto en Chatwoot como en la base de datos del CRM.",
                private=True
            )
            return {"status": "ok", "action": "contact_renamed", "new_name": new_name}

        # -------------------------------------------------------------
        # 8. APROBACIÓN O RECHAZO DE PAGO POR ID (/pagoapro_<ID>, /pagodene_<ID>)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/pagoapro", "/pagodene", "/aprobarpago", "/rechazarpago")):
            match_app = re.search(r'/(?:pagoapro|aprobarpago)[_\s]+(\d+)', clean_cmd)
            match_rej = re.search(r'/(?:pagodene|rechazarpago)[_\s]+(\d+)', clean_cmd)

            if match_app:
                pid = int(match_app.group(1))
                agent_name = sender.get("name") or "Agente Chatwoot"
                res = database.approve_pending_payment(pid, admin_user=f"Chatwoot ({agent_name})")
                if res.get("success"):
                    p = res.get("payment", {})
                    amt_fmt = p.get("amount_formatted") or database.format_ars(p.get("amount") or 0.0)

                    note_agent = (
                        f"✅ **[StreamVault CRM] ¡Pago #{pid} Aprobado!**\n\n"
                        f"• Cliente: **{p.get('client_name')}**\n"
                        f"• Servicio: **{p.get('platform') or 'Streaming'}**\n"
                        f"• Monto: **{amt_fmt}** ({p.get('bank') or 'Transferencia'})\n"
                        f"• Op: `#{p.get('operation_id') or '-'}`\n"
                        f"• Estado: Servicio al día y acreditado en libro contable."
                    )
                    await whatsapp_client.send_chatwoot_message(conv_id, note_agent, private=True)

                    msg_client = (
                        f"🎉 ¡Hola {p.get('client_name', 'Cliente')}! Confirmamos la recepción y acreditación de tu pago"
                        + (f" de *{amt_fmt}*" if amt_fmt else "") + f" para tu servicio *{p.get('platform') or 'activo'}*.\n\n"
                        f"Tu suscripción quedó confirmada y al día. ¡Muchas gracias por tu confianza! 🙌✨"
                    )
                    await whatsapp_client.send_chatwoot_message(conv_id, msg_client, private=False)

                    await send_telegram_message(
                        f"✅ <b>PAGO #P{pid} APROBADO DESDE CHATWOOT</b>\n\n"
                        f"• Cliente: <b>{p.get('client_name')}</b>\n"
                        f"• Monto: <b>{amt_fmt}</b>\n"
                        f"• Agente: <b>{agent_name}</b>"
                    )
                    return {"status": "ok", "action": "payment_approved", "payment_id": pid}
                else:
                    await whatsapp_client.send_chatwoot_message(
                        conv_id,
                        f"⚠️ **[StreamVault CRM] Error al aprobar pago #{pid}:** {res.get('error')}",
                        private=True
                    )
                    return {"status": "error", "error": res.get("error")}

            elif match_rej:
                pid = int(match_rej.group(1))
                agent_name = sender.get("name") or "Agente Chatwoot"
                res = database.reject_pending_payment(pid, reason="Rechazado desde Chatwoot", admin_user=f"Chatwoot ({agent_name})")
                if res.get("success"):
                    p = res.get("payment", {})
                    note_agent = (
                        f"❌ **[StreamVault CRM] Pago #{pid} Denegado / Rechazado:**\n\n"
                        f"• Cliente: **{p.get('client_name')}**\n"
                        f"• Estado: Rechazado (No acreditado)."
                    )
                    await whatsapp_client.send_chatwoot_message(conv_id, note_agent, private=True)

                    msg_client = (
                        f"Hola {p.get('client_name', 'Cliente')}. Te informamos que no pudimos validar el comprobante de pago enviado (#P{pid}).\n\n"
                        f"Por favor revisa que el importe y los datos de destino correspondan a nuestros datos oficiales, o comunícate con nosotros para verificarlo."
                    )
                    await whatsapp_client.send_chatwoot_message(conv_id, msg_client, private=False)

                    await send_telegram_message(
                        f"❌ <b>COMPROBANTE #P{pid} DENEGADO DESDE CHATWOOT</b>\n\n"
                        f"• Cliente: <b>{p.get('client_name')}</b>\n"
                        f"• Agente: <b>{agent_name}</b>"
                    )
                    return {"status": "ok", "action": "payment_rejected", "payment_id": pid}
                else:
                    await whatsapp_client.send_chatwoot_message(
                        conv_id,
                        f"⚠️ **[StreamVault CRM] Error al denegar pago #{pid}:** {res.get('error')}",
                        private=True
                    )
                    return {"status": "error", "error": res.get("error")}

        # -------------------------------------------------------------
        # 9. REEMPLAZO INSTANTÁNEO DE CUENTA CAÍDA (/caida, /reemplazo)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/caida", "/reemplazo", "/reemplazar")):
            parts = content.strip().split(maxsplit=1)
            target_arg = parts[1].strip() if len(parts) > 1 else ""

            # Si no se pasó argumento específico, buscar cuenta activa del cliente de la conversación
            if not target_arg:
                c_info = database.get_client_360_profile(client["id"])
                active_accs = c_info.get("active_accounts", []) if c_info else []
                if not active_accs:
                    await whatsapp_client.send_chatwoot_message(
                        conv_id,
                        f"⚠️ [StreamVault CRM] **{client['name']}** no registra suscripciones activas ni caídas para reemplazar.",
                        private=True
                    )
                    return {"status": "ok", "action": "no_account_to_replace"}
                target_arg = str(active_accs[0]["id"])

            res = database.report_and_auto_replace_account(
                target_arg,
                reason=f"Reemplazo en 1 clic desde Chatwoot (Conv #{conv_id})"
            )

            if res.get("replaced"):
                new_a = res["new_account"]
                old_a = res["old_account"]
                # 1. Enviar mensaje de nuevas credenciales al cliente (visible en el chat)
                await whatsapp_client.send_chatwoot_message(conv_id, res["whatsapp_message"], private=False)

                # 2. Enviar nota privada para el agente
                note_agent = (
                    f"✅ **[StreamVault CRM] ¡Reemplazo Instantáneo Exitoso!**\n\n"
                    f"• Cliente: **{res.get('client_name')}**\n"
                    f"• Plataforma: **{res.get('platform')}**\n"
                    f"• Cuenta vieja (caída): `{old_a.get('email')}`\n"
                    f"• Nueva cuenta asignada: `{new_a.get('email')}`\n"
                    f"• Clave: `{new_a.get('password')}`" + (f" | Perfil: `{new_a.get('profile_name')}`" if new_a.get('profile_name') else "") + (f" [PIN: `{new_a.get('profile_pin')}`]" if new_a.get('profile_pin') else "") + "\n"
                    f"• Vencimiento mantenido: `{new_a.get('expiry_date')}`\n"
                    f"• Las nuevas credenciales fueron enviadas directamente al cliente en esta conversación."
                )
                await whatsapp_client.send_chatwoot_message(conv_id, note_agent, private=True)

                # 3. Notificar a Telegram
                await send_telegram_message(
                    f"🔄 <b>REEMPLAZO EN 1 CLIC DESDE CHATWOOT</b>\n\n"
                    f"• Cliente: <b>{res.get('client_name')}</b>\n"
                    f"• Servicio: <b>{res.get('platform')}</b>\n"
                    f"• Nueva cuenta: <code>{new_a.get('email')}</code>\n"
                    f"• Clave: <code>{new_a.get('password')}</code>"
                )
                return {"status": "ok", "action": "account_replaced", "details": res}
            elif res.get("out_of_stock"):
                old_a = res["old_account"]
                await whatsapp_client.send_chatwoot_message(
                    conv_id,
                    f"⚠️ **[StreamVault CRM] ¡SIN STOCK LIBRE PARA REEMPLAZAR!**\n\n"
                    f"• Servicio: **{res.get('platform')}**\n"
                    f"• Cuenta: `{old_a.get('email')}`\n"
                    f"La cuenta fue marcada como **CAÍDA** en el CRM, pero no hay stock libre en inventario para asignar una nueva.\n"
                    f"Por favor añade stock de esta plataforma en el Panel Web.",
                    private=True
                )
                return {"status": "ok", "action": "out_of_stock", "details": res}
            else:
                await whatsapp_client.send_chatwoot_message(
                    conv_id,
                    f"⚠️ [StreamVault CRM] Error al reemplazar cuenta: {res.get('error')}",
                    private=True
                )
        # -------------------------------------------------------------
        # 10. AUTORIZACIÓN O ESPERA DE REPORTE DE CAÍDA (/cambiar_<ID>, /esperar_<ID>)
        # -------------------------------------------------------------
        elif clean_cmd.startswith(("/cambiar", "/reemplazar_", "/esperar", "/espera_")):
            match_cambiar = re.search(r'/(?:cambiar|reemplazar)[_\s]+(\d+)', clean_cmd)
            match_esperar = re.search(r'/(?:esperar|espera)[_\s]+(\d+)', clean_cmd)

            if match_cambiar:
                rid = int(match_cambiar.group(1))
                agent_name = sender.get("name") or "Agente Chatwoot"
                res = database.authorize_fallen_report(rid, admin_user=f"Chatwoot ({agent_name})")
                if res.get("success"):
                    if res.get("replaced"):
                        c_phone = res.get("clean_phone")
                        if c_phone:
                            try:
                                await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)
                            except Exception as wa_err:
                                logger.debug(f"Error enviando mensaje WhatsApp al cliente: {wa_err}")

                        new_a = res.get("new_account", {})
                        old_a = res.get("old_account", {})
                        note_agent = (
                            f"✅ **[StreamVault CRM] ¡Reporte #C{rid} Autorizado y Reemplazado!**\n\n"
                            f"• Cliente: **{res.get('client_name')}**\n"
                            f"• Servicio: **{res.get('platform')}**\n"
                            f"• Cuenta anterior: `{old_a.get('email')}`\n"
                            f"• Nueva cuenta asignada: `{new_a.get('email')}`\n"
                            f"• Clave: `{new_a.get('password')}`" + (f" | Perfil: `{new_a.get('profile_name')}`" if new_a.get('profile_name') else "") + "\n"
                            f"• Vencimiento conservado: `{new_a.get('expiry_date')}`\n"
                            f"• Se notificaron las nuevas credenciales al cliente por WhatsApp."
                        )
                        await whatsapp_client.send_chatwoot_message(conv_id, note_agent, private=True)

                        await send_telegram_message(
                            f"✅ <b>REPORTE #C{rid} AUTORIZADO DESDE CHATWOOT</b>\n\n"
                            f"• Cliente: <b>{res.get('client_name')}</b>\n"
                            f"• Servicio: <b>{res.get('platform')}</b>\n"
                            f"• Nueva Cuenta: <code>{new_a.get('email')}</code>\n"
                            f"• Agente: <b>{agent_name}</b>"
                        )
                        return {"status": "ok", "action": "report_authorized", "report_id": rid}
                    elif res.get("out_of_stock"):
                        await whatsapp_client.send_chatwoot_message(
                            conv_id,
                            f"⚠️ **[StreamVault CRM] ¡Sin stock libre para autorizar reporte #C{rid}!**\n\n"
                            f"No hay cuentas libres en inventario para esa plataforma. Carga stock en el panel web para proceder.",
                            private=True
                        )
                        return {"status": "ok", "action": "out_of_stock", "report_id": rid}
                    elif res.get("already_resolved"):
                        await whatsapp_client.send_chatwoot_message(
                            conv_id,
                            f"ℹ️ [StreamVault CRM] El reporte #C{rid} ya fue resuelto previamente.",
                            private=True
                        )
                        return {"status": "ok", "action": "already_resolved", "report_id": rid}
                else:
                    await whatsapp_client.send_chatwoot_message(
                        conv_id,
                        f"⚠️ **[StreamVault CRM] Error al autorizar reporte #C{rid}:** {res.get('error')}",
                        private=True
                    )
                    return {"status": "error", "error": res.get("error")}

            elif match_esperar:
                rid = int(match_esperar.group(1))
                agent_name = sender.get("name") or "Agente Chatwoot"
                res = database.put_fallen_report_on_wait(rid, admin_user=f"Chatwoot ({agent_name})")
                if res.get("success"):
                    c_phone = res.get("clean_phone")
                    if c_phone:
                        try:
                            await whatsapp_client.send_text_message(c_phone, res["whatsapp_message"], delay_seconds=1.0)
                        except Exception as wa_err:
                            logger.debug(f"Error enviando mensaje WhatsApp al cliente: {wa_err}")

                    note_agent = (
                        f"⏳ **[StreamVault CRM] Reporte #C{rid} Puesto en Espera:**\n\n"
                        f"• Cliente: **{res.get('client_name')}**\n"
                        f"• Estado: En cola de atención prioritaria.\n"
                        f"• Se notificó al cliente para que aguarde la nueva cuenta.\n\n"
                        f"💡 Para asignarle la cuenta cuando la tengas lista, escribe `/cambiar_{rid}`."
                    )
                    await whatsapp_client.send_chatwoot_message(conv_id, note_agent, private=True)

                    await send_telegram_message(
                        f"⏳ <b>CLIENTE PUESTO EN ESPERA (#C{rid}) DESDE CHATWOOT</b>\n\n"
                        f"• Cliente: <b>{res.get('client_name')}</b>\n"
                        f"• Agente: <b>{agent_name}</b>"
                    )
                    return {"status": "ok", "action": "report_put_on_wait", "report_id": rid}
                else:
                    await whatsapp_client.send_chatwoot_message(
                        conv_id,
                        f"⚠️ **[StreamVault CRM] Error al poner en espera reporte #C{rid}:** {res.get('error')}",
                        private=True
                    )
                    return {"status": "error", "error": res.get("error")}

        return {"status": "ignored", "reason": "unknown_command"}

    except Exception as err:
        logger.error(f"Error ejecutando comando Chatwoot '{clean_cmd}': {err}", exc_info=True)
        await whatsapp_client.send_chatwoot_message(
            conv_id,
            f"❌ **[StreamVault CRM] Error al ejecutar `{clean_cmd}`:** {str(err)}",
            private=True
        )
        return {"status": "error", "error": str(err)}
