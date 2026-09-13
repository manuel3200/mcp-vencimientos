import os
import re
import logging
from typing import Dict, Any, Optional
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
        "enabled": True,
        "url": webhook_url,
        "webhookByEvents": False,
        "events": ["MESSAGES_UPSERT"]
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
