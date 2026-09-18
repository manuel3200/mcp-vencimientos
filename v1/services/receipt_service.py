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
    "Mercado Pago", "MercadoPago", "MP", "Banco Galicia", "Galicia", "Santander", "BBVA", "Banco Macro", "Macro",
    "Banco Nacion", "Banco Nación", "BNA", "Brubank", "Ualá", "Uala", "Cuenta DNI",
    "Naranja X", "NaranjaX", "NX", "Reba", "Banco Provincia", "BAPRO", "Banco Ciudad", "Supervielle",
    "Banco Patagonia", "Itaú", "Itau", "ICBC", "Banco Credicoop", "Credicoop", "Banco Hipotecario",
    "Openbank", "Lemon", "Lemon Cash", "Belo", "MODO", "Personal Pay", "PersonalPay", "Prex", "AstroPay",
    "NBCH", "NBCH24", "NBCH 24", "Nuevo Banco del Chaco", "Banco del Chaco", "Onda", "Onda Siempre",
    "Banco Formosa", "Banco de Formosa", "Bancor", "Banco de Córdoba", "Banco de Cordoba", "Banco Entre Rios",
    "Banco San Juan", "Banco Santa Cruz", "Banco Santa Fe", "Banco Comafi", "Comafi"
]


def extract_text_from_image(image_bytes: bytes) -> str:
    """Extrae texto de una imagen utilizando pytesseract si está disponible."""
    if not image_bytes:
        return ""
    try:
        from PIL import Image
        import pytesseract

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Intentar con español, luego con idioma por defecto
        try:
            text = pytesseract.image_to_string(img, lang="spa")
        except Exception:
            text = pytesseract.image_to_string(img)

        return (text or "").strip()
    except Exception as e:
        logger.debug(f"OCR local (pytesseract) no pudo procesar imagen: {e}")
        return ""


