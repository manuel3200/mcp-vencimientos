FROM python:3.11-slim

# Metadatos
LABEL maintainer="Juan Ortiz"
LABEL description="Gemini Spark MCP Server con alertas de vencimiento por Telegram"

# Evitar que Python genere archivos .pyc y habilitar logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema mínimas necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias fijadas de Python (Q05: resolución reproducible)
COPY requirements.txt requirements.lock* ./
RUN if [ -f requirements.lock ]; then pip install --no-cache-dir -r requirements.lock; else pip install --no-cache-dir -r requirements.txt; fi

# Crear usuario y grupo sin privilegios (O03: non-root container runtime)
RUN groupadd -g 10001 app && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app

# Crear carpeta persistente para la base de datos SQLite antes del código
RUN mkdir -p /app/data

# Copiar el código fuente de la aplicación con permisos del usuario no privilegiado
COPY --chown=10001:10001 . .
RUN chown -R 10001:10001 /app

# Ejecutar bajo usuario sin privilegios (O03)
USER 10001:10001

# Puerto expuesto
EXPOSE 8000

# Comprobación de salud para Docker / Portainer
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Iniciar la aplicación
CMD ["python", "main.py"]
