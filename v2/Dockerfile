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

# Copiar e instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Crear carpeta persistente para la base de datos SQLite antes del código
RUN mkdir -p /app/data

# Copiar el código fuente de la aplicación
COPY . .

# Puerto expuesto
EXPOSE 8000

# Comprobación de salud para Docker / Portainer
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Iniciar la aplicación
CMD ["python", "main.py"]
