import logging
import re
from typing import Optional, List, Dict, Any

from mcp_server.instance import mcp
import database
import whatsapp_client

logger = logging.getLogger("mcp.group_tools")

@mcp.tool()
def listar_grupos_whatsapp() -> str:
    """Lista todos los grupos de WhatsApp configurados en StreamVault, indicando si el bot está habilitado, estado de antilink y bienvenida."""
    groups = database.list_groups_config()
    bot_mode = database.get_bot_mode()
    if not groups:
        return f"ℹ️ No hay grupos registrados en la base de datos (Modo actual del bot: {bot_mode.upper()}). Puedes sincronizarlos usando 'sincronizar_grupos_whatsapp' o desde el panel web."

    out = [f"👥 *GRUPOS DE WHATSAPP REGISTRADOS* (Modo global: *{bot_mode.upper()}*):\n"]
    for g in groups:
        jid = g.get("group_jid", "")
        name = g.get("group_name") or jid
        bot_st = "🟢 Activo (Responde)" if g.get("bot_enabled") else "⚪ Inactivo (Ignorado)"
        anti_st = f"🛡️ Antilink ({g.get('antilink_action', 'delete')})" if g.get("antilink_enabled") else "⚪ Sin antilink"
        wel_st = "👋 Bienvenida ON" if g.get("welcome_enabled") else "⚪ Bienvenida OFF"
        out.append(f"• *{name}*\n  ID: `{jid}`\n  Bot: {bot_st} | {anti_st} | {wel_st}\n")

    return "\n".join(out)


@mcp.tool()
async def sincronizar_grupos_whatsapp() -> str:
    """Escanea y sincroniza los grupos donde participa el bot de WhatsApp desde Evolution API y los registra en la base de datos."""
    try:
        raw_groups = await whatsapp_client.fetch_all_groups(get_participants=False)
        count = 0
        for g in raw_groups:
            jid = g.get("id") or g.get("jid") or g.get("remoteJid") or g.get("groupJid")
            if not jid:
                continue
            jid_str = str(jid).strip()
            if "@g.us" not in jid_str:
                if jid_str.isdigit() and len(jid_str) >= 10:
                    jid_str = f"{jid_str}@g.us"
                else:
                    continue
            name = g.get("subject") or g.get("name") or g.get("pushName") or g.get("notify") or jid_str
            database.upsert_group_config(group_jid=jid_str, group_name=str(name).strip())
            count += 1
        return f"✅ Sincronización exitosa: Se escanearon y registraron {count} grupos desde WhatsApp."
    except Exception as e:
        logger.error(f"Error sincronizando grupos MCP: {e}")
        return f"❌ Error al sincronizar grupos desde Evolution API: {str(e)}"



@mcp.tool()
def configurar_grupo_whatsapp(
    group_jid: str,
    bot_habilitado: Optional[bool] = None,
    antilink_habilitado: Optional[bool] = None,
    antilink_accion: Optional[str] = None,
    bienvenida_habilitada: Optional[bool] = None,
    mensaje_bienvenida: Optional[str] = None,
    mensaje_despedida: Optional[str] = None
) -> str:
    """Configura las políticas del bot en un grupo específico: bot_habilitado (True/False), antilink_habilitado, antilink_accion ('delete' o 'kick'), bienvenida_habilitada, mensaje_bienvenida, mensaje_despedida."""
    clean_jid = group_jid.strip()
    if not clean_jid or "@g.us" not in clean_jid:
        return "❌ Error: Debes proporcionar un JID de grupo válido terminado en @g.us."

    bot_val = 1 if bot_habilitado is True else (0 if bot_habilitado is False else None)
    anti_val = 1 if antilink_habilitado is True else (0 if antilink_habilitado is False else None)
    wel_val = 1 if bienvenida_habilitada is True else (0 if bienvenida_habilitada is False else None)

    updated = database.upsert_group_config(
        group_jid=clean_jid,
        bot_enabled=bot_val,
        antilink_enabled=anti_val,
        antilink_action=antilink_accion,
        welcome_enabled=wel_val,
        welcome_message=mensaje_bienvenida,
        goodbye_message=mensaje_despedida
    )
    name = updated.get("group_name") or clean_jid
    return f"✅ Grupo '{name}' actualizado correctamente.\n• Bot Activo: {bool(updated.get('bot_enabled'))}\n• Antilink: {bool(updated.get('antilink_enabled'))} ({updated.get('antilink_action')})\n• Bienvenida: {bool(updated.get('welcome_enabled'))}"


