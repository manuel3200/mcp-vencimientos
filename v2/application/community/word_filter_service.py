import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger("application.community.word_filter")

SCAM_PATTERNS = [
    r"(?i)\b(gana(r)?\s+dinero\s+f[aá]cil)\b",
    r"(?i)\b(duplica\s+tu\s+inversi[oó]n)\b",
    r"(?i)\b(cripto\s+gratis|bitcoin\s+gratis|usdt\s+gratis)\b",
    r"(?i)\b(generador\s+de\s+(cuentas|claves))\b",
    r"(?i)\b(recupero\s+cuentas\s+bloqueadas)\b",
    r"(?i)\b(vendo\s+m[eé]todo\s+streaming\s+gratis)\b",
    r"(?i)\b(piramidal|esquema\s+ponzi|trabaja\s+desde\s+casa\s+\$?\d+)\b",
]

SPAM_INVITE_PATTERNS = [
    r"(?i)chat\.whatsapp\.com\/[A-Za-z0-9]{15,}",
    r"(?i)t\.me\/(joinchat|\+[A-Za-z0-9_-]{10,})",
    r"(?i)(bit\.ly|tinyurl\.com|is\.gd|cutt\.ly)\/[A-Za-z0-9_-]+"
]

TOXIC_PATTERNS = [
    r"(?i)\b(estafadores|ladrones|garcas|hdp)\b"
]


def inspect_community_message(
    text: str,
    sender_phone: str = "",
    group_jid: str = ""
) -> Dict[str, Any]:
    """Analiza un mensaje enviado en un grupo comunitario para detectar fraudes, spam o enlaces no autorizados."""
    clean_text = str(text or "").strip()
    if not clean_text:
        return {
            "is_safe": True,
            "threat_level": "none",
            "detected_threats": [],
            "recommended_action": "allow"
        }

    threats = []

    # 1. Escanear patrones de estafa financiera y phishing
    for pat in SCAM_PATTERNS:
        if re.search(pat, clean_text):
            threats.append("scam_phishing")
            break

    # 2. Escanear enlaces de invitación a grupos ajenos
    for pat in SPAM_INVITE_PATTERNS:
        if re.search(pat, clean_text):
            threats.append("unauthorized_invite_link")
            break

    # 3. Escanear lenguaje tóxico o difamatorio
    for pat in TOXIC_PATTERNS:
        if re.search(pat, clean_text):
            threats.append("toxic_language")
            break

    if "scam_phishing" in threats:
        level = "high"
        action = "delete_and_warn"
    elif "unauthorized_invite_link" in threats:
        level = "high"
        action = "delete_and_warn"
    elif "toxic_language" in threats:
        level = "medium"
        action = "warn"
    else:
        level = "none"
        action = "allow"

    is_safe = len(threats) == 0

    if not is_safe:
        logger.warning(f"Amenaza comunitaria detectada de {sender_phone} en {group_jid}: {threats} (Acción: {action})")

    return {
        "is_safe": is_safe,
        "threat_level": level,
        "detected_threats": threats,
        "recommended_action": action,
        "sender_phone": sender_phone,
        "group_jid": group_jid
    }
