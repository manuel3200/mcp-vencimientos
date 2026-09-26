import os
import html
from typing import Dict, Any, Set
from core.config import settings

_TEMPLATE_CACHE: Dict[str, str] = {}

# Claves que contienen fragmentos estructurales de servidor (cuyos datos dinámicos internos ya fueron escapados)
_TRUSTED_STRUCTURAL_KEYS: Set[str] = {
    "MESSAGE_HTML",
    "ERROR_HTML",
    "CONTENT_HTML",
    "USER_BLOCK",
    "LOGIN_FIELDS",
}


class SafeHTML(str):
    """Marca un fragmento HTML construido por el servidor cuyos valores interpolados ya fueron escapados."""
    pass


def render_template(
    template_name: str,
    context: Dict[str, Any],
    use_cache: bool = True,
    autoescape: bool = True,
) -> str:
    """Carga y renderiza una plantilla HTML reemplazando etiquetas {{TAG}} o {TAG} con escape contextual (V11)."""
    if use_cache and template_name in _TEMPLATE_CACHE:
        content = _TEMPLATE_CACHE[template_name]
    else:
        file_path = os.path.join(settings.TEMPLATES_DIR, template_name)
        if not os.path.exists(file_path):
            fallback = os.path.join(settings.BASE_DIR, "templates", template_name)
            if os.path.exists(fallback):
                file_path = fallback

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if use_cache:
            _TEMPLATE_CACHE[template_name] = content

    for key, val in context.items():
        raw_str = str(val) if val is not None else ""
        if autoescape and not isinstance(val, SafeHTML) and key not in _TRUSTED_STRUCTURAL_KEYS:
            safe_val = html.escape(raw_str, quote=True)
        else:
            safe_val = raw_str
        content = content.replace(f"{{{{{key}}}}}", safe_val).replace(f"{{{key}}}", safe_val)

    return content
