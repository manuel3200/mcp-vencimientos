import os
from typing import Dict, Any
from core.config import settings

_TEMPLATE_CACHE: Dict[str, str] = {}

def render_template(template_name: str, context: Dict[str, Any], use_cache: bool = True) -> str:
    """Carga y renderiza una plantilla HTML reemplazando etiquetas {{TAG}} o {TAG} de forma segura."""
    if use_cache and template_name in _TEMPLATE_CACHE:
        content = _TEMPLATE_CACHE[template_name]
    else:
        # Intentar ruta configurada (presentation/templates) o fallback a templates
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
        str_val = str(val) if val is not None else ""
        content = content.replace(f"{{{{{key}}}}}", str_val).replace(f"{{{key}}}", str_val)

    return content
