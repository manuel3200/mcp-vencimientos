# ⚡ Gemini Spark MCP - Alertas de Vencimiento por Telegram

Servidor **MCP (Model Context Protocol)** con transporte **Streamable HTTP / SSE**, diseñado para integrarse con **Gemini Spark** (`gemini.google.com/spark/apps`) y correr en **Docker (Portainer)** sobre arquitectura **ARM64 (Oracle Cloud Ampere)** o AMD64.

Incluye un **planificador automático en segundo plano** que revisa diariamente tus servicios y te envía alertas a **Telegram 2 días antes** de que venzan (sin necesidad de que abras Gemini para consultar).

---

## 🚀 Arquitectura y Capacidades

```
                       ┌─────────────────────────┐
                       │  Gemini Spark / Web     │
                       │  (gemini.google.com)    │
                       └────────────┬────────────┘
                                    │ Consulta / Registra herramientas
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Servidor en Oracle Cloud ARM (Docker / Portainer)                     │
│                                                                        │
│   ┌──────────────────────────────────────────────────────────────┐    │
│   │ FastMCP + FastAPI                                            │    │
│   │ • Endpoint MCP: https://tu-dominio.com/mcp                   │    │
│   │ • Dashboard Web: https://tu-dominio.com/                     │    │
│   └───────────────┬──────────────────────────────▲───────────────┘    │
│                   │                              │                     │
│                   ▼                              │                     │
│   ┌──────────────────────────────┐ ┌─────────────┴───────────────┐    │
│   │ Base de Datos SQLite         │ │ Tarea Programada (Scheduler)│    │
│   │ /app/data/services.db        │ │ Revisa todos los días a las │    │
│   │ (Volumen persistente)        │ │ 09:00 hs (configurable)     │    │
│   └──────────────────────────────┘ └─────────────┬───────────────┘    │
└──────────────────────────────────────────────────┼─────────────────────┘
                                                   │ Alerta 2 días antes
                                                   ▼
                                     ┌───────────────────────────┐
                                     │     Bot de Telegram       │
                                     │  (Mensaje directo al cel) │
                                     └───────────────────────────┘
```

---

## 🛠️ Herramientas que expone a Gemini Spark

Cuando chatees con Gemini, tendrá acceso a estas herramientas automáticas:
* **`agregar_servicio`**: Registra un servicio (*nombre*, *fecha_vencimiento*, *costo*, *recurrencia*, *categoría*, *notas*).
* **`listar_servicios`**: Muestra la lista de todos los servicios, días restantes y estado (*activo*, *por vencer*, *vencido*).
* **`proximos_vencimientos`**: Filtra los servicios que vencen en los próximos $N$ días.
* **`renovar_servicio`**: Actualiza la fecha de corte tras haber realizado el pago.
* **`eliminar_servicio`**: Elimina un servicio registrado por su ID.
* **`enviar_alerta_prueba_telegram`**: Envía un mensaje de prueba a tu chat.
* **`verificar_vencimientos_ahora`**: Dispara manualmente la comprobación y envía las alertas correspondientes.

---

## 📋 Paso 1: Crear tu Bot de Telegram (1 minuto)

1. Abre Telegram y busca al usuario oficial **`@BotFather`**.
2. Envía el comando `/newbot`.
3. Sigue las instrucciones para darle un nombre y usuario (ej. `JuanVencimientosBot`).
4. `@BotFather` te entregará el **`BOT_TOKEN`** (algo como `7123456789:AAH...`).
5. Ahora busca a **`@userinfobot`** en Telegram y dale a *Iniciar*. Te responderá con tu número de **`Id`** (este es tu `TELEGRAM_CHAT_ID`).
6. **Importante:** Envía un mensaje cualquiera (o dale `/start`) a tu nuevo bot para que tenga permiso de enviarte mensajes.

---

## 🐳 Paso 2: Despliegue en Portainer (Oracle ARM)

Como tu servidor Oracle Cloud usa **Portainer** (ej. `portainer.juanconnect.online`):

### Opción A: Desplegar como Stack desde la carpeta del servidor
1. Sube o clona la carpeta `v1` en tu servidor Oracle:
   ```bash
   scp -r v1 usuario@tu-servidor-ip:~/mcp-v1
   ```
2. O en Portainer:
   - Ve a **Stacks** -> **Add stack**.
   - Nombre: `mcp-vencimientos`.
   - Pega el contenido de `docker-compose.yml`.
   - En **Environment variables**, define:
     - `TELEGRAM_BOT_TOKEN`: El token de BotFather.
     - `TELEGRAM_CHAT_ID`: Tu ID de Telegram.
     - `DAYS_BEFORE_ALERT`: `2`
     - `ALERT_HOUR`: `9`
     - `TIMEZONE`: `America/Argentina/Buenos_Aires` (o tu zona horaria).
   - Haz clic en **Deploy the stack**.

---

## 🌐 Paso 3: Configurar Dominio y HTTPS

Gemini Spark requiere una URL segura con **HTTPS**:

Si ya usas **Nginx Proxy Manager**, **Traefik** o **Cloudflare Tunnels** con tu dominio `juanconnect.online`:
1. Crea un subdominio, por ejemplo: `mcp.juanconnect.online`.
2. Apunta el proxy hacia el contenedor en el puerto `8000`.
3. Activa el certificado SSL / HTTPS (Let's Encrypt o Cloudflare).
4. Abre `https://mcp.juanconnect.online` en tu navegador: verás el **Dashboard Web** interactivo con el estado del bot y la tabla de servicios.

---

## 🔗 Paso 4: Conectar a Gemini Spark

1. Entra en tu navegador a: **[gemini.google.com/spark/apps](https://gemini.google.com/spark/apps)**.
2. En la sección **Aplicaciones personalizadas para Spark**, pega la URL completa del endpoint MCP:
   ```text
   https://mcp.juanconnect.online/mcp
   ```
3. Haz clic en **Siguiente** y confirma los permisos.

---

## 💬 Ejemplos de uso con Gemini Spark

Una vez conectado, puedes hablarle a Gemini de manera totalmente natural:

* *"Anota que el hosting de Oracle vence el 28 de este mes, cuesta $0 y es mensual."*
* *"Agrega la suscripción a Netflix por $15 USD que vence el 15 de octubre."*
* *"¿Cuáles son los servicios que vencen en los próximos 7 días?"*
* *"Mándame un mensaje de prueba a Telegram para ver si está conectado."*
* *"Ya pagué el dominio de juanconnect, cámbiale el vencimiento al 2027-09-10."*
