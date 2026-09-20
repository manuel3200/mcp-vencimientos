import re
import io
import os
import base64
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

import httpx

from core.rate_limiter import receipt_quota_manager

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


def compute_perceptual_hash(image_bytes: bytes) -> str:
    """Calcula el hash perceptual (dHash 64 bits) de una imagen utilizando Pillow.
    Convierte la imagen a escala de grises 'L', la redimensiona a 9x8, calcula las
    diferencias de intensidad adyacentes horizontalmente y retorna un string hexadecimal
    de 16 caracteres. Si falla o no es una imagen válida, retorna "".
    """
    if not image_bytes:
        return ""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("L")

        resample = getattr(Image, "Resampling", Image).LANCZOS if hasattr(getattr(Image, "Resampling", Image), "LANCZOS") else getattr(Image, "BILINEAR", None)
        if resample is not None:
            img = img.resize((9, 8), resample)
        else:
            img = img.resize((9, 8))

        diff_bits = 0
        for y in range(8):
            for x in range(8):
                left = img.getpixel((x, y))
                right = img.getpixel((x + 1, y))
                diff_bits = (diff_bits << 1) | (1 if left > right else 0)

        return f"{diff_bits:016x}"
    except Exception as e:
        logger.debug(f"Error calculando hash perceptual (dHash): {e}")
        return ""