def extract_text_from_pdf(pdf_bytes: bytes) -> Tuple[str, int, Optional[bytes]]:
    """Extrae el texto de un documento PDF utilizando pypdf o escaneo directo de streams.
    Si el PDF es una exportación de imagen bancaria sin capa de texto digital (ej. NBCH 24, Brubank, Personal Pay),
    extrae la imagen interna y realiza OCR con Tesseract, además de retornar los bytes de la imagen primaria.
    Retorna (texto_extraido, total_paginas, primary_image_bytes).
    Rechaza automáticamente archivos de más de 4MB o con más de 3 páginas (libros/manuales).
    """
    if not pdf_bytes:
        return "", 0, None

    # Rechazar archivos de tamaño excesivo (> 4 MB). Un comprobante de pago pesa menos de 500 KB.
    if len(pdf_bytes) > 4 * 1024 * 1024:
        logger.info(f"PDF rechazado por tamaño excesivo ({len(pdf_bytes) / 1024 / 1024:.1f} MB > 4 MB). No es comprobante bancario.")
        return "", 999, None

    primary_img: Optional[bytes] = None

    # 1. Intentar con pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        if num_pages > 3:
            logger.info(f"PDF rechazado: tiene {num_pages} páginas (> 3). No es comprobante de pago.")
            return "", num_pages, None

        pages_text = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(t)
            else:
                # Extraer imagen incrustada (comprobantes bancarios exportados como imagen única)
                try:
                    if hasattr(page, "images") and page.images:
                        for img_obj in page.images:
                            if not primary_img:
                                primary_img = img_obj.data
                            ocr_t = extract_text_from_image(img_obj.data)
                            if ocr_t:
                                pages_text.append(ocr_t)
                except Exception as img_err:
                    logger.debug(f"Error al extraer imágenes de página PDF: {img_err}")

        if pages_text:
            return "\n".join(pages_text), num_pages, primary_img
    except Exception as e:
        logger.debug(f"pypdf no pudo extraer texto: {e}")

    # 2. Fallback: Extracción por expresiones regulares sobre streams de texto PDF
    try:
        raw = pdf_bytes.decode("latin-1", errors="ignore")
        matches = re.findall(r'\(([^)]{2,100})\)', raw)
        clean_chunks = [m.strip() for m in matches if any(c.isalnum() for c in m)]
        if clean_chunks:
            return " ".join(clean_chunks), 1, primary_img
    except Exception as e:
        logger.debug(f"Fallback regex PDF extraction error: {e}")

    return "", 1, primary_img


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
        "recipient": None,
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
        "capítulo", "trabajo practico", "trabajo práctico", "evaluacion", "evaluación",
        "carrera", "parcial", "final", "resumen de lectura"
    ]
    has_negative = any(ns in t_lower for ns in negative_signals)

    # 2. Detección de beneficiario / destinatario oficial de la cuenta (ANCLA de certeza máxima)
    recipient_patterns = [
        "juan manuel ortiz", "juan manuel", "ortiz juan manuel", "ortiz juan",
        "0000003100098090274687", "20-42185991-5", "20421859915"
    ]
    recipient_matched = any(rp in t_lower for rp in recipient_patterns)
    if recipient_matched:
        res["recipient"] = "Juan Manuel Ortiz"

    # 3. Frases fuertes de comprobante bancario
    strong_receipt_phrases = [
        "comprobante de transferencia", "transferencia exitosa", "enviaste dinero",
        "transferiste a", "transferiste el", "transferiste", "pago realizado", "pago exitoso",
        "constancia de transferencia", "datos de la transferencia",
        "detalle de la transferencia", "detalle de la operacion", "detalle de la operación",
        "detalle de transferencia", "dinero enviado", "comprobante de pago", "operación exitosa", "operacion exitosa",
        "recibo de pago", "ticket de pago", "transferencia recibida", "pago acreditado",
        "transferencia enviada", "envío exitoso", "envio exitoso",
        "destino", "destinatario", "cuenta de destino", "coelsa", "id de transferencia",
        "onda siempre", "nbch24", "nuevo banco del chaco", "personal pay", "naranja x"
    ]
    has_strong_phrase = any(sp in t_lower for sp in strong_receipt_phrases)

    # 4. Detectar Banco o Billetera
    for bank in KNOWN_BANKS:
        if bank.lower() in t_lower:
            res["bank"] = bank
            break
    if not res["bank"]:
        if "mp" in t_lower or "mercadopago" in t_lower or "mercado pago" in t_lower:
            res["bank"] = "Mercado Pago"
        elif "nbch" in t_lower or "chaco" in t_lower:
            res["bank"] = "NBCH 24"
        elif "onda" in t_lower or "formosa" in t_lower:
            res["bank"] = "Onda Siempre"
        elif "personal" in t_lower or "ppay" in t_lower:
            res["bank"] = "Personal Pay"
        elif "naranja" in t_lower or "nx" in t_lower:
            res["bank"] = "Naranja X"
        elif "brubank" in t_lower:
            res["bank"] = "Brubank"

    # 5. Detectar Monto en Pesos
    amount_patterns = [
        r'(?:monto|importe|total|transferiste|enviaste|pagaste|transferencia por|envío por|pago de)\s*[:\$]?\s*\$?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)',
        r'\$\s*([0-9]{3,6}(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{3,6})\b',
        r'(?:ars|pesos)\s*([0-9]{1,3}(?:\.[0-9]{3})*)'
    ]

    for pat in amount_patterns:
        for m in re.finditer(pat, t, re.IGNORECASE):
            val_raw = m.group(1).strip()
            if "." in val_raw and "," in val_raw:
                val_clean = val_raw.replace(".", "").replace(",", ".")
            elif "." in val_raw:
                parts = val_raw.split(".")
                if len(parts[-1]) == 3:
                    val_clean = val_raw.replace(".", "")
                else:
                    val_clean = val_raw
            elif "," in val_raw:
                parts = val_raw.split(",")
                if len(parts[-1]) == 2:
                    val_clean = val_raw.replace(",", ".")
                elif len(parts[-1]) == 3:
                    val_clean = val_raw.replace(",", "")
                else:
                    val_clean = val_raw.replace(",", ".")
            else:
                val_clean = val_raw

            try:
                num = float(val_clean)
                if 300 <= num <= 2000000:
                    res["amount"] = num
                    res["amount_formatted"] = f"${int(num):,}".replace(",", ".")
                    break
            except Exception:
                continue
        if res["amount"]:
            break

    # 6. Detectar Número de Operación / Código
    op_match = re.search(r'(?:operaci[oó]n|comprobante|transacci[oó]n|nro|c[oó]digo|control|referencia|coelsa|id)[\s:#.]*([0-9A-Za-z\-]{6,30})', t, re.IGNORECASE)
    if op_match:
        res["operation_id"] = op_match.group(1).strip("-#:")
    else:
        coelsa_match = re.search(r'\b([0-9]{12,22})\b', t)
        if coelsa_match:
            res["operation_id"] = coelsa_match.group(1)

    # 7. Detectar Datos Bancarios (CVU, CBU, Alias, Titular, Coelsa)
    banking_fields = ["cvu", "cbu", "alias", "coelsa", "cuit", "cuil", "titular", "destinatario", "motivo", "cuenta", "billetera"]
    banking_matches = sum(1 for bf in banking_fields if bf in t_lower)

    # 8. Detectar Fecha
    date_match = re.search(r'\b([0-3]?[0-9][/-][0-1]?[0-9][/-]20[2-3][0-9])\b', t)
    if date_match:
        res["date"] = date_match.group(1)
    else:
        date_match_words = re.search(r'\b([0-3]?[0-9]\s+de\s+[a-zA-ZáéíóúÁÉÍÓÚ]+\s+(?:de\s+)?20[2-3][0-9])\b', t)
        if date_match_words:
            res["date"] = date_match_words.group(1)

    # REGLA DE VALIDACIÓN:
    if not has_negative:
        if recipient_matched:
            # Si el destinatario es Juan Manuel Ortiz o su CVU/CUIL, es comprobante confirmado
            res["is_receipt"] = True
        elif has_strong_phrase and (res["amount"] or res["bank"] or res["operation_id"]):
            res["is_receipt"] = True
        elif res["bank"] and res["amount"] and (banking_matches >= 1 or res["operation_id"]):
            res["is_receipt"] = True
        elif res["amount"] and res["operation_id"] and banking_matches >= 1:
            res["is_receipt"] = True
        elif res["bank"] and banking_matches >= 2:
            res["is_receipt"] = True
    else:
        if (recipient_matched or has_strong_phrase) and res["bank"] and res["amount"]:
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
        try:
            import database
            wa_sett = database.get_whatsapp_api_settings()
            api_key = wa_sett.get("gemini_api_key", "").strip()
        except Exception as e:
            logger.debug(f"No se pudo consultar gemini_api_key desde BD: {e}")

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
                        "Eres un clasificador y extractor experto de comprobantes de pago y transferencias bancarias en Argentina.\n"
                        "Analiza la imagen adjunta con atención y precisión.\n"
                        "Bancos y billeteras frecuentes: Mercado Pago, Personal Pay, Naranja X, Brubank, Onda Siempre (Banco Formosa), "
                        "NBCH 24 (Nuevo Banco del Chaco), Banco Galicia, Santander, BBVA, Banco Nación, Macro, Ualá, Cuenta DNI, MODO, Lemon, etc.\n"
                        "El titular destinatario habitual de esta cuenta es Juan Manuel Ortiz (CVU: 0000003100098090274687, CUIL: 20-42185991-5).\n\n"
                        "REGLAS CRÍTICAS:\n"
                        "1. Si la imagen es una captura de pantalla, foto de pantalla de celular (incluso con reflejos, polvo o en modo oscuro), "
                        "o ticket de transferencia de dinero, pon is_receipt: true y extrae amount, bank, operation_id y date.\n"
                        "2. Si es una foto de una persona, selfie, paisaje, meme, apuntes de estudio, libros o documento académico, "
                        "debes responder con is_receipt: false.\n"
                        "3. Responde ÚNICAMENTE en formato JSON con la siguiente estructura exacta:\n"
                        "{\n"
                        '  "is_receipt": true,\n'
                        '  "bank": "Nombre del banco o billetera (ej: Personal Pay, Naranja X, NBCH 24, Brubank, Onda Siempre, Mercado Pago)",\n'
                        '  "amount": 15500.0,\n'
                        '  "operation_id": "código o número de operación",\n'
                        '  "date": "fecha del pago",\n'
                        '  "recipient": "nombre o datos del destinatario"\n'
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
        async with httpx.AsyncClient(timeout=18.0) as client:
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
                parts = []
                if parsed.get("bank"):
                    parts.append(f"Banco: {parsed['bank']}")
                if parsed.get("amount_formatted"):
                    parts.append(f"Monto: {parsed['amount_formatted']}")
                if parsed.get("operation_id"):
                    parts.append(f"Op: #{parsed['operation_id']}")
                parsed["summary"] = " | ".join(parts) if parts else ""
                return parsed
            else:
                logger.warning(f"Gemini API retornó código HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.debug(f"Gemini Vision falló al analizar comprobante: {e}")

    return None