@mcp.tool()
async def enviar_tagall_grupo(group_jid: str, mensaje: str = "Aviso importante para todos los integrantes") -> str:
    """Menciona a todos los integrantes de un grupo de WhatsApp con un comunicado comercial o aviso general (@everyone / tagall)."""
    clean_jid = group_jid.strip()
    if not clean_jid or "@g.us" not in clean_jid:
        return "❌ Error: Debes indicar un group_jid válido de WhatsApp (@g.us)."

    res = await whatsapp_client.send_group_tagall(clean_jid, message=mensaje, sender_name="Gemini Assistant")
    if res.get("status") == "error":
        return f"❌ No se pudo enviar el tagall: {res.get('message', 'Error desconocido')}"
    return f"📢 Tagall enviado con éxito al grupo `{clean_jid}` con {res.get('total_mentioned', 0)} miembros mencionados."


@mcp.tool()
async def controlar_grupo(group_jid: str, accion: str) -> str:
    """Ejecuta acciones administrativas sobre el grupo: 'cerrar' o 'mute' (solo administradores pueden escribir), 'abrir' o 'unmute' (todos pueden escribir), 'link' o 'enlace' (obtiene el link de invitación)."""
    clean_jid = group_jid.strip()
    clean_act = accion.strip().lower()
    if not clean_jid or "@g.us" not in clean_jid:
        return "❌ Error: Debes indicar un group_jid válido (@g.us)."

    if clean_act in ("cerrar", "mute", "silenciar"):
        await whatsapp_client.update_group_setting(clean_jid, "announcement")
        return f"🔒 Grupo `{clean_jid}` cerrado. Ahora solo los administradores pueden enviar mensajes."
    elif clean_act in ("abrir", "unmute"):
        await whatsapp_client.update_group_setting(clean_jid, "not_announcement")
        return f"📢 Grupo `{clean_jid}` abierto. Todos los integrantes pueden escribir."
    elif clean_act in ("link", "enlace", "invitacion"):
        invite_code = await whatsapp_client.get_group_invite_code(clean_jid)
        if invite_code:
            return f"🔗 Enlace de invitación del grupo:\n{invite_code}"
        return "⚠️ No se pudo obtener el enlace (verifica que el bot sea administrador del grupo)."
    else:
        return f"❌ Acción no reconocida: '{accion}'. Opciones válidas: 'cerrar', 'abrir', 'link'."


@mcp.tool()
async def moderar_participante_grupo(group_jid: str, participante_telefono: str, accion: str) -> str:
    """Modera a un participante de un grupo: 'expulsar'/'kick' (elimina del grupo), 'promover'/'promote' (hace administrador), 'degradar'/'demote' (quita cargo de admin)."""
    clean_jid = group_jid.strip()
    digits = re.sub(r'[^0-9]', '', participante_telefono)
    clean_act = accion.strip().lower()
    if not clean_jid or "@g.us" not in clean_jid or not digits:
        return "❌ Error: Debes indicar un group_jid válido y el teléfono del participante."

    action_map = {
        "expulsar": "remove",
        "kick": "remove",
        "remove": "remove",
        "promover": "promote",
        "promote": "promote",
        "admin": "promote",
        "degradar": "demote",
        "demote": "demote"
    }
    evo_act = action_map.get(clean_act)
    if not evo_act:
        return f"❌ Acción desconocida: '{accion}'. Usa: 'expulsar', 'promover' o 'degradar'."

    res = await whatsapp_client.update_group_participant(clean_jid, evo_act, [digits])
    action_texts = {
        "remove": f"👢 Participante @{digits} expulsado del grupo.",
        "promote": f"⭐ Participante @{digits} promovido a Administrador.",
        "demote": f"🔻 Participante @{digits} degradado a miembro estándar."
    }
    return f"✅ {action_texts.get(evo_act, 'Acción ejecutada')} (Detalles: {res.get('status', 'ok')})"


