# 🤖 Gemini Spark MCP - Alertas de Vencimiento & Telegram Bot

Servidor MCP (**Model Context Protocol**) con soporte para **Streamable HTTP / SSE** diseñado para **Gemini Spark** (`gemini.google.com/spark/apps`) y preparado para desplegarse con **Docker y Portainer** en servidores **ARM64 (Oracle Cloud)** o AMD64.

## 🚀 Versiones
* [**v1**](./v1): Primera versión completa con:
  - Servidor FastMCP + FastAPI con endpoints `/mcp` y `/`.
  - Base de datos SQLite persistente para registrar servicios, costos y fechas.
  - Tarea programada en segundo plano (*APScheduler*) para alertas automáticas 2 días antes.
  - Notificaciones enriquecidas a Telegram vía Bot API.
  - Docker Compose para despliegue directo en Portainer.

Consulta la [documentación detallada de v1](./v1/README.md) para ver la guía paso a paso de configuración.