def hamming_distance(h1: str, h2: str) -> int:
    """Calcula la distancia de Hamming entre dos hashes hexadecimales de 64 bits.
    Si alguno es inválido o de longitud distinta a 16 caracteres, retorna 999.
    """
    if not h1 or not h2:
        return 999
    h1_clean = str(h1).strip().lower()
    h2_clean = str(h2).strip().lower()
    if len(h1_clean) != 16 or len(h2_clean) != 16:
        return 999
    try:
        val1 = int(h1_clean, 16)
        val2 = int(h2_clean, 16)
        return bin(val1 ^ val2).count("1")
    except (ValueError, TypeError):
        return 999


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

    # 4. Detectar Banco o Billetera (Priorizando Banco Origen/Emisor sobre Banco Destino)
    origin_bank = None

    # A. Buscar específicamente en sección de "cuenta origen" / "desde"
    origin_section = re.search(r'(?:cuenta\s+origen|billetera\s+origen|desde|emisor)[\s\S]{1,150}?(?:cuenta\s+destino|hacia|para|informaci[oó]n|coelsa|$)', t_lower)
    if origin_section:
        sec_text = origin_section.group(0)
        for bank in KNOWN_BANKS:
            b_low = bank.lower()
            if b_low in sec_text or (bank == "Naranja X" and "naranjax" in sec_text):
                origin_bank = bank
                break

    # B. Si no se encontró en cuenta origen, buscar en el encabezado superior del comprobante (primeros 120 caracteres)
    if not origin_bank:
        header_chunk = t_lower[:120]
        for bank in KNOWN_BANKS:
            b_low = bank.lower()
            if b_low in header_chunk or (bank == "Naranja X" and "naranjax" in header_chunk):
                origin_bank = bank
                break

    # C. Si no se encontró en origen ni encabezado, buscar en todo el texto (evitando cuenta destino si hay otra opción)
    if origin_bank:
        res["bank"] = origin_bank
    else:
        for bank in KNOWN_BANKS:
            if bank.lower() in t_lower:
                res["bank"] = bank
                break
        if not res["bank"]:
            if "naranja" in t_lower or "nx" in t_lower:
                res["bank"] = "Naranja X"
            elif "mp" in t_lower or "mercadopago" in t_lower or "mercado pago" in t_lower:
                res["bank"] = "Mercado Pago"
            elif "nbch" in t_lower or "chaco" in t_lower:
                res["bank"] = "NBCH 24"
            elif "onda" in t_lower or "formosa" in t_lower:
                res["bank"] = "Onda Siempre"
            elif "personal" in t_lower or "ppay" in t_lower:
                res["bank"] = "Personal Pay"
            elif "brubank" in t_lower:
                res["bank"] = "Brubank"

    # 5. Detectar Monto en Pesos
    # Corrección de artefacto OCR común: '$' interpretado como '5' antes del monto (ej: 'Enviaste 58.000' -> 'Enviaste $ 8.000')
    t_clean = t
    t_clean = re.sub(r'(enviaste|transferiste|pagaste|monto|total)\s*[:\$]?\s*5\s*([1-9]\.[0-9]{3})', r'\1 $ \2', t_clean, flags=re.IGNORECASE)
    t_clean = re.sub(r'\$\s*5([1-9]\.[0-9]{3})', r'$ \1', t_clean)

    amount_patterns = [
        r'(?:monto|importe|total|transferiste|enviaste|pagaste|transferencia por|envío por|pago de)\s*[:\$]?\s*\$?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)',
        r'\$\s*([0-9]{3,6}(?:,[0-9]{2})?)',
        r'\$\s*([0-9]{3,6})\b',
        r'(?:ars|pesos)\s*([0-9]{1,3}(?:\.[0-9]{3})*)'
    ]

    for pat in amount_patterns:
        for m in re.finditer(pat, t_clean, re.IGNORECASE):
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
                    # Si detectó un valor >= 50000 generado por el trazo vertical del signo '$' confundido con '5'
                    if num >= 50000.0:
                        base_cand = num - 50000.0
                        if base_cand in (8000.0, 4500.0, 3500.0, 2500.0, 3000.0, 5000.0, 6000.0, 7000.0, 9000.0, 10000.0):
                            num = base_cand

                    res["amount"] = num
                    res["amount_formatted"] = f"${int(num):,}".replace(",", ".")
                    break
            except Exception:
                continue
        if res["amount"]:
            break

    # 6. Detectar Número de Operación / Código
    INVALID_OP_WORDS = {
        "coelsa", "codigo", "código", "operacion", "operación", "transaccion", "transacción",
        "comprobante", "referencia", "control", "transferencia", "bancaria", "informacion",
        "información", "numero", "número", "banco", "cuenta", "destino", "origen", "titular",
        "detalle", "identificador", "id"
    }

    # A. Buscar específicamente COELSA ID (10-35 caracteres alfanuméricos)
    coelsa_id_match = re.search(r'coelsa\s*(?:id)?[\s:#.]*([0-9A-Za-z]{10,35})', t, re.IGNORECASE)
    if coelsa_id_match and coelsa_id_match.group(1).lower() not in INVALID_OP_WORDS:
        res["operation_id"] = coelsa_id_match.group(1).strip("-#:")

    # B. Buscar código de transacción (UUID o alfanumérico de al menos 16 caracteres)
    if not res["operation_id"]:
        tx_code_match = re.search(r'(?:c[oó]digo\s*(?:de\s*)?transacci[oó]n|transacci[oó]n)[\s:#.]*([0-9a-fA-F\-]{16,40})', t, re.IGNORECASE)
        if tx_code_match and tx_code_match.group(1).lower() not in INVALID_OP_WORDS:
            res["operation_id"] = tx_code_match.group(1).strip("-#:")

    # C. Buscar número de comprobante / operación estándar descartando palabras reservadas
    if not res["operation_id"]:
        for m in re.finditer(r'(?:operaci[oó]n(?: de [a-zA-Z\s]+)?|comprobante|nro|control|referencia)[\s:#.]*([0-9A-Za-z\-]{6,35})', t, re.IGNORECASE):
            cand = m.group(1).strip("-#:").strip()
            if cand.lower() not in INVALID_OP_WORDS and not (cand.isalpha() and len(cand) <= 6):
                res["operation_id"] = cand
                break

    # D. Fallback número de operación puramente numérico (8 a 18 dígitos, excluyendo CBU/CVU de 22 dígitos y CUIT de 11 dígitos)
    if not res["operation_id"]:
        for num_m in re.finditer(r'\b([0-9]{8,18})\b', t):
            cand_num = num_m.group(1)
            if len(cand_num) in (11, 22) or cand_num in ("0000003100098090274687", "20421859915"):
                continue
            res["operation_id"] = cand_num
            break

    # 7. Detectar Datos Bancarios (CVU, CBU, Alias, Titular, Coelsa)
    banking_fields = ["cvu", "cbu", "alias", "coelsa", "cuit", "cuil", "titular", "destinatario", "motivo", "cuenta", "billetera"]
    banking_matches = sum(1 for bf in banking_fields if bf in t_lower)

    # 8. Detectar Fecha
    date_match = re.search(r'\b([0-3]?[0-9][/-](?:[0-1]?[0-9]|[a-zA-ZáéíóúÁÉÍÓÚ]+)[/-]20[2-3][0-9])\b', t)
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