@mcp.tool()
def configurar_modo_bot(modo: str) -> str:
    """Configura el modo global de operación del bot: 'public' (atiende DMs y grupos habilitados), 'private' (solo atiende chats privados individuales), 'self' (solo atiende al dueño/administrador)."""
    clean_mode = modo.strip().lower()
    if clean_mode not in ("public", "private", "self"):
        return "❌ Modo inválido. Las opciones permitidas son: 'public', 'private', 'self'."

    new_mode = database.set_bot_mode(clean_mode)
    descriptions = {
        "public": "🌐 Responde a clientes en privados y a los grupos autorizados de la lista blanca.",
        "private": "🔒 Solo atiende mensajes directos individuales (DMs). Ignora silenciosamente todos los grupos.",
        "self": "🛡️ Solo atiende órdenes provenientes del número del administrador/dueño."
    }
    return f"✅ Modo de operación del bot actualizado a: *{new_mode.upper()}*\n{descriptions.get(new_mode, '')}"


@mcp.tool()
def obtener_modo_bot() -> str:
    """Consulta el modo operativo actual del bot ('public', 'private' o 'self')."""
    mode = database.get_bot_mode()
    return f"ℹ️ El modo de operación actual del bot es: *{mode.upper()}*."


@mcp.tool()
def banear_silencioso(telefono_o_grupo: str, motivo: str = "") -> str:
    """Aplica baneo silencioso (Silent Ban - Atlas-MD) a un teléfono o grupo (@g.us). El bot descartará absolutamente cualquier mensaje de este origen sin contestar nada (invisible ante spam)."""
    clean_target = telefono_o_grupo.strip()
    if not clean_target:
        return "❌ Error: Debes ingresar un número de teléfono o ID de grupo."

    ok = database.add_silent_ban(clean_target, reason=motivo)
    if ok:
        return f"🛡️ Baneo silencioso activado para `{clean_target}`. Motivo: {motivo or 'Sin motivo'}. El bot ignorará todos sus mensajes sin responder."
    return f"❌ No se pudo registrar el baneo para `{clean_target}`."


@mcp.tool()
def desbanear_silencioso(telefono_o_grupo: str) -> str:
    """Remueve el baneo silencioso a un usuario o grupo, permitiendo que el bot vuelva a procesar sus mensajes normalmente."""
    clean_target = telefono_o_grupo.strip()
    if not clean_target:
        return "❌ Error: Debes ingresar un número o ID de grupo."

    removed = database.remove_silent_ban(clean_target)
    if removed:
        return f"✅ Baneo silencioso levantado para `{clean_target}`. El bot vuelve a responder a este origen."
    return f"ℹ️ El objetivo `{clean_target}` no se encontraba en la lista de baneos silenciosos."


@mcp.tool()
def listar_baneos_silenciosos() -> str:
    """Lista todos los números de teléfono y grupos que se encuentran bajo baneo silencioso."""
    bans = database.list_silent_bans()
    if not bans:
        return "🛡️ La lista de baneos silenciosos está vacía. Ningún usuario o grupo está siendo ignorado actualmente."

    out = ["🛡️ *LISTA DE BANEOS SILENCIOSOS ACTIVOS (Atlas-MD):*\n"]
    for b in bans:
        out.append(f"• *{b.get('target_id')}* ({b.get('target_type')}) - Motivo: {b.get('reason') or 'N/A'} (Desde: {b.get('created_at')})")
    return "\n".join(out)


