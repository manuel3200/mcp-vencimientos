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
    "Banco Patagonia", "Itaú", "ICBC", "Banco Credicoop", "Banco Hipotecario",
    "Openbank", "Lemon", "Lemon Cash", "Belo", "MODO", "Personal Pay", "Prex", "AstroPay"
]


def extract_text_from_pdf(pdf_bytes: bytes) -> Tuple[str, int]:
    """Extrae el texto de un documento PDF utilizando pypdf o escaneo directo de streams.
    Retorna (texto_extraido, total_paginas).
    Rechaza automáticamente archivos de más de 4MB o con más de 3 páginas (libros/manuales).
    """
    if not pdf_bytes:
        return "", 0

    # Rechazar archivos de tamaño excesivo (> 4 MB). Un comprobante de pago pesa menos de 500 KB.
    if len(pdf_bytes) > 4 * 1024 * 1024:
        logger.info(f"PDF rechazado por tamaño excesivo ({len(pdf_bytes) / 1024 / 1024:.1f} MB > 4 MB). No es comprobante bancario.")
        return "", 999

    # 1. Intentar con pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        if num_pages > 3:
            logger.info(f"PDF rechazado: tiene {num_pages} páginas (> 3). No es comprobante de pago.")
            return "", num_pages

        pages_text = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(t)
        if pages_text:
            return "\n".join(pages_text), num_pages
    except Exception as e:
        logger.debug(f"pypdf no pudo extraer texto: {e}")

    # 2. Fallback: Extracción por expresiones regulares sobre streams de texto PDF
    try:
        raw = pdf_bytes.decode("latin-1", errors="ignore")
        matches = re.findall(r'\(([^)]{2,100})\)', raw)
        clean_chunks = [m.strip() for m in matches if any(c.isalnum() for c in m)]
        if clean_chunks:
            return " ".join(clean_chunks), 1
    except Exception as e:
        logger.debug(f"Fallback regex PDF extraction error: {e}")

    return "", 1


def parse_transfer_receipt_text(text: str) -> Dict[str, Any]:
    """Analiza texto plano de un comprobante y extrae monto, banco, fecha y operación.
    Verifica rigurosamente que sea un comprobante financiero legítimo y no apuntes, libros ni chats casuales.
    """
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
    t_lower = t.lower()

    # 1. Filtro negativo: Si contiene vocabulario estrictamente académico/editorial/no financiero
    negative_signals = [
        "psicologia", "psicología", "psicometria", "psicometría", "facultad",
        "universidad", "catedra", "cátedra", "materia", "alumno", "profesor",
        "profesora", "profe", "bibliografia", "bibliografía", "editorial", "capitulo",
        "capítulo", "trabajo practico", "trabajo práctico", "evaluacion", "evaluación"
    ]
    has_negative = any(ns in t_lower for ns in negative_signals)

    # 2. Frases fuertes de comprobante bancario
    strong_receipt_phrases = [
        "comprobante de transferencia", "transferencia exitosa", "enviaste dinero",
        "transferiste a", "transferiste el", "pago realizado", "pago exitoso",
        "constancia de transferencia", "datos de la transferencia",
        "detalle de la transferencia", "detalle de la operacion", "detalle de la operación",
        "dinero enviado", "comprobante de pago", "operación exitosa", "operacion exitosa",
        "recibo de pago", "ticket de pago", "transferencia recibida", "pago acreditado"
    ]
    has_strong_phrase = any(sp in t_lower for sp in strong_receipt_phrases)

    # 3. Detectar Banco o Billetera
    for bank in KNOWN_BANKS:
        if bank.lower() in t_lower:
            res["bank"] = bank
            break
    if not res["bank"] and ("mp" in t_lower or "mercadopago" in t_lower or "mercado pago" in t_lower):
        res["bank"] = "Mercado Pago"

    # 4. Detectar Monto en Pesos
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
                if 500 <= num <= 500000:
                    res["amount"] = num
                    res["amount_formatted"] = f"${int(num):,}".replace(",", ".")
                    break
            except Exception:
                continue

    # 5. Detectar Número de Operación / Código
    op_match = re.search(r'(?:operaci[oó]n|comprobante|transacci[oó]n|nro|c[oó]digo)[\s:#.]*([0-9A-Za-z]{6,20})', t, re.IGNORECASE)
    if op_match:
        res["operation_id"] = op_match.group(1)

    # 6. Detectar Datos Bancarios (CVU, CBU, Alias, Titular, Coelsa)
    banking_fields = ["cvu", "cbu", "alias", "coelsa", "cuit", "cuil", "titular", "destinatario", "motivo"]
    banking_matches = sum(1 for bf in banking_fields if bf in t_lower)

    # 7. Detectar Fecha
    date_match = re.search(r'\b([0-3]?[0-9][/-][0-1]?[0-9][/-]20[2-3][0-9])\b', t)
    if date_match:
        res["date"] = date_match.group(1)

    # REGLA DE VALIDACIÓN ESTRICTA:
    if not has_negative:
        if has_strong_phrase and (res["amount"] or res["bank"] or res["operation_id"]):
            res["is_receipt"] = True
        elif res["bank"] and res["amount"] and (banking_matches >= 1 or res["operation_id"]):
            res["is_receipt"] = True
        elif res["amount"] and res["operation_id"] and banking_matches >= 2:
            res["is_receipt"] = True
    else:
        # Si tenía palabras académicas, SOLO considerarlo si tiene tanto frase fuerte, como banco Y monto
        if has_strong_phrase and res["bank"] and res["amount"] and res["operation_id"]:
            res["is_receipt"] = True

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
                {
                    "text": (
                        "Eres un clasificador experto de comprobantes de pago y transferencias bancarias en Argentina.\n"
                        "Determina si la imagen corresponde a un COMPROBANTE DE PAGO O TRANSFERENCIA FINANCIERA REAL "
                        "(ej: captura de Mercado Pago, Cuenta DNI, Ualá, Banco Galicia, Santander, BBVA, Macro, Naranja X, etc.).\n\n"
                        "IMPORTANTE:\n"
                        "- Si la imagen es una foto personal, un meme, una foto de texto, apuntes universitarios, un libro, "
                        "un documento de estudio, una foto de una persona, paisaje o producto ajeno, "
                        "debes responder estrictamente con is_receipt: false.\n"
                        "- Responde únicamente en formato JSON con la siguiente estructura:\n"
                        "{\n"
                        '  "is_receipt": true/false,\n'
                        '  "bank": string o null,\n'
                        '  "amount": float o null,\n'
                        '  "operation_id": string o null,\n'
                        '  "date": string o null\n'
                        "}"
                    )
                },
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
                    try:
                        parsed["amount_formatted"] = f"${int(float(parsed['amount'])):,}".replace(",", ".")
                    except Exception:
                        parsed["amount_formatted"] = f"${parsed['amount']}"
                return parsed
    except Exception as e:
        logger.debug(f"Gemini Vision falló al analizar comprobante: {e}")

    return None