def _parse_gemini_json(text_out: str) -> Optional[Dict[str, Any]]:
    """Extrae y parsea el objeto JSON retornado por Gemini, tolerando bloques markdown o texto circundante."""
    if not text_out:
        return None
    cleaned = text_out.strip()
    if "```" in cleaned:
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        match = re.search(r'(\{.*\})', cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception as je:
        logger.warning(f"Error parseando JSON de Gemini Vision: {je}. Raw: {text_out[:300]}")
    return None


async def analyze_image_with_gemini(image_b64: str, mime_type: str = "image/jpeg") -> Optional[Dict[str, Any]]:
    """Analiza una imagen usando Google Gemini Vision API para clasificar con precisión estricta
    si es un comprobante de pago/transferencia o si es una foto casual, producto, juguete, mascota, meme, etc.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        try:
            import database
            wa_sett = database.get_whatsapp_api_settings()
            api_key = (wa_sett.get("gemini_api_key") or "").strip()
        except Exception as e:
            logger.debug(f"No se pudo consultar gemini_api_key desde BD: {e}")

    if not api_key:
        return None

    clean_b64 = image_b64.replace("\n", "").replace("\r", "").strip()
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",")[1].strip()

    if not clean_b64:
        return None

    # Pre-filtro de decodificación y tamaño (> 8 MB)
    try:
        raw_bytes = base64.b64decode(clean_b64)
    except Exception as b64_err:
        logger.debug(f"Error decodificando base64 de imagen: {b64_err}")
        return None

    if len(raw_bytes) > 8 * 1024 * 1024:
        logger.info(f"Imagen rechazada por tamaño excesivo ({len(raw_bytes) / 1024 / 1024:.2f} MB > 8 MB).")
        return {
            "is_receipt": False,
            "bank": None,
            "amount": None,
            "amount_formatted": None,
            "operation_id": None,
            "date": None,
            "recipient": None,
            "summary": "Imagen rechazada por tamaño excesivo (> 8MB)"
        }

    # Pre-filtro de dimensiones mínimas (< 200x200)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw_bytes)) as img:
            width, height = img.size
            if width < 200 or height < 200:
                logger.info(f"Imagen rechazada por resolución insuficiente ({width}x{height} < 200x200).")
                return {
                    "is_receipt": False,
                    "bank": None,
                    "amount": None,
                    "amount_formatted": None,
                    "operation_id": None,
                    "date": None,
                    "recipient": None,
                    "summary": "Imagen rechazada por resolución insuficiente (< 200x200)"
                }
    except Exception as img_err:
        logger.debug(f"Error comprobando dimensiones de imagen: {img_err}")

    # Pre-filtro de percepción negativa (dHash contra historial no-comprobante)
    p_hash = ""
    try:
        p_hash = compute_perceptual_hash(raw_bytes)
        if p_hash and receipt_quota_manager.is_known_non_receipt(p_hash):
            logger.info(f"Descarte instantáneo por similitud con imagen no-comprobante previa (pHash: {p_hash}).")
            return {
                "is_receipt": False,
                "bank": None,
                "amount": None,
                "amount_formatted": None,
                "operation_id": None,
                "date": None,
                "recipient": None,
                "summary": "Descarte instantáneo por similitud con imagen no-comprobante previa"
            }
    except Exception as ph_err:
        logger.debug(f"Error verificando percepción negativa: {ph_err}")

    prompt_text = (
        "Eres un auditor y clasificador experto de comprobantes de pago y transferencias bancarias en Argentina.\n"
        "Analiza la imagen adjunta para determinar con precisión si es un COMPROBANTE DE PAGO BANCARIO o TRANSFERENCIA REAL.\n\n"
        "REGLA PRINCIPAL DE APROBACIÓN (is_receipt = true):\n"
        "Si la imagen contiene un comprobante, ticket digital o constancia de transferencia o pago emitido por un banco o billetera virtual "
        "(Mercado Pago, Personal Pay, Naranja X, Brubank, Onda Siempre / Banco Formosa, NBCH 24, Banco Galicia, Santander, "
        "BBVA, Banco Nación, Banco Macro, Ualá, Cuenta DNI, MODO, Lemon, etc.):\n"
        "-> DEBES responder con is_receipt: true.\n"
        "-> Extrae el monto principal transferido (amount), el banco o billetera (bank), el número o código de operación (operation_id), "
        "la fecha (date) y el destinatario (recipient).\n"
        "-> Destinatario habitual: Juan Manuel Ortiz (CVU: 0000003100098090274687, CUIL: 20-42185991-5).\n\n"
        "MUY IMPORTANTE (BANNERS Y PUBLICIDAD AL PIE DEL COMPROBANTE):\n"
        "Muchos comprobantes auténticos (especialmente de Mercado Pago) incluyen al pie de página tarjetas publicitarias, avisos o promociones "
        "(por ejemplo: 'Mago, tu asistente', 'Hacé pagos y transferencias por mensaje o audio', fotos de personas o modelos sosteniendo un celular, etc.). "
        "ESTA PUBLICIDAD NO INVALIDA EL COMPROBANTE. Si la parte superior o principal contiene el comprobante de transferencia con los datos del pago, "
        "ES UN COMPROBANTE VÁLIDO (is_receipt: true). Ignora la publicidad o fotos de personas del pie de página.\n"
        "Asegúrate de extraer el MONTO REAL de la transferencia (arriba, ej: $ 4.500) y NO el monto que pueda aparecer como ejemplo en la publicidad.\n\n"
        "CRITERIO DE RECHAZO (is_receipt = false):\n"
        "Responde is_receipt: false ÚNICAMENTE si la imagen NO es un comprobante de pago bancario, por ejemplo:\n"
        "- Fotos casuales de productos, juguetes, mercadería, artículos de bazar, packaging, cajas de juguetes, burbujeros, vasos, slime, ropa o comida.\n"
        "- Fotos familiares, selfies puras o fotos de mascotas (perros, gatos) que no tienen ninguna relación con un pago.\n"
        "- Fotos de objetos cotidianos, locales, vidrieras, paisajes o la calle.\n"
        "- Capturas de chats de WhatsApp, mensajes de voz o memes.\n"
        "- Capturas o fotos de pantallas de televisores (Smart TV) con errores de Netflix o streaming.\n"
        "- Documentos académicos, libros, manuales, apuntes o fotocopias.\n\n"
        "Responde ÚNICAMENTE un objeto JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "is_receipt": false,\n'
        '  "bank": null,\n'
        '  "amount": null,\n'
        '  "operation_id": null,\n'
        '  "date": null,\n'
        '  "recipient": null\n'
        "}\n"
        "Si es un comprobante bancario real, cambia is_receipt a true y extrae los datos numéricos y de texto correspondientes."
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": clean_b64}}
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    # Intentar con gemini-2.0-flash y fallback a gemini-1.5-flash
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates:
                        logger.warning(f"Gemini ({model_name}) retornó 0 candidatos.")
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts or "text" not in parts[0]:
                        logger.warning(f"Gemini ({model_name}) retornó respuesta sin texto.")
                        continue

                    parsed = _parse_gemini_json(parts[0]["text"])
                    if not parsed:
                        continue

                    # Si Gemini determinó que NO es un comprobante
                    if not parsed.get("is_receipt"):
                        if p_hash:
                            receipt_quota_manager.record_non_receipt(p_hash)
                        return {
                            "is_receipt": False,
                            "bank": None,
                            "amount": None,
                            "amount_formatted": None,
                            "operation_id": None,
                            "date": None,
                            "recipient": None,
                            "summary": "No es un comprobante de pago"
                        }

                    # Si ES un comprobante legítimo
                    if parsed.get("amount"):
                        try:
                            parsed["amount_formatted"] = f"${int(float(parsed['amount'])):,}".replace(",", ".")
                        except Exception:
                            parsed["amount_formatted"] = f"${parsed['amount']}"

                    if p_hash:
                        parsed["phash"] = p_hash

                    res_parts = []
                    if parsed.get("bank"):
                        res_parts.append(f"Banco: {parsed['bank']}")
                    if parsed.get("amount_formatted"):
                        res_parts.append(f"Monto: {parsed['amount_formatted']}")
                    if parsed.get("operation_id"):
                        res_parts.append(f"Op: #{parsed['operation_id']}")
                    parsed["summary"] = " | ".join(res_parts) if res_parts else "Comprobante verificado"
                    return parsed
                elif resp.status_code in (404, 400):
                    logger.warning(f"Gemini ({model_name}) HTTP {resp.status_code}, probando modelo alternativo...")
                    continue
                else:
                    logger.warning(f"Gemini API ({model_name}) retornó código HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.debug(f"Gemini Vision ({model_name}) falló al analizar comprobante: {e}")

    return None