@mcp.tool()
async def agregar_grupo_whatsapp(
    enlace_o_jid: str,
    nombre_grupo: str = "",
    bot_habilitado: bool = True,
    antilink_habilitado: bool = False
) -> str:
    """Registra manualmente un grupo de WhatsApp en el sistema usando su enlace de invitación (https://chat.whatsapp.com/...) o JID (@g.us)."""
    target = enlace_o_jid.strip()
    if not target:
        return "❌ Error: Debes ingresar un enlace de invitación o JID de grupo."

    resolved_jid = ""
    resolved_name = nombre_grupo.strip()

    invite_code_match = re.search(r'(?:chat\.whatsapp\.com\/)([a-zA-Z0-9_-]+)', target)
    if invite_code_match or (not "@" in target and len(target) in (20, 21, 22, 23, 24, 25)):
        code = invite_code_match.group(1) if invite_code_match else target
        info = await whatsapp_client.find_group_info_from_invite_code(code)
        if info:
            resolved_jid = info.get("id") or info.get("jid") or info.get("groupJid") or ""
            if not resolved_name:
                resolved_name = info.get("subject") or info.get("name") or ""
        if not resolved_jid:
            join_res = await whatsapp_client.accept_group_invite_code(code)
            if join_res and isinstance(join_res, dict):
                resolved_jid = join_res.get("id") or join_res.get("jid") or join_res.get("groupJid") or ""
                if not resolved_name:
                    resolved_name = join_res.get("subject") or join_res.get("name") or ""

    if not resolved_jid:
        if "@g.us" in target:
            resolved_jid = target
        elif target.isdigit() and len(target) >= 10:
            resolved_jid = f"{target}@g.us"

    if not resolved_jid or "@g.us" not in resolved_jid:
        return "❌ Error: No se pudo resolver el JID del grupo. Ingresa el JID directamente (ej: 120363...@g.us)."

    if not resolved_name:
        try:
            grp_meta = await whatsapp_client.find_group_info(resolved_jid)
            if grp_meta and isinstance(grp_meta, dict):
                resolved_name = grp_meta.get("subject") or grp_meta.get("name") or ""
        except Exception:
            pass

    if not resolved_name:
        resolved_name = f"Grupo {resolved_jid.split('@')[0]}"

    saved = database.upsert_group_config(
        group_jid=resolved_jid,
        group_name=resolved_name,
        bot_enabled=1 if bot_habilitado else 0,
        antilink_enabled=1 if antilink_habilitado else 0
    )
    return f"✅ Grupo registrado exitosamente:\n• Nombre: *{resolved_name}*\n• ID: `{resolved_jid}`\n• Bot Activo: {bot_habilitado}\n• Antilink: {antilink_habilitado}"


@mcp.tool()
def eliminar_grupo_whatsapp(group_jid: str) -> str:
    """Elimina un grupo de WhatsApp de la lista de configuración y whitelist del bot."""
    clean_jid = group_jid.strip()
    if not clean_jid:
        return "❌ Error: Debes ingresar el JID del grupo a eliminar."
    deleted = database.delete_group_config(clean_jid)
    if deleted:
        return f"🗑️ Grupo `{clean_jid}` eliminado de la base de datos."
    return f"ℹ️ El grupo `{clean_jid}` no estaba registrado."


@mcp.tool()
def consultar_ranking_grupo_whatsapp(group_jid: str, limite: int = 10) -> str:
    """Consulta el ranking de miembros más activos y sus rangos de gamificación (Diamante, Oro, Plata, Bronce) en un grupo de WhatsApp."""
    clean_jid = group_jid.strip()
    if not clean_jid:
        return "❌ Error: Debes ingresar el JID del grupo."
    records = database.get_group_leaderboard(clean_jid, limit=limite)
    g_cfg = database.get_group_config(clean_jid)
    g_name = g_cfg.get("group_name") if g_cfg else "Grupo"
    return database.GamificationManager.format_leaderboard(records, group_name=g_name)

