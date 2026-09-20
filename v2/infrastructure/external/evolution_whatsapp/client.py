import os
import re
import logging
from typing import Dict, Any, Optional, List, Tuple, Union
import httpx

import database

logger = logging.getLogger("whatsapp_client")

def get_evolution_config() -> Dict[str, Any]:
    """Obtiene la configuración activa de Evolution API desde la base de datos o variables de entorno."""
    settings = database.get_whatsapp_api_settings()
    api_url = (settings.get("api_url") or os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080")).strip().rstrip("/")
    api_key = (settings.get("api_key") or os.getenv("EVOLUTION_API_KEY", "mcp-evolution-key-2026")).strip()
    instance_name = (settings.get("instance_name") or os.getenv("EVOLUTION_INSTANCE_NAME", "streaming-bot")).strip()

    return {
        "api_url": api_url,
        "api_key": api_key,
        "instance_name": instance_name,
        "auto_send_expiry": bool(settings.get("auto_send_expiry", 0)),
        "auto_send_sales": bool(settings.get("auto_send_sales", 0)),
        "auto_reply_enabled": bool(settings.get("auto_reply_enabled", 1)),
    }

def get_headers(api_key: str) -> Dict[str, str]:
    return {
        "apikey": api_key,
        "Content-Type": "application/json"
    }

async def check_connection_status() -> Dict[str, Any]:
    """Verifica el estado de conexión de la instancia con los servidores de WhatsApp."""
    config = get_evolution_config()
    url = f"{config['api_url']}/instance/connectionState/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                inst = data.get("instance", {})
                state = inst.get("state", "close")
                return {
                    "connected": state == "open",
                    "state": state,
                    "instance": config["instance_name"],
                    "error": None
                }
            elif resp.status_code == 404:
                return {
                    "connected": False,
                    "state": "not_created",
                    "instance": config["instance_name"],
                    "error": "La instancia aún no existe en Evolution API."
                }
            else:
                return {
                    "connected": False,
                    "state": "error",
                    "instance": config["instance_name"],
                    "error": f"Error HTTP {resp.status_code}: {resp.text[:200]}"
                }
    except Exception as e:
        logger.warning(f"No se pudo conectar con Evolution API ({url}): {e}")
        return {
            "connected": False,
            "state": "unreachable",
            "instance": config["instance_name"],
            "error": f"No se pudo contactar a Evolution API: {str(e)}"
        }

async def create_instance_if_not_exists() -> Dict[str, Any]:
    """Crea la instancia en Evolution API si no existe actualmente."""
    config = get_evolution_config()
    url = f"{config['api_url']}/instance/create"
    headers = get_headers(config["api_key"])
    payload = {
        "instanceName": config["instance_name"],
        "token": config["api_key"],
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Instancia '{config['instance_name']}' creada exitosamente en Evolution API.")
                return {"success": True, "data": resp.json()}
            elif resp.status_code == 403 or "already in use" in resp.text:
                return {"success": True, "message": "La instancia ya existía."}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def get_qr_code() -> Dict[str, Any]:
    """Obtiene el código QR en base64 para vincular la sesión de WhatsApp escaneando desde el teléfono."""
    status = await check_connection_status()
    if status.get("connected"):
        return {
            "success": True,
            "connected": True,
            "message": "WhatsApp ya se encuentra conectado y activo."
        }

    config = get_evolution_config()
    if status.get("state") == "not_created":
        await create_instance_if_not_exists()

    url = f"{config['api_url']}/instance/connect/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                base64_qr = data.get("base64") or data.get("qrcode", {}).get("base64")
                code = data.get("code") or data.get("pairingCode")
                return {
                    "success": True,
                    "connected": False,
                    "base64": base64_qr,
                    "pairingCode": code,
                    "instance": config["instance_name"]
                }
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def send_presence(
    phone: str,
    presence: str = "composing",
    delay_ms: int = 3500
) -> Dict[str, Any]:
    """Envía el estado de presencia ('composing' / 'escribiendo...') a través de Evolution API para apariencia 100% humana."""
    clean_phone = re.sub(r'[^0-9]', '', str(phone or ""))
    if not clean_phone or len(clean_phone) < 8:
        return {"success": False, "error": "Número inválido"}

    config = get_evolution_config()
    url = f"{config['api_url']}/chat/sendPresence/{config['instance_name']}"
    headers = get_headers(config["api_key"])
    payload = {
        "number": clean_phone,
        "presence": presence,
        "delay": delay_ms
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            return {"success": resp.status_code in (200, 201)}
    except Exception as e:
        logger.debug(f"Aviso de presencia a {clean_phone} omitido: {e}")
        return {"success": False, "error": str(e)}

async def send_text_message(
    phone: str,
    text: str,
    delay_seconds: float = 2.0,
    simulate_typing: bool = True
) -> Dict[str, Any]:
    """Envía un mensaje de texto por WhatsApp a través de Evolution API.
    Aplica limpieza estricta de teléfono, simulación previa de 'Escribiendo...' y delay para protección anti-baneo.
    """
    clean_phone = re.sub(r'[^0-9]', '', str(phone or ""))
    if not clean_phone or len(clean_phone) < 8:
        return {"success": False, "error": f"Número de teléfono inválido: '{phone}'"}

    config = get_evolution_config()
    url = f"{config['api_url']}/message/sendText/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    delay_ms = int(max(delay_seconds, 1.0) * 1000)

    # Activar estado de presencia 'escribiendo...' antes del despacho
    if simulate_typing:
        try:
            await send_presence(clean_phone, presence="composing", delay_ms=delay_ms)
            # Pequeña pausa para permitir que WhatsApp propague el estado 'escribiendo...'
            await asyncio.sleep(min(delay_seconds, 3.0))
        except Exception:
            pass

    payload = {
        "number": clean_phone,
        "text": text,
        "delay": delay_ms,
        "linkPreview": False
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                msg_id = data.get("key", {}).get("id") or "sent"
                logger.info(f"Mensaje WhatsApp enviado con éxito a {clean_phone} (ID: {msg_id})")
                return {
                    "success": True,
                    "phone": clean_phone,
                    "message_id": msg_id,
                    "data": data
                }
            else:
                err = f"HTTP {resp.status_code}: {resp.text[:250]}"
                logger.error(f"Fallo al enviar WhatsApp a {clean_phone}: {err}")
                return {"success": False, "phone": clean_phone, "error": err}
    except Exception as e:
        err = str(e)
        logger.error(f"Excepción al enviar WhatsApp a {clean_phone}: {err}")
        return {"success": False, "phone": clean_phone, "error": err}


async def send_media_message(
    phone: str,
    base64_data: str,
    mime_type: str = "image/jpeg",
    file_name: str = "comprobante.jpg",
    caption: str = "",
    delay_seconds: float = 2.0
) -> Dict[str, Any]:
    """Envía un archivo multimedia (imagen o PDF) por WhatsApp a través de Evolution API."""
    clean_phone = re.sub(r'[^0-9]', '', str(phone or ""))
    if not clean_phone or len(clean_phone) < 8:
        return {"success": False, "error": f"Número de teléfono inválido: '{phone}'"}

    config = get_evolution_config()
    url = f"{config['api_url']}/message/sendMedia/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    clean_b64 = base64_data
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",")[1]

    media_type = "image" if "image" in mime_type.lower() else "document"
    delay_ms = int(max(delay_seconds, 1.0) * 1000)

    payload = {
        "number": clean_phone,
        "mediatype": media_type,
        "mimetype": mime_type,
        "caption": caption,
        "media": clean_b64,
        "fileName": file_name,
        "delay": delay_ms
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                msg_id = data.get("key", {}).get("id") or "sent"
                logger.info(f"Media WhatsApp enviada con éxito a {clean_phone} (ID: {msg_id})")
                return {"success": True, "phone": clean_phone, "message_id": msg_id, "data": data}
            else:
                err = f"HTTP {resp.status_code}: {resp.text[:250]}"
                logger.error(f"Fallo al enviar media WhatsApp a {clean_phone}: {err}")
                return {"success": False, "phone": clean_phone, "error": err}
    except Exception as e:
        err = str(e)
        logger.error(f"Excepción al enviar media WhatsApp a {clean_phone}: {err}")
        return {"success": False, "phone": clean_phone, "error": err}


async def send_reaction(
    remote_jid: str,
    message_id: str,
    emoji: str,
    from_me: bool = False
) -> Dict[str, Any]:
    """Envía una reacción (emoji) a un mensaje de WhatsApp específico vía Evolution API."""
    clean_jid = str(remote_jid or "").strip()
    if not clean_jid:
        return {"success": False, "error": "remote_jid requerido para reaccionar"}
    if "@" not in clean_jid:
        clean_digits = re.sub(r'[^0-9]', '', clean_jid)
        clean_jid = f"{clean_digits}@s.whatsapp.net"

    config = get_evolution_config()
    url = f"{config['api_url']}/message/sendReaction/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    payload = {
        "key": {
            "remoteJid": clean_jid,
            "fromMe": from_me,
            "id": message_id
        },
        "reaction": emoji
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Reacción '{emoji}' enviada a mensaje {message_id} en {clean_jid}")
                return {"success": True, "data": resp.json()}
            else:
                err = f"HTTP {resp.status_code}: {resp.text[:250]}"
                logger.warning(f"Error enviando reacción a {clean_jid}: {err}")
                return {"success": False, "error": err}
    except Exception as e:
        logger.warning(f"Excepción enviando reacción a {clean_jid}: {e}")
        return {"success": False, "error": str(e)}


async def send_contact_vcard(
    phone: str,
    full_name: str,
    contact_phone: str,
    organization: str = "StreamVault"
) -> Dict[str, Any]:
    """Envía una tarjeta de contacto (VCard) por WhatsApp para que el cliente agende con 1 clic."""
    clean_phone = re.sub(r'[^0-9]', '', str(phone or ""))
    if not clean_phone or len(clean_phone) < 8:
        return {"success": False, "error": f"Número de destino inválido: '{phone}'"}

    clean_target = re.sub(r'[^0-9]', '', str(contact_phone or ""))
    if not clean_target:
        clean_target = clean_phone

    config = get_evolution_config()
    url = f"{config['api_url']}/message/sendContact/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    payload = {
        "number": clean_phone,
        "contact": [
            {
                "fullName": full_name or "Soporte StreamVault",
                "wuid": f"{clean_target}@s.whatsapp.net",
                "phoneNumber": f"+{clean_target}",
                "organization": organization or "StreamVault"
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"VCard de contacto '{full_name}' enviada con éxito a {clean_phone}")
                return {"success": True, "phone": clean_phone, "data": resp.json()}
            else:
                err = f"HTTP {resp.status_code}: {resp.text[:250]}"
                logger.error(f"Fallo al enviar VCard a {clean_phone}: {err}")
                return {"success": False, "error": err}
    except Exception as e:
        logger.error(f"Excepción al enviar VCard a {clean_phone}: {e}")
        return {"success": False, "error": str(e)}


async def send_sticker(
    phone: str,
    sticker_data_or_url: str
) -> Dict[str, Any]:
    """Envía un sticker de WhatsApp (URL o base64) a través de Evolution API."""
    clean_phone = re.sub(r'[^0-9]', '', str(phone or ""))
    if not clean_phone or len(clean_phone) < 8:
        return {"success": False, "error": f"Número de teléfono inválido: '{phone}'"}

    clean_sticker = str(sticker_data_or_url or "").strip()
    if not clean_sticker:
        return {"success": False, "error": "Datos o URL de sticker requeridos"}

    if "," in clean_sticker and not clean_sticker.startswith("http"):
        clean_sticker = clean_sticker.split(",")[1]

    config = get_evolution_config()
    url = f"{config['api_url']}/message/sendSticker/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    payload = {
        "number": clean_phone,
        "sticker": clean_sticker
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Sticker enviado con éxito a {clean_phone}")
                return {"success": True, "phone": clean_phone, "data": resp.json()}
            else:
                err = f"HTTP {resp.status_code}: {resp.text[:250]}"
                logger.error(f"Fallo al enviar sticker a {clean_phone}: {err}")
                return {"success": False, "error": err}
    except Exception as e:
        logger.error(f"Excepción al enviar sticker a {clean_phone}: {e}")
        return {"success": False, "error": str(e)}


async def mark_as_read(
    remote_jid: str,
    message_id: str,
    from_me: bool = False
) -> Dict[str, Any]:
    """Marca un mensaje como leído (enciende el doble tilde azul) vía Evolution API."""
    clean_jid = str(remote_jid or "").strip()
    if not clean_jid:
        return {"success": False, "error": "remote_jid requerido"}
    if "@" not in clean_jid:
        clean_digits = re.sub(r'[^0-9]', '', clean_jid)
        clean_jid = f"{clean_digits}@s.whatsapp.net"

    config = get_evolution_config()
    url = f"{config['api_url']}/chat/markMessageAsRead/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    payload = {
        "readMessages": [
            {
                "remoteJid": clean_jid,
                "fromMe": from_me,
                "id": message_id
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def configure_webhook(webhook_url: str) -> Dict[str, Any]:
    """Registra o actualiza la URL del webhook en Evolution API para recibir eventos."""
    config = get_evolution_config()
    url = f"{config['api_url']}/webhook/set/{config['instance_name']}"
    headers = get_headers(config["api_key"])
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "byEvents": False,
            "base64": False,
            "events": [
                "MESSAGES_UPSERT"
            ]
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Webhook configurado exitosamente en Evolution API -> {webhook_url}")
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def logout_instance() -> Dict[str, Any]:
    """Cierra la sesión activa de WhatsApp en Evolution API."""
    config = get_evolution_config()
    url = f"{config['api_url']}/instance/logout/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code == 200:
                logger.info(f"Sesión cerrada para la instancia '{config['instance_name']}'")
                return {"success": True}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def configure_chatwoot(
    chatwoot_url: str,
    chatwoot_token: str,
    account_id: str = "1",
    sign_msg: bool = False,
    reopen_conversation: bool = True,
    conversation_pending: bool = False,
    import_contacts: bool = True,
    import_messages: bool = True,
    days_limit_import: int = 3
) -> Dict[str, Any]:
    """Vincula la instancia de WhatsApp en Evolution API con Chatwoot."""
    config = get_evolution_config()
    url = f"{config['api_url']}/chatwoot/set/{config['instance_name']}"
    headers = get_headers(config["api_key"])
    payload = {
        "enabled": True,
        "accountId": str(account_id),
        "token": chatwoot_token.strip(),
        "url": chatwoot_url.strip().rstrip("/"),
        "signMsg": sign_msg,
        "reopenConversation": reopen_conversation,
        "conversationPending": conversation_pending,
        "importContacts": import_contacts,
        "importMessages": import_messages,
        "daysLimitImportMessages": days_limit_import
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Chatwoot configurado exitosamente en Evolution API para '{config['instance_name']}'")
                database.save_chatwoot_settings(
                    url=chatwoot_url.strip(),
                    token=chatwoot_token.strip(),
                    account_id=str(account_id).strip(),
                    enabled=1
                )
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def get_chatwoot_status() -> Dict[str, Any]:
    """Obtiene la configuración actual de integración de Chatwoot en Evolution API."""
    config = get_evolution_config()
    url = f"{config['api_url']}/chatwoot/find/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_chatwoot_config() -> Dict[str, Any]:
    """Obtiene la configuración activa de Chatwoot desde la base de datos."""
    cfg = database.get_chatwoot_settings()
    if not (cfg.get("token") or "").strip():
        cfg["token"] = "ZRzCpt75vxkyiUC7H1otEoog"
    return cfg

def get_chatwoot_headers(token: str) -> Dict[str, str]:
    return {
        "api-access-token": token,
        "api_access_token": token,
        "HTTP_API_ACCESS_TOKEN": token,
        "Content-Type": "application/json"
    }


async def test_chatwoot_connection(url: str = "", token: str = "", account_id: str = "1") -> Dict[str, Any]:
    """Valida la conectividad y las credenciales con Chatwoot consultando el perfil del usuario."""
    cfg = get_chatwoot_config()
    target_url = (url or cfg.get("url") or "https://chat.joif.net").strip().rstrip("/")
    target_token = (token or cfg.get("token") or "").strip()
    target_acc = str(account_id or cfg.get("account_id") or "1").strip()

    if not target_token:
        return {
            "success": False,
            "error": "Falta ingresar el Token de acceso de Chatwoot. En chat.joif.net ve a Perfil (abajo a la izquierda) -> Configuración de perfil -> Token de acceso y cópialo."
        }

    base_urls = [target_url]
    if "chatwoot-rails" in target_url:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in target_url:
        base_urls.append("http://chatwoot-rails:3000")

    headers = get_chatwoot_headers(target_token)
    last_err = ""

    for base in base_urls:
        profile_url = f"{base.rstrip('/')}/api/v1/profile"
        try:
            async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                resp = await client.get(profile_url, headers=headers)
                if resp.status_code == 200:
                    user_data = resp.json()
                    user_name = user_data.get("name") or user_data.get("email") or "Usuario"
                    return {
                        "success": True,
                        "user": user_data,
                        "base_url": base,
                        "message": f"Conexión exitosa como {user_name} ({user_data.get('email', '')})"
                    }
                elif resp.status_code == 401:
                    return {
                        "success": False,
                        "error": "Token de Chatwoot no autorizado (Error 401). Verifica que hayas copiado tu 'Token de acceso' de usuario en Chatwoot (Perfil -> Configuración de perfil -> Token de acceso)."
                    }
                elif resp.status_code == 403:
                    return {
                        "success": False,
                        "error": "Acceso denegado en Chatwoot (Error 403). Verifica los permisos de tu usuario."
                    }
                else:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            last_err = str(e)
            continue

    return {"success": False, "error": f"No se pudo conectar con Chatwoot en {target_url}: {last_err}"}

async def search_chatwoot_contacts(query: str) -> List[Dict[str, Any]]:
    """Busca contactos en Chatwoot por nombre, teléfono o correo."""
    cfg = get_chatwoot_config()
    token = cfg.get("token") or ""
    if not cfg.get("enabled") or not token:
        return []

    clean_q = str(query or "").strip()
    if not clean_q:
        return []

    # Probar primero URL configurada (interna docker o externa)
    base_urls = [cfg["url"]]
    if "chatwoot-rails" in cfg["url"]:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in cfg["url"]:
        base_urls.append("http://chatwoot-rails:3000")

    for base_url in base_urls:
        url = f"{base_url.rstrip('/')}/api/v1/accounts/{cfg['account_id']}/contacts/search"
        headers = get_chatwoot_headers(token)
        params = {"q": clean_q}

        try:
            async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    payload = data.get("payload", [])
                    if payload:
                        return payload
                elif resp.status_code == 401:
                    logger.warning("Token de Chatwoot inválido o expirado.")
                    break
        except Exception as e:
            logger.debug(f"Fallo al conectar con Chatwoot ({base_url}): {e}")
            continue

    return []

async def list_chatwoot_contacts(page: int = 1) -> List[Dict[str, Any]]:
    """Lista contactos registrados en Chatwoot con paginación."""
    cfg = get_chatwoot_config()
    token = cfg.get("token") or ""
    if not cfg.get("enabled") or not token:
        return []

    base_urls = [cfg["url"]]
    if "chatwoot-rails" in cfg["url"]:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in cfg["url"]:
        base_urls.append("http://chatwoot-rails:3000")

    for base_url in base_urls:
        url = f"{base_url.rstrip('/')}/api/v1/accounts/{cfg['account_id']}/contacts"
        headers = get_chatwoot_headers(token)
        params = {"page": page}

        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("payload", [])
        except Exception as e:
            logger.debug(f"Fallo al listar contactos de Chatwoot ({base_url}): {e}")
            continue

    return []

async def sync_chatwoot_contacts_to_crm() -> Dict[str, Any]:
    """Importa o actualiza todos los contactos de Chatwoot a la tabla de clientes del CRM."""
    cfg = get_chatwoot_config()
    if not cfg.get("enabled"):
        return {"success": False, "error": "La integración con Chatwoot no está habilitada."}

    page = 1
    total_imported = 0
    total_updated = 0
    processed_contacts = []

    try:
        while True:
            contacts = await list_chatwoot_contacts(page=page)
            if not contacts:
                break

            for c in contacts:
                name = (c.get("name") or "").strip()
                phone = (c.get("phone_number") or "").strip()
                email = (c.get("email") or "").strip()
                add_attr = c.get("additional_attributes") or {}
                location = add_attr.get("city") or add_attr.get("country") or ""

                if not name and not phone:
                    continue

                client_name = name if name else f"WhatsApp {phone}"
                notes = f"Contacto de Chatwoot (ID: #{c.get('id')})"
                if location:
                    notes += f" | Ubicación: {location}"
                if email:
                    notes += f" | Email: {email}"

                target_search = phone if phone else client_name
                existing = database.search_client(target_search)
                
                res_client = database.find_or_create_client(
                    name=client_name,
                    whatsapp=phone,
                    client_type="consumidor_final",
                    notes=notes
                )
                if existing:
                    total_updated += 1
                else:
                    total_imported += 1

                processed_contacts.append({
                    "name": res_client["name"],
                    "code": res_client["client_code"],
                    "whatsapp": res_client.get("whatsapp"),
                    "chatwoot_id": c.get("id"),
                    "is_new": existing is None
                })

            if len(contacts) < 15:
                break
            page += 1
            if page > 20:
                break

        return {
            "success": True,
            "imported": total_imported,
            "updated": total_updated,
            "total_processed": len(processed_contacts),
            "contacts": processed_contacts
        }
    except Exception as e:
        logger.error(f"Error sincronizando contactos de Chatwoot: {e}")
        return {"success": False, "error": str(e)}


async def update_chatwoot_contact(
    contact_id: int,
    name: Optional[str] = None,
    email: Optional[str] = None,
    phone_number: Optional[str] = None
) -> Dict[str, Any]:
    """Actualiza los datos de un contacto existente en Chatwoot (ej. cambiar nombre de pushName a nombre real)."""
    cfg = get_chatwoot_config()
    token = (cfg.get("token") or "ZRzCpt75vxkyiUC7H1otEoog").strip()
    if not cfg.get("enabled") or not token:
        return {"success": False, "error": "Chatwoot no está habilitado o falta Token."}

    payload = {}
    if name is not None:
        payload["name"] = name.strip()
    if email is not None:
        payload["email"] = email.strip()
    if phone_number is not None:
        payload["phone_number"] = phone_number.strip()

    if not payload:
        return {"success": False, "error": "No hay campos para actualizar."}

    base_urls = [cfg["url"]]
    if "chatwoot-rails" in cfg["url"]:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in cfg["url"]:
        base_urls.append("http://chatwoot-rails:3000")

    headers = get_chatwoot_headers(token)
    for base in base_urls:
        url = f"{base.rstrip('/')}/api/v1/accounts/{cfg['account_id']}/contacts/{contact_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.put(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    logger.info(f"Contacto #{contact_id} actualizado exitosamente en Chatwoot: {payload}")
                    return {"success": True, "contact": data.get("payload", {})}
                elif resp.status_code == 404:
                    return {"success": False, "error": f"Contacto #{contact_id} no encontrado en Chatwoot."}
                else:
                    logger.warning(f"Error HTTP {resp.status_code} actualizando contacto Chatwoot ({base}): {resp.text[:200]}")
        except Exception as e:
            logger.debug(f"Fallo al actualizar contacto en {base}: {e}")
            continue

    return {"success": False, "error": "No se pudo actualizar el contacto en Chatwoot."}


async def fetch_evolution_contacts() -> List[Dict[str, Any]]:
    """Consulta los contactos almacenados en la agenda de WhatsApp a través de Evolution API."""
    config = get_evolution_config()
    headers = get_headers(config["api_key"])

    endpoints = [
        ("POST", f"{config['api_url']}/chat/findContacts/{config['instance_name']}", {}),
        ("GET", f"{config['api_url']}/chat/findContacts/{config['instance_name']}", None),
        ("POST", f"{config['api_url']}/contact/find/{config['instance_name']}", {}),
        ("GET", f"{config['api_url']}/contact/find/{config['instance_name']}", None),
    ]

    for method, url, body in endpoints:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if method == "POST":
                    resp = await client.post(url, headers=headers, json=body or {})
                else:
                    resp = await client.get(url, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        valid_items = [x for x in data if isinstance(x, dict)]
                        if valid_items:
                            logger.info(f"Se obtuvieron {len(valid_items)} contactos de WhatsApp vía Evolution API ({url}).")
                            return valid_items
                    elif isinstance(data, dict):
                        for key in ("contacts", "payload", "data"):
                            if key in data and isinstance(data[key], list):
                                valid_items = [x for x in data[key] if isinstance(x, dict)]
                                if valid_items:
                                    logger.info(f"Se obtuvieron {len(valid_items)} contactos de WhatsApp vía Evolution API ({url}).")
                                    return valid_items
        except Exception as e:
            logger.debug(f"Endpoint Evolution {url} no disponible: {e}")
            continue

    return []


async def sync_whatsapp_names_to_chatwoot() -> Dict[str, Any]:
    """Sincroniza los nombres reales de la agenda de WhatsApp y del CRM hacia los contactos de Chatwoot.
    Reemplaza nombres que provienen del 'pushName' (ej: 'Samu☠️', '😴😴') por el nombre real de agenda (ej: 'Samuel Martinez', 'Lautaro Yahari').
    """
    try:
        cfg = get_chatwoot_config()
        if not cfg.get("enabled"):
            return {"success": False, "error": "Chatwoot no está habilitado."}

        # 1. Obtener contactos de la agenda de WhatsApp (Evolution API)
        evo_name_by_phone: Dict[str, str] = {}
        evo_by_last8: Dict[str, str] = {}
        try:
            evo_contacts = await fetch_evolution_contacts()
            for ec in evo_contacts:
                if not isinstance(ec, dict):
                    continue
                raw_ident = str(ec.get("remoteJid") or ec.get("jid") or ec.get("id") or ec.get("number") or "")
                if "@g.us" in raw_ident:
                    continue  # Ignorar grupos
                raw_num = raw_ident.split("@")[0]
                num = re.sub(r'[^0-9]', '', raw_num)
                if not num or len(num) < 8 or len(num) > 16:
                    continue

                # Prioridad para el nombre: name (agenda) > verifiedName > pushName (si es texto legible)
                agenda_name = str(ec.get("name") or ec.get("verifiedName") or "").strip()
                if not agenda_name:
                    p_name = str(ec.get("pushName") or "").strip()
                    if p_name and re.search(r'[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}', p_name):
                        agenda_name = p_name

                if not agenda_name or agenda_name == num:
                    continue

                # Mapear variaciones
                evo_name_by_phone[num] = agenda_name
                if num.startswith("549") and len(num) >= 12:
                    nat10 = num[3:]
                    evo_name_by_phone[nat10] = agenda_name
                    evo_name_by_phone["54" + nat10] = agenda_name
                elif num.startswith("54") and len(num) >= 11:
                    nat10 = num[2:]
                    evo_name_by_phone[nat10] = agenda_name
                    evo_name_by_phone["549" + nat10] = agenda_name

                if len(num) >= 8:
                    evo_by_last8[num[-8:]] = agenda_name

            logger.info(f"Cargados {len(evo_name_by_phone)} índices telefónicos de agenda WhatsApp ({len(evo_by_last8)} sufijos únicos).")
        except Exception as e:
            logger.warning(f"No se pudieron cargar contactos de Evolution API: {e}")

        # 2. Obtener clientes del CRM local
        crm_name_by_phone: Dict[str, str] = {}
        crm_by_last8: Dict[str, str] = {}
        try:
            crm_clients = database.list_all_clients()
            for cl in crm_clients:
                if not isinstance(cl, dict):
                    continue
                cl_phone = database.clean_whatsapp_phone(cl.get("whatsapp", ""))
                cl_name = str(cl.get("name") or "").strip()
                if cl_phone and cl_name and not cl_name.lower().startswith("whatsapp") and len(cl_phone) >= 8:
                    crm_name_by_phone[cl_phone] = cl_name
                    if cl_phone.startswith("549") and len(cl_phone) >= 12:
                        nat10 = cl_phone[3:]
                        crm_name_by_phone[nat10] = cl_name
                        crm_name_by_phone["54" + nat10] = cl_name
                    elif cl_phone.startswith("54") and len(cl_phone) >= 11:
                        nat10 = cl_phone[2:]
                        crm_name_by_phone[nat10] = cl_name
                        crm_name_by_phone["549" + nat10] = cl_name
                    if len(cl_phone) >= 8:
                        crm_by_last8[cl_phone[-8:]] = cl_name
        except Exception as e:
            logger.warning(f"No se pudieron cargar clientes del CRM: {e}")

        # 3. Recorrer contactos en Chatwoot
        page = 1
        total_checked = 0
        total_updated = 0
        updated_details = []

        while True:
            cw_contacts = await list_chatwoot_contacts(page=page)
            if not cw_contacts or not isinstance(cw_contacts, list):
                break

            for cw_c in cw_contacts:
                if not isinstance(cw_c, dict):
                    continue
                total_checked += 1
                cw_id = cw_c.get("id")
                if not cw_id:
                    continue
                cw_name = str(cw_c.get("name") or "").strip()
                ident = str(cw_c.get("identifier") or "")
                cw_phone_raw = str(cw_c.get("phone_number") or "")

                # Omitir grupos de WhatsApp (@g.us o con etiqueta de grupo)
                if "@g.us" in ident or "@g.us" in cw_phone_raw or "(group)" in cw_name.lower():
                    continue

                if not cw_phone_raw:
                    if "@s.whatsapp.net" in ident:
                        cw_phone_raw = ident.split("@")[0]
                    else:
                        continue

                clean_p = re.sub(r'[^0-9]', '', cw_phone_raw)
                # Un número válido de WhatsApp tiene entre 8 y 16 dígitos
                if not clean_p or len(clean_p) < 8 or len(clean_p) > 16:
                    continue

                target_name = None
                source = ""

                # Prioridad 1: Agenda de WhatsApp (Evolution API)
                if clean_p in evo_name_by_phone:
                    target_name = evo_name_by_phone[clean_p]
                    source = "Agenda WhatsApp (Directo)"
                elif clean_p.startswith("549") and ("54" + clean_p[3:]) in evo_name_by_phone:
                    target_name = evo_name_by_phone["54" + clean_p[3:]]
                    source = "Agenda WhatsApp (54 normalizado)"
                elif clean_p.startswith("549") and clean_p[3:] in evo_name_by_phone:
                    target_name = evo_name_by_phone[clean_p[3:]]
                    source = "Agenda WhatsApp (Nacional 10d)"
                elif clean_p.startswith("54") and not clean_p.startswith("549") and ("549" + clean_p[2:]) in evo_name_by_phone:
                    target_name = evo_name_by_phone["549" + clean_p[2:]]
                    source = "Agenda WhatsApp (549 normalizado)"
                elif len(clean_p) >= 8 and clean_p[-8:] in evo_by_last8:
                    target_name = evo_by_last8[clean_p[-8:]]
                    source = "Agenda WhatsApp (Sufijo 8d)"

                # Prioridad 2: CRM Local
                if not target_name:
                    if clean_p in crm_name_by_phone:
                        target_name = crm_name_by_phone[clean_p]
                        source = "CRM (Directo)"
                    elif clean_p.startswith("549") and ("54" + clean_p[3:]) in crm_name_by_phone:
                        target_name = crm_name_by_phone["54" + clean_p[3:]]
                        source = "CRM (54 normalizado)"
                    elif clean_p.startswith("549") and clean_p[3:] in crm_name_by_phone:
                        target_name = crm_name_by_phone[clean_p[3:]]
                        source = "CRM (Nacional 10d)"
                    elif len(clean_p) >= 8 and clean_p[-8:] in crm_by_last8:
                        target_name = crm_by_last8[clean_p[-8:]]
                        source = "CRM (Sufijo 8d)"

                # Prioridad 3: Búsqueda flexible en DB
                if not target_name:
                    try:
                        c_found = database.search_client(clean_p)
                        if c_found and c_found.get("name") and not str(c_found["name"]).lower().startswith("whatsapp"):
                            target_name = str(c_found["name"]).strip()
                            source = "CRM DB"
                    except Exception:
                        pass

                # Comprobar si el nombre actual en Chatwoot difiere o si es un pushName/emoji
                is_emoji_or_special = bool(re.search(r'[\U00010000-\U0010ffff]', cw_name) or cw_name.count('?') >= 2)
                if target_name and (target_name != cw_name or is_emoji_or_special):
                    try:
                        up_res = await update_chatwoot_contact(contact_id=int(cw_id), name=target_name)
                        if up_res.get("success"):
                            total_updated += 1
                            updated_details.append({
                                "id": cw_id,
                                "phone": cw_phone_raw,
                                "old_name": cw_name,
                                "new_name": target_name,
                                "source": source
                            })
                            logger.info(f"Chatwoot #{cw_id} ({cw_phone_raw}) actualizado: '{cw_name}' -> '{target_name}' vía {source}")
                    except Exception as e:
                        logger.warning(f"Error actualizando contacto Chatwoot #{cw_id}: {e}")

            if len(cw_contacts) < 15:
                break
            page += 1
            if page > 50:
                break

        return {
            "success": True,
            "total_contacts_checked": total_checked,
            "total_updated": total_updated,
            "updated_contacts": updated_details
        }
    except Exception as e:
        logger.error(f"Error general en sync_whatsapp_names_to_chatwoot: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def html_to_chatwoot_markdown(text: str) -> str:
    """Convierte etiquetas HTML (<b>, <code>, etc.) a Markdown limpio para Chatwoot."""
    if not text:
        return ""
    t = text
    t = re.sub(r'</?(?:b|strong)>', '**', t)
    t = re.sub(r'</?code>', '`', t)
    t = re.sub(r'<pre>', '```\n', t)
    t = re.sub(r'</pre>', '\n```', t)
    t = re.sub(r'<br\s*/?>', '\n', t)
    t = re.sub(r'</?small>', '', t)
    t = re.sub(r'</?(?:i|em)>', '*', t)
    return t


async def send_chatwoot_message(conversation_id: int, content: str, private: bool = False) -> Dict[str, Any]:
    """Envía un mensaje o una nota privada a una conversación en Chatwoot."""
    cfg = get_chatwoot_config()
    token = (cfg.get("token") or "ZRzCpt75vxkyiUC7H1otEoog").strip()
    if not cfg.get("enabled") or not token:
        return {"success": False, "error": "Chatwoot no está habilitado o falta un Token de acceso válido."}

    base_urls = [cfg["url"]]
    if "chatwoot-rails" in cfg["url"]:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in cfg["url"]:
        base_urls.append("http://chatwoot-rails:3000")

    clean_content = html_to_chatwoot_markdown(content)

    payload = {
        "content": clean_content,
        "message_type": "outgoing",
        "private": private
    }

    for base_url in base_urls:
        url = f"{base_url.rstrip('/')}/api/v1/accounts/{cfg['account_id']}/conversations/{conversation_id}/messages"
        headers = get_chatwoot_headers(token)

        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    return {"success": True, "data": resp.json()}
                elif resp.status_code == 401:
                    logger.warning("Token de Chatwoot no autorizado al enviar mensaje (Error 401).")
                    break
                else:
                    logger.warning(f"Error al enviar mensaje a Chatwoot: HTTP {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            logger.debug(f"Fallo al conectar con Chatwoot ({base_url}): {e}")
            continue

    return {"success": False, "error": "No se pudo enviar el mensaje a Chatwoot."}


async def setup_chatwoot_webhook(webhook_url: str = "") -> Dict[str, Any]:
    """Registra o actualiza el webhook en Chatwoot para recibir eventos de mensajes de agentes."""
    cfg = get_chatwoot_config()
    token = (cfg.get("token") or "ZRzCpt75vxkyiUC7H1otEoog").strip()
    if not cfg.get("enabled") or not token:
        return {
            "success": False,
            "error": "Falta configurar un Token de acceso válido de Chatwoot. Configúralo primero en la pestaña de integraciones."
        }

    target_url = webhook_url.strip() or "https://mcp.joif.net/api/webhook/chatwoot"

    base_urls = [cfg["url"]]
    if "chatwoot-rails" in cfg["url"]:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in cfg["url"]:
        base_urls.append("http://chatwoot-rails:3000")

    payload = {
        "webhook": {
            "url": target_url,
            "subscriptions": ["message_created"]
        }
    }

    last_err = ""
    for base_url in base_urls:
        url = f"{base_url.rstrip('/')}/api/v1/accounts/{cfg['account_id']}/webhooks"
        headers = get_chatwoot_headers(token)

        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                # Comprobar si ya existe
                check_resp = await client.get(url, headers=headers)
                if check_resp.status_code == 200:
                    raw_data = check_resp.json()
                    webhooks_list = []
                    if isinstance(raw_data, list):
                        webhooks_list = raw_data
                    elif isinstance(raw_data, dict):
                        p = raw_data.get("payload")
                        if isinstance(p, dict):
                            webhooks_list = p.get("webhooks", [])
                        elif isinstance(p, list):
                            webhooks_list = p
                        elif "webhooks" in raw_data and isinstance(raw_data["webhooks"], list):
                            webhooks_list = raw_data["webhooks"]

                    for w in webhooks_list:
                        if isinstance(w, dict) and w.get("url") == target_url:
                            logger.info(f"Webhook ya registrado en Chatwoot: #{w.get('id')} -> {target_url}")
                            return {"success": True, "data": w, "already_exists": True}

                elif check_resp.status_code == 401:
                    return {
                        "success": False,
                        "error": "Token de Chatwoot no autorizado (Error 401). Verifica tu Token de acceso de usuario."
                    }

                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    logger.info(f"Webhook registrado en Chatwoot exitosamente -> {target_url}")
                    return {"success": True, "data": resp.json()}
                elif resp.status_code == 401:
                    return {
                        "success": False,
                        "error": "Token de Chatwoot no autorizado (Error 401). Verifica tu Token de acceso de usuario."
                    }
                else:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except Exception as e:
            last_err = str(e)
            logger.debug(f"Fallo al registrar webhook en Chatwoot ({base_url}): {e}")
            continue

    return {"success": False, "error": last_err or "No se pudo configurar el webhook en Chatwoot."}


async def setup_chatwoot_canned_responses() -> Dict[str, Any]:
    """Crea los atajos y respuestas predefinidas /nc en Chatwoot para autocompletado en el chat."""
    cfg = get_chatwoot_config()
    token = (cfg.get("token") or "").strip()
    if not cfg.get("enabled") or not token:
        return {
            "success": False,
            "error": "Falta configurar tu Token de acceso de Chatwoot. Ve a Chatwoot (chat.joif.net) -> Perfil (abajo a la izquierda) -> Configuración de perfil -> Token de acceso, pégalo en el CRM y haz clic en Guardar Chatwoot."
        }

    commands = [
        {"short_code": "nc_n_casaextra", "content": "/nc_n_casaextra"},
        {"short_code": "nc_n_full", "content": "/nc_n_full"},
        {"short_code": "nc_disney", "content": "/nc_disney"},
        {"short_code": "nc_max", "content": "/nc_max"},
        {"short_code": "nc_prime", "content": "/nc_prime"},
        {"short_code": "nc_spotify", "content": "/nc_spotify"},
        {"short_code": "nc_youtube", "content": "/nc_youtube"},
        {"short_code": "nc_paramount", "content": "/nc_paramount"},
        {"short_code": "nc_crunchyroll", "content": "/nc_crunchyroll"},
        {"short_code": "pago", "content": "/pago"},
        {"short_code": "renovar", "content": "/renovar"},
        {"short_code": "caida", "content": "/caida"},
        {"short_code": "reemplazo", "content": "/reemplazo"},
        {"short_code": "cambiar", "content": "/cambiar_"},
        {"short_code": "esperar", "content": "/esperar_"},
        {"short_code": "pagoapro", "content": "/pagoapro_"},
        {"short_code": "pagodene", "content": "/pagodene_"},
        {"short_code": "stock", "content": "/stock"},
        {"short_code": "info", "content": "/info"},
        {"short_code": "cbu", "content": "/cbu"},
        {"short_code": "catalogo", "content": "/catalogo"},
        {"short_code": "precios", "content": "/precios"},
        {"short_code": "ayuda", "content": "/ayuda"}
    ]

    base_urls = [cfg["url"]]
    if "chatwoot-rails" in cfg["url"]:
        base_urls.append("https://chat.joif.net")
    elif "chat.joif.net" in cfg["url"]:
        base_urls.append("http://chatwoot-rails:3000")

    last_error = ""

    for base_url in base_urls:
        list_url = f"{base_url.rstrip('/')}/api/v1/accounts/{cfg['account_id']}/canned_responses"
        headers = get_chatwoot_headers(token)

        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(list_url, headers=headers)
                if resp.status_code == 401:
                    return {
                        "success": False,
                        "error": "Token de Chatwoot no autorizado (Error 401). Verifica tu Token de acceso personal en Chatwoot (Perfil -> Configuración de perfil -> Token de acceso)."
                    }
                elif resp.status_code == 403:
                    return {
                        "success": False,
                        "error": f"Acceso denegado en Chatwoot (Error 403 para cuenta #{cfg['account_id']}). Verifica que tu usuario sea Administrador."
                    }
                elif resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                    continue

                existing_codes = set()
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data:
                            code = item.get("short_code", "")
                            if code:
                                existing_codes.add(code.lower())
                except Exception as e:
                    logger.debug(f"No se pudo parsear lista de canned responses: {e}")

                created = 0
                existing_count = 0
                for cmd in commands:
                    if cmd["short_code"].lower() in existing_codes:
                        existing_count += 1
                        continue

                    post_payload = {
                        "canned_response": {
                            "short_code": cmd["short_code"],
                            "content": cmd["content"]
                        },
                        "short_code": cmd["short_code"],
                        "content": cmd["content"]
                    }
                    post_resp = await client.post(list_url, headers=headers, json=post_payload)
                    if post_resp.status_code in (200, 201):
                        created += 1
                    else:
                        logger.warning(f"Error creando atajo {cmd['short_code']} en Chatwoot: HTTP {post_resp.status_code} - {post_resp.text[:120]}")

                if created > 0 or existing_count > 0:
                    return {
                        "success": True,
                        "created": created,
                        "existing": existing_count,
                        "total": len(commands)
                    }
                else:
                    return {
                        "success": False,
                        "error": f"No se pudo registrar ningún atajo en Chatwoot (0 creados). Revisa los permisos de tu cuenta en Chatwoot."
                    }
        except Exception as e:
            last_error = str(e)
            logger.debug(f"Fallo al sincronizar respuestas predefinidas en Chatwoot ({base_url}): {e}")
            continue

    return {"success": False, "error": last_error or "No se pudieron registrar las respuestas predefinidas en Chatwoot."}


async def get_media_base64(message_key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Descarga el contenido en base64 de un archivo multimedia (imagen/PDF) desde Evolution API."""
    cfg = get_evolution_config()
    url = f"{cfg['api_url']}/chat/getBase64FromMediaMessage/{cfg['instance_name']}"
    headers = get_headers(cfg["api_key"])
    payload = {
        "message": {
            "key": message_key
        },
        "convertToMp4": False
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return resp.json()
    except Exception as e:
        logger.debug(f"Error al obtener base64 de Evolution API: {e}")
    return None


# ==========================================
# GESTIÓN Y MODERACIÓN DE GRUPOS (Atlas-MD / Baileys)
# ==========================================

async def fetch_all_groups(get_participants: bool = False) -> List[Dict[str, Any]]:
    """Obtiene la lista completa de grupos en los que participa la instancia en WhatsApp.
    Implementa estrategia en cascada (Cascade Fallback) con timeouts extendidos:
    1. GET /group/fetchAllGroups/{instance}?getParticipants=false
    2. GET /group/fetchAllGroups/{instance}?getParticipants=true
    3. POST /chat/findChats/{instance} (Lectura de base de datos local de Evolution API)
    4. GET /chat/findChats/{instance}
    """
    cfg = get_evolution_config()
    api_url = cfg["api_url"]
    instance = cfg["instance_name"]
    headers = get_headers(cfg["api_key"])

    found_groups_by_jid: Dict[str, Dict[str, Any]] = {}

    def _extract_items(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for k in ("response", "groups", "data", "chats", "result", "value"):
                v = payload.get(k)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    return list(v.values())
        return []

    def _process_candidate(item: Any):
        if not isinstance(item, dict):
            return
        raw_jid = (
            item.get("id") or 
            item.get("jid") or 
            item.get("remoteJid") or 
            item.get("groupJid") or 
            item.get("chatJid") or 
            ""
        )
        if not isinstance(raw_jid, str):
            raw_jid = str(raw_jid)
        raw_jid = raw_jid.strip()

        is_grp = ("@g.us" in raw_jid) or bool(item.get("isGroup"))
        if not is_grp or not raw_jid:
            return

        if "@g.us" not in raw_jid:
            raw_jid = f"{raw_jid}@g.us"

        raw_name = (
            item.get("subject") or 
            item.get("name") or 
            item.get("pushName") or 
            item.get("notify") or 
            item.get("description") or 
            ""
        )
        if not isinstance(raw_name, str) or not raw_name.strip():
            raw_name = raw_jid

        existing = found_groups_by_jid.get(raw_jid)
        if not existing or (existing.get("subject") == raw_jid and raw_name != raw_jid):
            found_groups_by_jid[raw_jid] = {
                "id": raw_jid,
                "jid": raw_jid,
                "subject": raw_name.strip(),
                "name": raw_name.strip(),
                "participants": item.get("participants", []),
                "isGroup": True
            }

    # 1. GET /group/fetchAllGroups con getParticipants=false
    try:
        url1 = f"{api_url}/group/fetchAllGroups/{instance}?getParticipants=false"
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp1 = await client.get(url1, headers=headers)
            if resp1.status_code in (200, 201):
                items1 = _extract_items(resp1.json())
                for it in items1:
                    _process_candidate(it)
                if found_groups_by_jid:
                    logger.info(f"fetchAllGroups(false) detectó {len(found_groups_by_jid)} grupos.")
            else:
                logger.warning(f"fetchAllGroups(false) devolvió status {resp1.status_code}: {resp1.text[:200]}")
    except Exception as e:
        logger.warning(f"Excepción en fetchAllGroups(false): {e}")

    # 2. Si no hubo grupos, intentar con getParticipants=true (fuerza a Baileys a cargar grupos en memoria)
    if not found_groups_by_jid:
        try:
            url2 = f"{api_url}/group/fetchAllGroups/{instance}?getParticipants=true"
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp2 = await client.get(url2, headers=headers)
                if resp2.status_code in (200, 201):
                    items2 = _extract_items(resp2.json())
                    for it in items2:
                        _process_candidate(it)
                    if found_groups_by_jid:
                        logger.info(f"fetchAllGroups(true) detectó {len(found_groups_by_jid)} grupos.")
                else:
                    logger.warning(f"fetchAllGroups(true) devolvió status {resp2.status_code}: {resp2.text[:200]}")
        except Exception as e:
            logger.warning(f"Excepción en fetchAllGroups(true): {e}")

    # 3. POST /chat/findChats (Base de datos local PostgreSQL de Evolution API)
    if not found_groups_by_jid:
        try:
            url3 = f"{api_url}/chat/findChats/{instance}"
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp3 = await client.post(url3, headers=headers, json={})
                if resp3.status_code in (200, 201):
                    items3 = _extract_items(resp3.json())
                    for it in items3:
                        _process_candidate(it)
                    if found_groups_by_jid:
                        logger.info(f"POST findChats detectó {len(found_groups_by_jid)} grupos.")
                else:
                    logger.warning(f"POST findChats devolvió status {resp3.status_code}: {resp3.text[:200]}")
        except Exception as e:
            logger.warning(f"Excepción en POST findChats: {e}")

    # 4. GET /chat/findChats (Fallback para versiones que usan GET)
    if not found_groups_by_jid:
        try:
            url4 = f"{api_url}/chat/findChats/{instance}"
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp4 = await client.get(url4, headers=headers)
                if resp4.status_code in (200, 201):
                    items4 = _extract_items(resp4.json())
                    for it in items4:
                        _process_candidate(it)
                    if found_groups_by_jid:
                        logger.info(f"GET findChats detectó {len(found_groups_by_jid)} grupos.")
                else:
                    logger.warning(f"GET findChats devolvió status {resp4.status_code}: {resp4.text[:200]}")
        except Exception as e:
            logger.warning(f"Excepción en GET findChats: {e}")

    return list(found_groups_by_jid.values())


async def find_group_info(group_jid: str) -> Optional[Dict[str, Any]]:
    """Obtiene metadatos detallados de un grupo (participantes, administradores, descripción, foto)."""
    cfg = get_evolution_config()
    clean_jid = group_jid.strip()
    url = f"{cfg['api_url']}/group/findGroupInfos/{cfg['instance_name']}?groupJid={clean_jid}"
    headers = get_headers(cfg["api_key"])
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (200, 201):
                return resp.json()
    except Exception as e:
        logger.warning(f"Error consultando findGroupInfos para {clean_jid}: {e}")
    return None


async def find_group_info_from_invite_code(invite_code: str) -> Optional[Dict[str, Any]]:
    """Obtiene información de un grupo a partir del código o enlace de invitación de WhatsApp."""
    cfg = get_evolution_config()
    code = invite_code.strip()
    if "/" in code:
        code = code.split("/")[-1].split("?")[0].strip()

    headers = get_headers(cfg["api_key"])

    # 1. Estrategia: findGroupInfosFromInviteCode
    try:
        url1 = f"{cfg['api_url']}/group/findGroupInfosFromInviteCode/{cfg['instance_name']}?inviteCode={code}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp1 = await client.get(url1, headers=headers)
            if resp1.status_code in (200, 201):
                data = resp1.json()
                if isinstance(data, dict) and (data.get("id") or data.get("jid") or data.get("groupJid")):
                    return data
    except Exception as e:
        logger.warning(f"Error consultando findGroupInfosFromInviteCode para {code}: {e}")

    # 2. Estrategia fallback: inviteInfo
    try:
        url2 = f"{cfg['api_url']}/group/inviteInfo/{cfg['instance_name']}?inviteCode={code}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp2 = await client.get(url2, headers=headers)
            if resp2.status_code in (200, 201):
                data = resp2.json()
                if isinstance(data, dict) and (data.get("id") or data.get("jid") or data.get("groupJid")):
                    return data
    except Exception as e:
        logger.warning(f"Error consultando inviteInfo para {code}: {e}")

    return None


async def accept_group_invite_code(invite_code: str) -> Optional[Dict[str, Any]]:
    """Une la instancia de WhatsApp al grupo mediante su código de invitación."""
    cfg = get_evolution_config()
    code = invite_code.strip()
    if "/" in code:
        code = code.split("/")[-1].split("?")[0].strip()

    headers = get_headers(cfg["api_key"])
    url = f"{cfg['api_url']}/group/acceptInviteCode/{cfg['instance_name']}?inviteCode={code}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json={})
            if resp.status_code in (200, 201):
                return resp.json()
    except Exception as e:
        logger.warning(f"Error uniendo al grupo mediante inviteCode {code}: {e}")
    return None



async def get_group_invite_code(group_jid: str) -> Optional[str]:
    """Obtiene el enlace de invitación de un grupo de WhatsApp."""
    cfg = get_evolution_config()
    clean_jid = group_jid.strip()
    url = f"{cfg['api_url']}/group/inviteCode/{cfg['instance_name']}?groupJid={clean_jid}"
    headers = get_headers(cfg["api_key"])
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                code = data.get("inviteCode") or data.get("code")
                link = data.get("link") or data.get("invitationUrl")
                if link:
                    return link
                if code:
                    return f"https://chat.whatsapp.com/{code}"
    except Exception as e:
        logger.warning(f"Error consultando inviteCode para {clean_jid}: {e}")
    return None


async def update_group_setting(group_jid: str, action: str) -> Dict[str, Any]:
    """Modifica configuraciones del grupo:
    - action='announcement': Cierra el grupo (mute / solo administradores pueden enviar mensajes).
    - action='not_announcement': Abre el grupo (unmute / todos los miembros pueden enviar mensajes).
    """
    cfg = get_evolution_config()
    clean_jid = group_jid.strip()
    clean_act = action.strip().lower()
    if clean_act in ("mute", "cerrar", "close", "announcement"):
        setting_val = "announcement"
    else:
        setting_val = "not_announcement"

    url = f"{cfg['api_url']}/group/updateSetting/{cfg['instance_name']}?groupJid={clean_jid}"
    headers = get_headers(cfg["api_key"])
    payload = {"action": setting_val}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return {"success": True, "action": setting_val, "data": resp.json()}
            return {"success": False, "error": f"Evolution status {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def update_group_participant(group_jid: str, action: str, participants: List[str]) -> Dict[str, Any]:
    """Gestiona participantes en un grupo:
    - action: 'add', 'remove' (kick), 'promote', 'demote'
    - participants: lista de números telefónicos o JIDs (ej: ['5491100001111@s.whatsapp.net'])
    """
    cfg = get_evolution_config()
    clean_jid = group_jid.strip()
    clean_act = action.strip().lower()
    if clean_act in ("kick", "expulsar", "eliminar"):
        clean_act = "remove"

    formatted_parts = []
    for p in participants:
        p_clean = str(p).strip()
        if "@" not in p_clean:
            p_clean = f"{re.sub(r'[^0-9]', '', p_clean)}@s.whatsapp.net"
        formatted_parts.append(p_clean)

    url = f"{cfg['api_url']}/group/updateParticipant/{cfg['instance_name']}?groupJid={clean_jid}"
    headers = get_headers(cfg["api_key"])
    payload = {
        "action": clean_act,
        "participants": formatted_parts
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return {"success": True, "action": clean_act, "participants": formatted_parts, "data": resp.json()}
            return {"success": False, "error": f"Evolution status {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def delete_message_for_everyone(remote_jid: str, message_id: str, participant: str = "") -> Dict[str, Any]:
    """Elimina un mensaje para todos en el chat o grupo (Antilink / Moderación)."""
    cfg = get_evolution_config()
    clean_jid = remote_jid.strip()
    url = f"{cfg['api_url']}/chat/deleteMessageForEveryone/{cfg['instance_name']}"
    headers = get_headers(cfg["api_key"])
    key_dict: Dict[str, Any] = {
        "remoteJid": clean_jid,
        "fromMe": False,
        "id": message_id
    }
    if participant:
        key_dict["participant"] = participant

    payload = {
        "id": message_id,
        "key": key_dict
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return {"success": True, "deleted_id": message_id}
            return {"success": False, "error": f"Status {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_group_tagall(group_jid: str, message: str = "", sender_name: str = "") -> Dict[str, Any]:
    """Menciona a todos los integrantes de un grupo (tagall / @everyone) en WhatsApp."""
    clean_jid = group_jid.strip()
    info = await find_group_info(clean_jid)
    participants = []
    if info:
        participants_data = info.get("participants", [])
        for p in participants_data:
            p_id = p.get("id") or p.get("user") or ""
            if p_id:
                p_digits = re.sub(r'[^0-9]', '', p_id.split("@")[0])
                if p_digits:
                    participants.append(p_digits)

    sender_tag = f" <i>(por {sender_name})</i>" if sender_name else ""
    tag_lines = [f"📢 <b>AVISO GENERAL (@everyone){sender_tag}</b>\n"]
    if message:
        tag_lines.append(f"{message.strip()}\n")
    tag_lines.append("👥 <b>Integrantes:</b>")

    for p_num in participants:
        tag_lines.append(f"• @{p_num}")

    full_text = "\n".join(tag_lines)

    cfg = get_evolution_config()
    url = f"{cfg['api_url']}/message/sendText/{cfg['instance_name']}"
    headers = get_headers(cfg["api_key"])
    payload = {
        "number": clean_jid,
        "text": full_text,
        "options": {
            "delay": 1200,
            "presence": "composing",
            "linkPreview": False,
            "mentionsEveryOne": True,
            "mentioned": [f"{num}@s.whatsapp.net" for num in participants]
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return {"success": True, "tagged_count": len(participants), "data": resp.json()}
            return {"success": False, "error": f"Evolution status {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

import sys
whatsapp_client = sys.modules[__name__]




