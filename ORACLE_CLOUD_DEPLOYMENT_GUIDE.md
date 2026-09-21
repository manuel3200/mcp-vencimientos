# 🚀 Guía de Despliegue en Oracle Cloud Always Free (Estrategia B)
## StreamVault v2 + n8n + Evolution API + MySQL HeatWave (50 GB Gratis)

Esta guía detalla paso a paso cómo desplegar la infraestructura de **StreamVault v2** en **Oracle Cloud Infrastructure (OCI)** aprovechando al 100% el nivel **Always Free (Gratis de por vida)**, aislando las bases de datos y registros pesados en **MySQL HeatWave (50 GB)** para no consumir el disco de tu máquina virtual ARM de 100 GB.

---

## 🏗️ 1. Arquitectura de Despliegue

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ORACLE CLOUD INFRASTRUCTURE (OCI)               │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │             VIRTUAL CLOUD NETWORK (VCN) - 10.0.0.0/16            │  │
│  │                                                                  │  │
│  │  ┌─────────────────────────────────┐   Conexión Privada (3306)   │  │
│  │  │  Compute VM Ampere A1 (ARM64)   │ ─────────────────────────┐  │  │
│  │  │  (4 OCPU, 24 GB RAM, 100 GB SSD)│                          │  │  │
│  │  │                                 │                          ▼  │  │
│  │  │  🐳 Docker Compose Stack:       │            ┌────────────────┴┐ │
│  │  │   • StreamVault v2 Core (:8000) │            │ MySQL HeatWave  │ │
│  │  │   • n8n Engine (:5678)          │            │  (Always Free)  │ │
│  │  │   • Evolution API v2 (:8081)    │            │  • 50 GB Disco  │ │
│  │  └─────────────────────────────────┘            │  • Backups Auto │ │
│  │                                                 │  • IP: 10.0.1.X │ │
│  │                                                 └─────────────────┘ │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### 💡 ¿Por qué esta arquitectura protege tu máquina ARM?
1. **n8n almacena gigabytes de ejecuciones**: En funcionamiento continuo, los webhooks y logs de n8n llenarían los 100 GB del disco local en pocos meses. Conectándolo a **MySQL HeatWave**, todos los datos pesados van a los **50 GB gratuitos de Oracle**.
2. **Evolution API almacena chats y mensajes en MySQL**: La base de WhatsApp no toca el disco de tu máquina.
3. **Poda Automática Integrada**: n8n purga ejecuciones de más de 7 días (`EXECUTIONS_DATA_PRUNE=true`), garantizando rendimiento constante sin intervención humana.

---

## 📋 2. Paso a Paso: Provisionamiento en Oracle Cloud

### Paso 2.1: Crear la Base de Datos MySQL HeatWave Always Free
1. Inicia sesión en la **Consola de Oracle Cloud** (`cloud.oracle.com`).
2. Abre el menú de navegación (icono hamburguesa ☰ arriba a la izquierda).
3. Ve a **Databases** > **MySQL HeatWave** > **DB Systems**.
4. Haz clic en **Create DB System**.
5. Configura los parámetros:
   - **Compartment**: Tu compartimento principal.
   - **Name**: `streamvault-mysql-db`.
   - **Database Type**: Selecciona **Always Free** (debe mostrar el badge verde *Always Free Eligible*).
   - **Administrator Credentials**:
     * Username: `streamadmin`
     * Password: Crea una contraseña segura (e.g., `StreamVault_2026_Secure!`).
   - **Networking**:
     * **Virtual Cloud Network**: Selecciona la misma VCN donde reside tu máquina ARM.
     * **Subnet**: Selecciona la subred privada (o pública) de tu VCN.
6. Haz clic en **Create**. El aprovisionamiento toma entre 5 y 10 minutos.
7. Una vez en estado **ACTIVE**, copia la **Private IP Address** asignada (ejemplo: `10.0.1.15`).

---

### Paso 2.2: Habilitar el Puerto 3306 en las Reglas de Seguridad de la VCN
Para que la máquina virtual ARM pueda comunicarse con MySQL HeatWave:
1. En la consola OCI, ve a **Networking** > **Virtual Cloud Networks**.
2. Haz clic en tu VCN y luego en **Security Lists** (ej. *Default Security List for VCN*).
3. Haz clic en **Add Ingress Rules**:
   - **Source Type**: CIDR
   - **Source CIDR**: `10.0.0.0/16` (o el rango CIDR de tu subred ARM).
   - **IP Protocol**: TCP
   - **Source Port Range**: All
   - **Destination Port Range**: `3306, 33060`
   - **Description**: `Permitir acceso interno a MySQL HeatWave desde la VM ARM`
4. Guarda la regla.

---

### Paso 2.3: Crear los Esquemas de Base de Datos para n8n y Evolution
Conéctate por SSH a tu máquina virtual ARM y crea las bases de datos ejecutando:

```bash
# Instalar el cliente ligero de MySQL si no lo tienes instalado
sudo apt-get update && sudo apt-get install -y mysql-client

# Conectarte a la IP privada de tu MySQL HeatWave
mysql -h 10.0.1.15 -u streamadmin -p

# Ejecutar dentro de MySQL:
CREATE DATABASE IF NOT EXISTS n8n_streamvault CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS evolution_streamvault CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
EXIT;
```

---

## 🐳 3. Despliegue del Stack Docker en la Máquina ARM

### Paso 3.1: Clonar el Repositorio y Configurar Variables
En tu máquina ARM:

```bash
cd /opt
git clone https://github.com/manuel3200/mcp-vencimientos.git streamvault
cd streamvault

# Crear el archivo de entorno desde la plantilla
cp .env.oracle.example .env
nano .env
```

Edita `.env` con tus datos:
```ini
# IP privada de tu MySQL HeatWave en Oracle Cloud
MYSQL_HEATWAVE_HOST=10.0.1.15
MYSQL_HEATWAVE_PORT=3306
MYSQL_HEATWAVE_USER=streamadmin
MYSQL_HEATWAVE_PASSWORD=StreamVault_2026_Secure!

# Contraseña de administrador de StreamVault y Secret HMAC
ADMIN_USER=admin
ADMIN_PASSWORD=TuPasswordAdminSeguro
SESSION_SECRET_KEY=GeneraUnaClaveHex64BitsConOpenssl
PUBLIC_BASE_URL=https://tudominio.com

# Telegram
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_ADMIN_CHAT_ID=123456789
TELEGRAM_CHANNEL_ID=-100123456789

# Evolution API
EVOLUTION_API_KEY=TuApiKeyHexEvolution2026
EVOLUTION_WEBHOOK_SECRET=TuWebhookSecretHmac
```

---

### Paso 3.2: Iniciar los Contenedores
Ejecuta el compose oficial para Oracle Cloud:

```bash
docker compose -f docker-compose.oracle-stack.yml up -d
```

Verifica que los tres servicios estén corriendo:
```bash
docker compose -f docker-compose.oracle-stack.yml ps
```

Deberás ver:
- `streamvault-core`: Puerto `8000` (CRM & FastMCP)
- `streamvault-n8n`: Puerto `5678` (Motor n8n conectado a MySQL)
- `streamvault-evolution`: Puerto `8081` (Gateway WhatsApp conectado a MySQL)

---

## ⚡ 4. Carga y Activación de los Flujos de n8n

Los flujos ya vienen pre-programados en la carpeta `./n8n/workflows` montada dentro de n8n:

1. Abre tu navegador y accede a `http://<IP_DE_TU_VM>:5678`.
2. Inicia sesión con el usuario y contraseña definidos en `.env` (`N8N_USER` y `N8N_PASSWORD`).
3. En el menú lateral izquierdo, haz clic en **Workflows** > **Import from File**.
4. Importa los 4 flujos listos:
   - **`workflow_whatsapp_antiban_buffer.json`**: Cola de mensajes con pausa gaussiana humana (45s a 90s) y límite de 50 msg/hora.
   - **`workflow_daily_audit_anchor.json`**: Publicación automática del hash raíz de auditoría en Telegram a las 23:59 hs.
   - **`workflow_evolution_heartbeat_watchdog.json`**: Guardián de desconexión de WhatsApp cada 15 minutos con alerta roja por Telegram.
   - **`workflow_financial_weekly_digest.json`**: Resumen financiero de rentabilidad neta real los domingos a las 20:00 hs.
5. Haz clic en cada flujo y activa el interruptor **Active** (arriba a la derecha).

---

## 📱 5. Vincular tu Teléfono a Evolution API

1. Abre `http://<IP_DE_TU_VM>:8081/instance/connect/streamvault` o usa el panel web de StreamVault v2 (`http://<IP_DE_TU_VM>:8000`).
2. Escanea el código QR con el WhatsApp de tu negocio.
3. El watchdog de n8n detectará automáticamente el estado `open` y dejará todo listo para procesar recordatorios y cobros.

---

## 🛡️ 6. Resumen de Seguridad y Costos

- **Costo Mensual**: **\$0.00 USD (Para Siempre)**.
- **Uso de Disco en la Máquina ARM**: Menos de **500 MB** en total para contenedores y dependencias (los 100 GB quedan completamente libres para tus otros proyectos).
- **Seguridad**:
  - MySQL HeatWave no está expuesto a internet (solo escucha en la red privada de tu VCN).
  - Todas las comunicaciones entre servicios usan la red interna de Docker (`streamvault-net`).
  - La bitácora criptográfica se ancla automáticamente cada noche a un canal privado de Telegram.
