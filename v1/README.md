# ⚡ Gemini Spark MCP - CRM de Cuentas y Perfiles de Streaming

Servidor **MCP (Model Context Protocol)** con transporte **Streamable HTTP / SSE**, diseñado para integrarse con **Gemini Spark** (`gemini.google.com/spark/apps`) y correr en **Docker (Portainer)** sobre arquitectura **ARM64 (Oracle Cloud Ampere)** o AMD64.

Gestiona tu negocio de venta de cuentas y perfiles de streaming (Netflix, Disney+, Max, Prime, Spotify, YouTube, etc.) directamente en lenguaje natural con Gemini Spark.

---

## 🚀 Capacidades del Sistema

1. **Gestión de Clientes (CRM):**
   - Registro de clientes con ID único (`CLI-001`), nombre/alias (ej: "Maik"), teléfono de WhatsApp y usuario de Telegram.
   - Distinción entre **Revendedor** y **Consumidor Final**.
2. **Stock Libre (Inventario):**
   - Almacena cuentas y perfiles libres listos para entregar.
3. **Reporte de Cuentas Caídas:**
   - Si una suscripción se cae, se marca como caída con el motivo.
4. **Reemplazo Inteligente Automático:**
   - Si dices *"Cámbiame este correo caído"*, el sistema detecta la plataforma, busca una cuenta libre de la **misma plataforma** en el stock, se la asigna al cliente conservando su fecha de vencimiento y te entrega las credenciales para enviárselas.
5. **Alertas a Telegram 2 días antes:**
   - Te avisa diariamente con los datos de contacto directo (enlace a WhatsApp y Telegram) para que le cobres la renovación con 1 clic.
6. **Panel Web Protegido con 2FA:**
   - Visualiza en pestañas: *Clientes y Activas*, *Stock Libre* y *Cuentas Caídas*.

---

## 💬 Ejemplos de uso con Gemini Spark

* **Vender o asignar un servicio:**
  > *"Anota una venta para Maik, su WhatsApp es +5491122334455 y su Telegram es @maik_stream, es revendedor. La cuenta es netflix1@correo.com clave 1234, perfil 2, vence el 25 de octubre por $10 USD mensual."*

* **Buscar cliente y sus servicios:**
  > *"Búscame a Maik, ¿qué cuentas tiene y cuándo vencen?"*

* **Cargar cuentas libres al stock:**
  > *"Tengo este Disney+ libre en stock: correo disney_libre@correo.com clave pass123."*

* **Reportar caída:**
  > *"Se cayó la suscripción de netflix1@correo.com, márcalo como caído."*

* **Reemplazar automáticamente por una cuenta de la misma plataforma:**
  > *"Cámbiame el correo caído netflix1@correo.com por una libre."*
  *(Gemini te responderá con las nuevas credenciales de la cuenta de reemplazo).*

* **Consultar stock libre:**
  > *"¿Cuánto stock libre tengo disponible?"* o *"¿Tengo Disney+ libre?"*

* **Consultar cuentas caídas:**
  > *"¿Qué cuentas caídas tengo para reclamar?"*

* **Renovar tras recibir el pago:**
  > *"Maik me pagó la renovación de Netflix, cámbiale la fecha al 2026-11-25."*
