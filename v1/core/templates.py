import os
from typing import Dict, Any

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
_TEMPLATE_CACHE: Dict[str, str] = {}

def render_template(template_name: str, context: Dict[str, Any], use_cache: bool = True) -> str:
    """Carga y renderiza una plantilla HTML reemplazando etiquetas {{TAG}} o {TAG} de forma segura."""
    if use_cache and template_name in _TEMPLATE_CACHE:
        content = _TEMPLATE_CACHE[template_name]
    else:
        file_path = os.path.join(TEMPLATES_DIR, template_name)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if use_cache:
            _TEMPLATE_CACHE[template_name] = content

    for key, val in context.items():
        str_val = str(val) if val is not None else ""
        content = content.replace(f"{{{{{key}}}}}", str_val).replace(f"{{{key}}}", str_val)

    return content
