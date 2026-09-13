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

async def send_text_message(
    phone: str,
    text: str,
    delay_seconds: float = 2.0
) -> Dict[str, Any]:
    """Envía un mensaje de texto por WhatsApp a través de Evolution API.
    Aplica limpieza estricta de teléfono y simulación de delay para protección anti-baneo.
    """
    clean_phone = re.sub(r'[^0-9]', '', str(phone or ""))
    if not clean_phone or len(clean_phone) < 8:
        return {"success": False, "error": f"Número de teléfono inválido: '{phone}'"}

    config = get_evolution_config()
    url = f"{config['api_url']}/message/sendText/{config['instance_name']}"
    headers = get_headers(config["api_key"])

    delay_ms = int(max(delay_seconds, 1.0) * 1000)
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
            async with httpx.AsyncClient(timeout=5.0) as client:
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
                    elif isinstance(data, dict) and "contacts" in data and isinstance(data["contacts"], list):
                        return [x for x in data["contacts"] if isinstance(x, dict)]
        except Exception as e:
            logger.debug(f"Endpoint Evolution {url} no disponible: {e}")
            continue

    return []


async def sync_whatsapp_names_to_chatwoot() -> Dict[str, Any]:
    """Sincroniza los nombres reales de la agenda de WhatsApp y del CRM hacia los contactos de Chatwoot.
    Reemplaza nombres que provienen del 'pushName' (ej: 'Samu☠️') por el nombre real de agenda (ej: 'Samuel Martinez').
    """
    try:
        cfg = get_chatwoot_config()
        if not cfg.get("enabled"):
            return {"success": False, "error": "Chatwoot no está habilitado."}

        # 1. Obtener contactos de la agenda de WhatsApp (Evolution API)
        evo_name_by_phone: Dict[str, str] = {}
        try:
            evo_contacts = await fetch_evolution_contacts()
            for ec in evo_contacts:
                if not isinstance(ec, dict):
                    continue
                num = re.sub(r'[^0-9]', '', str(ec.get("number") or ec.get("id") or ""))
                agenda_name = str(ec.get("name") or "").strip()
                if agenda_name and agenda_name != num:
                    if len(num) >= 8:
                        evo_name_by_phone[num] = agenda_name
                        if num.startswith("549") and len(num) > 10:
                            evo_name_by_phone[num[3:]] = agenda_name
                            evo_name_by_phone["54" + num[3:]] = agenda_name
        except Exception as e:
            logger.warning(f"No se pudieron cargar contactos de Evolution API: {e}")

        # 2. Obtener clientes del CRM local
        crm_name_by_phone: Dict[str, str] = {}
        try:
            crm_clients = database.list_all_clients()
            for cl in crm_clients:
                if not isinstance(cl, dict):
                    continue
                cl_phone = database.clean_whatsapp_phone(cl.get("whatsapp", ""))
                cl_name = str(cl.get("name") or "").strip()
                if cl_phone and cl_name and not cl_name.lower().startswith("whatsapp"):
                    crm_name_by_phone[cl_phone] = cl_name
                    if cl_phone.startswith("549") and len(cl_phone) > 10:
                        crm_name_by_phone[cl_phone[3:]] = cl_name
                        crm_name_by_phone["54" + cl_phone[3:]] = cl_name
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
                cw_phone_raw = str(cw_c.get("phone_number") or cw_c.get("identifier") or "")
                clean_p = re.sub(r'[^0-9]', '', cw_phone_raw)

                if not clean_p or len(clean_p) < 8:
                    continue

                target_name = None
                source = ""

                # Prioridad 1: Agenda de WhatsApp (Evolution)
                for k, v in evo_name_by_phone.items():
                    if k in clean_p or clean_p in k:
                        target_name = v
                        source = "Agenda WhatsApp"
                        break

                # Prioridad 2: CRM Local
                if not target_name:
                    for k, v in crm_name_by_phone.items():
                        if k in clean_p or clean_p in k:
                            target_name = v
                            source = "CRM"
                            break

                # Prioridad 3: Búsqueda flexible en CRM
                if not target_name:
                    try:
                        c_found = database.search_client(clean_p)
                        if c_found and c_found.get("name") and not str(c_found["name"]).lower().startswith("whatsapp"):
                            target_name = str(c_found["name"]).strip()
                            source = "CRM DB"
                    except Exception:
                        pass

                if target_name and target_name != cw_name:
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
                    except Exception as e:
                        logger.warning(f"Error actualizando contacto Chatwoot #{cw_id}: {e}")

            if len(cw_contacts) < 15:
                break
            page += 1
            if page > 20:
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
    token = (cfg.get("token") or "").strip()
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
                    existing = check_resp.json().get("payload", [])
                    for w in existing:
                        if w.get("url") == target_url:
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
        {"short_code": "stock", "content": "/stock"},
        {"short_code": "info", "content": "/info"},
        {"short_code": "cbu", "content": "/cbu"},
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



