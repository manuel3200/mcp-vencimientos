import re
import io
import os
import base64
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

import httpx

logger = logging.getLogger("receipt_service")

# Bancos y billeteras comunes en Argentina
KNOWN_BANKS = [
    "Mercado Pago", "Banco Galicia", "Santander", "BBVA", "Banco Macro",
    "Banco Nacion", "Banco Nación", "Brubank", "Ualá", "Uala", "Cuenta DNI",
    "Naranja X", "Reba", "Banco Provincia", "Banco Ciudad", "Supervielle",
    "Itaú", "ICBC", "Banco Credicoop", "Banco Hipotecario", "Openbank", "Lemon"
]


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrae el texto de un documento PDF utilizando pypdf o escaneo directo de streams."""
    if not pdf_bytes:
        return ""

    # 1. Intentar con pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(t)
        if pages_text:
            return "\n".join(pages_text)
    except Exception as e:
        logger.debug(f"pypdf no pudo extraer texto: {e}")

    # 2. Fallback: Extracción por expresiones regulares sobre streams de texto PDF
    try:
        raw = pdf_bytes.decode("latin-1", errors="ignore")
        matches = re.findall(r'\(([^)]{2,100})\)', raw)
        clean_chunks = [m.strip() for m in matches if any(c.isalnum() for c in m)]
        if clean_chunks:
            return " ".join(clean_chunks)
    except Exception as e:
        logger.debug(f"Fallback regex PDF extraction error: {e}")

    return ""


def parse_transfer_receipt_text(text: str) -> Dict[str, Any]:
    """Analiza texto plano de un comprobante y extrae monto, banco, fecha y operación."""
    res: Dict[str, Any] = {
        "is_receipt": False,
        "amount": None,
        "amount_formatted": None,
        "bank": None,
        "operation_id": None,
        "date": None,
        "summary": ""
    }

    if not text:
        return res

    t = text.replace("\r", " ")

    # 1. Detectar si parece comprobante de transferencia
    receipt_signals = [
        "comprobante", "transferencia", "transferiste", "enviaste", "pago",
        "operacion", "operación", "motivo", "cvu", "cbu", "alias",
        "dinero enviado", "destinatario", "transacción", "transaccion", "importe"
    ]
    t_lower = t.lower()
    matches_count = sum(1 for s in receipt_signals if s in t_lower)
    if matches_count >= 1:
        res["is_receipt"] = True

    # 2. Detectar Banco o Billetera
    for bank in KNOWN_BANKS:
        if bank.lower() in t_lower:
            res["bank"] = bank
            break
    if not res["bank"] and ("mp" in t_lower or "mercadopago" in t_lower):
        res["bank"] = "Mercado Pago"

    # 3. Detectar Monto en Pesos
    amount_patterns = [
        r'(?:monto|importe|total|transferiste|enviaste|pagaste)\s*[:\$]?\s*\$?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{4,6}(?:,[0-9]{2})?)',
        r'(?:ars|pesos)\s*([0-9]{1,3}(?:\.[0-9]{3})*)'
    ]

    for pat in amount_patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            val_str = m.group(1).replace(".", "").replace(",", ".")
            try:
                num = float(val_str)
                if 500 <= num <= 250000:
                    res["amount"] = num
                    res["amount_formatted"] = f"${int(num):,}".replace(",", ".")
                    break
            except Exception:
                continue

    # 4. Detectar Número de Operación / Código
    op_match = re.search(r'(?:operaci[oó]n|comprobante|transacci[oó]n|nro|c[oó]digo)[\s:#.]*([0-9A-Za-z]{6,20})', t, re.IGNORECASE)
    if op_match:
        res["operation_id"] = op_match.group(1)

    # 5. Detectar Fecha
    date_match = re.search(r'\b([0-3]?[0-9][/-][0-1]?[0-9][/-]20[2-3][0-9])\b', t)
    if date_match:
        res["date"] = date_match.group(1)

    # Resumen
    parts = []
    if res["bank"]:
        parts.append(f"Banco: {res['bank']}")
    if res["amount_formatted"]:
        parts.append(f"Monto: {res['amount_formatted']}")
    if res["operation_id"]:
        parts.append(f"Op: #{res['operation_id']}")
    res["summary"] = " | ".join(parts) if parts else ""

    return res


async def analyze_image_with_gemini(image_b64: str, mime_type: str = "image/jpeg") -> Optional[Dict[str, Any]]:
    """Analiza una imagen de comprobante bancario usando Google Gemini Vision API."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    clean_b64 = image_b64
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",")[1]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": "Analiza este comprobante de transferencia o pago en Argentina. Responde en JSON con los campos: is_receipt (bool), amount (float o null), bank (string o null), operation_id (string o null), date (string o null)."},
                {"inline_data": {"mime_type": mime_type, "data": clean_b64}}
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                text_out = data["candidates"][0]["content"]["parts"][0]["text"]
                import json
                parsed = json.loads(text_out)
                if parsed.get("amount"):
                    parsed["amount_formatted"] = f"${int(parsed['amount']):,}".replace(",", ".")
                return parsed
    except Exception as e:
        logger.debug(f"Gemini Vision falló al analizar comprobante: {e}")

    return None
