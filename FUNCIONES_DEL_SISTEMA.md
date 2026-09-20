# 📑 INVENTARIO GENERAL DE FUNCIONES (~100 CAPACIDADES)
## StreamVault v2 / MCP Vencimientos

Este documento reúne el inventario exhaustivo de más de **100 funciones, herramientas, comandos y automatizaciones** activas y en diseño dentro del ecosistema **StreamVault v2 / MCP Vencimientos**.

Cada función cuenta con su indicador de contexto operativo:
- **`[🔒 Chat Privado 1:1]`**: Interacción individual y confidencial con el cliente por WhatsApp.
- **`[👥 Grupos]`**: Moderación y comandos en grupos de clientes o revendedores de WhatsApp.
- **`[📢 Canales]`**: Difusión unidireccional masiva (WhatsApp Channels / Telegram Channels).
- **`[🌐 Comunidad]`**: Dinámicas grupales, gamificación y subgrupos.
- **`[🛡️ Admin Consola]`**: Comandos y paneles exclusivos para administradores y operadores autorizados.
- **`[⚙️ FastMCP Core]`**: Herramientas nativas expuestas para modelos de IA (Gemini, Claude, Cursor, Antigravity).

---

## 🧭 ÍNDICE POR PILARES

1. [Servidor FastMCP: Cuentas y Pantallas (1 a 15)](#1-servidor-fastmcp-cuentas-y-pantallas)
2. [Servidor FastMCP: CRM y Clientes (16 a 22)](#2-servidor-fastmcp-crm-y-clientes)
3. [Servidor FastMCP: Finanzas y Pagos (23 a 30)](#3-servidor-fastmcp-finanzas-y-pagos)
4. [Servidor FastMCP: Catálogo y Combos (31 a 36)](#4-servidor-fastmcp-catálogo-y-combos)
5. [Servidor FastMCP: Moderación Atlas-MD (37 a 48)](#5-servidor-fastmcp-moderación-atlas-md)
6. [Servidor FastMCP: Sistema y Backups (49 a 58)](#6-servidor-fastmcp-sistema-y-backups)
7. [Bot de WhatsApp: Auto-Atención y Self-Service (59 a 66)](#7-bot-de-whatsapp-auto-atención-y-self-service)
8. [Bot de WhatsApp: Motor OCR y Pagos (67 a 71)](#8-bot-de-whatsapp-motor-ocr-y-pagos)
9. [Bot de WhatsApp: Comandos Administrativos (72 a 81)](#9-bot-de-whatsapp-comandos-administrativos)
10. [Bot de WhatsApp: Moderación de Grupos (82 a 90)](#10-bot-de-whatsapp-moderación-de-grupos)
11. [Bot Administrativo de Telegram (91 a 101)](#11-bot-administrativo-de-telegram)
12. [Chatwoot CRM: Slash Commands en Vivo (102 a 111)](#12-chatwoot-crm-slash-commands-en-vivo)
13. [Automatización HTTP Custom VPN (112 a 115)](#13-automatización-http-custom-vpn)
14. [Dashboard Web BFF & Seguridad (116 a 124)](#14-dashboard-web-bff--seguridad)
15. [Canales de Difusión & Comunidad (125 a 130)](#15-canales-de-difusión--comunidad)
16. [Motor de Scheduler y Mantenimiento (131 a 136)](#16-motor-de-scheduler-y-mantenimiento)

---

### 1. Servidor FastMCP: Cuentas y Pantallas
* **1. `crear_cuenta_con_pantallas`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Registra cuentas madre particionadas en perfiles independientes con PINs y casilleros.
* **2. `vender_perfil_compartido`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Asigna el próximo perfil libre de una cuenta compartida a un cliente específico.
* **3. `vender_cuenta_completa`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Asigna todos los perfiles de una cuenta (Netflix Full, Disney Full) a un solo comprador.
* **4. `vender_o_asignar_servicio`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Asignador inteligente que decide entre cuenta completa o perfil según disponibilidad.
* **5. `consultar_stock_libre`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Lista el inventario disponible filtrando por plataforma.
* **6. `agregar_stock_libre`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Ingresa cuentas o perfiles al inventario sin asociar cliente.
* **7. `consultar_alerta_stock_bajo`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Audita existencias contra umbrales mínimos configurados.
* **8. `configurar_umbral_stock`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Define el nivel crítico de reposición por servicio.
* **9. `enviar_alerta_stock_telegram`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Despacha el informe gráfico de stock bajo a Telegram.
* **10. `buscar_cuenta`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Localiza cuentas por ID numérico, correo, PIN o nombre de usuario.
* **11. `consultar_estado_pantallas`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Mapa visual de ocupación de perfiles de cuentas madre.
* **12. `consultar_cuentas_madre`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Monitorea cuentas maestras en riesgo de corte ante proveedores.
* **13. `renovar_cuenta_madre`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Extiende la vigencia mayorista de la cuenta ante el proveedor.
* **14. `marcar_cuenta_caida`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Cambia el estado de una suscripción a caída e inicia la cola de resolución.
* **15. `reemplazar_cuenta_caida`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: **Reemplazo en 1 clic**: toma una cuenta libre, la transfiere al cliente conservando su fecha original de vencimiento y genera las nuevas credenciales.

---

### 2. Servidor FastMCP: CRM y Clientes
* **16. `consultar_ficha_cliente`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: **Ficha 360°** con salud crediticia, semáforo de morosidad (🟢/🟡/🔴), LTV histórico en ARS y cuentas activas.
* **17. `buscar_cliente`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Búsqueda multicriterio por nombre, código de cliente o número telefónico.
* **18. `listar_clientes_activos`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Listado consolidado de todos los clientes con suscripciones vigentes.
* **19. `registrar_cliente`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Da de alta o actualiza clientes clasificándolos en Consumidor Final, Revendedor o Revendedor VIP.
* **20. `generar_cobro_consolidado_whatsapp`** `[⚙️ FastMCP Core]` `[🔒 Chat Privado 1:1]`: Unifica todos los servicios del cliente en una sola suma total para transferir con datos bancarios oficiales.
* **21. `generar_mensaje_whatsapp`** `[⚙️ FastMCP Core]` `[🔒 Chat Privado 1:1]`: Genera textos oficiales de bienvenida, recordatorio de vencimiento o aviso de corte.
* **22. `eliminar_todas_las_cuentas_excepto_cliente`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Rutina de depuración controlada para entornos de staging o auditoría.

---

### 3. Servidor FastMCP: Finanzas y Pagos
* **23. `consultar_balance_y_ganancias`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Ingresos cobrados, costo de proveedores, **utilidad neta real en ARS** y proyección mensual.
* **24. `consultar_cuentas_por_cobrar`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Total adeudado y lista de vencimientos de los próximos 7 días.
* **25. `registrar_cobro_cliente`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Asienta un pago manual en el libro diario y extiende la cuenta.
* **26. `consultar_datos_pago`** `[⚙️ FastMCP Core]` `[🔒 Chat Privado 1:1]`: Muestra los medios de cobro vigentes (Alias, CBU, CVU, Banco, Titular).
* **27. `configurar_datos_pago`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Modifica en caliente los datos bancarios para las respuestas del bot.
* **28. `listar_comprobantes_pendientes`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Cola de tickets bancarios recibidos pendientes de validación.
* **29. `aprobar_comprobante_pago`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Valida el pago, renueva la suscripción (+30 días), asienta en finanzas y notifica a WhatsApp y Telegram (soporta renovación multi-servicio masiva).
* **30. `rechazar_comprobante_pago`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Deniega un comprobante y avisa al cliente para que revise el envío.

---

### 4. Servidor FastMCP: Catálogo y Combos
* **31. `consultar_catalogo_precios`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Grilla completa de precios en ARS (Final, Revendedor, Costo).
* **32. `configurar_precio_catalogo`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Actualiza tarifas y calcula márgenes brutos de ganancia en vivo.
* **33. `generar_mensaje_catalogo_whatsapp`** `[⚙️ FastMCP Core]` `[🔒 Chat Privado 1:1]` `[📢 Canales]`: Formatea el catálogo con emojis para envíos rápidos.
* **34. `listar_combos`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Lista de paquetes promocionales activos (ej: Netflix + Disney+).
* **35. `crear_o_actualizar_combo`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Alta de promociones con precios combinados bonificados.
* **36. `vender_combo`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Asignación atómica de todas las plataformas del combo a un cliente.

---

### 5. Servidor FastMCP: Moderación Atlas-MD
* **37. `listar_grupos_whatsapp`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Lista de grupos donde el bot participa con su estado de moderación.
* **38. `sincronizar_grupos_whatsapp`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Consulta Evolution API y sincroniza chats grupales en la base de datos.
* **39. `configurar_grupo_whatsapp`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Define parámetros individuales por grupo (Antilink, bienvenida, despedida).
* **40. `enviar_tagall_grupo`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Mención masiva `@everyone` con aviso del administrador.
* **41. `controlar_grupo`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Abre (`unmute`) o cierra (`mute`) el grupo para permitir mensajes solo a admins.
* **42. `moderar_participante_grupo`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Expulsa (`kick`), promueve (`promote`) o degrada (`demote`) integrantes.
* **43. `configurar_modo_bot`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Modos globales: `public` (todos), `private` (ignora grupos) o `self` (solo dueño).
* **44. `obtener_modo_bot`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Consulta el modo operativo actual.
* **45. `banear_silencioso`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: **Silent Ban**: el bot ignora por completo al usuario o grupo sin notificarlos.
* **46. `desbanear_silencioso`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Remueve el bloqueo silencioso.
* **47. `listar_baneos_silenciosos`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Lista negra de usuarios y grupos bajo Silent Ban.
* **48. `agregar_grupo_whatsapp`** `[⚙️ FastMCP Core]` `[👥 Grupos]`: Registro manual de un grupo en la base de datos.

---

### 6. Servidor FastMCP: Sistema y Backups
* **49. `diagnostico_salud_sistema`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Estado de salud de SQLite, Evolution API, Telegram, Gemini y Scheduler.
* **50. `enviar_backup_telegram`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Genera y envía el backup comprimido `.db.gz` y el `.csv` a Telegram.
* **51. `exportar_resumen_csv`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Genera archivos CSV de inventario, clientes y transacciones.
* **52. `importar_stock_desde_csv`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Carga masiva de inventario desde CSV.
* **53. `importar_ventas_desde_csv`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Carga masiva de clientes y cuentas vendidas desde CSV.
* **54. `consultar_logs_sistema`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Visualización de logs de eventos y auditoría en tiempo real.
* **55. `listar_proveedores`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Agenda de mayoristas con contactos y servicios provistos.
* **56. `crear_o_actualizar_proveedor`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Registro de nuevos proveedores mayoristas.
* **57. `cambiar_clave_admin`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Modificación de clave del panel web con hash Argon2.
* **58. `resumen_ejecutivo_negocio`** `[⚙️ FastMCP Core]` `[🛡️ Admin Consola]`: Informe estratégico consolidado de ingresos, retención y stock.

---

### 7. Bot de WhatsApp: Auto-Atención y Self-Service
* **59. Consulta de Vencimiento y Claves** `[🔒 Chat Privado 1:1]`: El cliente escribe `/vencimiento`, `/clave`, `/cuenta` o *¿cuándo vence?* y recibe sus accesos, PINs y días restantes. *(Bloqueado estrictamente en grupos).*
* **60. Enlace Efímero de Credenciales (Anti-SIM Swap)** `[🔒 Chat Privado 1:1]`: En vez de texto plano permanente, genera un link de 1 solo uso con validez de 10 minutos para visualizar la clave.
* **61. Consulta de Medios de Pago** `[🔒 Chat Privado 1:1]`: Ante `/pagar`, `/cbu` o `/alias`, devuelve los datos bancarios y el importe exacto.
* **62. Liquidación Consolidada Multicuenta** `[🔒 Chat Privado 1:1]`: Si el cliente tiene múltiples servicios, calcula automáticamente el total unificado a transferir.
* **63. Catálogo Interactivo por Segmento** `[🔒 Chat Privado 1:1]`: Muestra los precios de Consumidor Final o Revendedor según el perfil del remitente.
* **64. Filtro de Catálogo por Plataforma** `[🔒 Chat Privado 1:1]`: El cliente consulta *"precios de netflix"* y recibe únicamente esa categoría.
* **65. Reporte de Cuenta Caída** `[🔒 Chat Privado 1:1]`: Al escribir `/caida` o *se cayó mi cuenta*, genera un ticket `#C<ID>` y tranquiliza al usuario.
* **66. Seguimiento Automático de Tickets** `[🔒 Chat Privado 1:1]`: Si el cliente reenvía mensajes sobre un ticket abierto, los adjunta a la solicitud abierta sin duplicar reclamos.

---

### 6. Bot de WhatsApp: Motor OCR y Pagos
* **67. Análisis Multimodal de Comprobantes (Gemini Vision)** `[🔒 Chat Privado 1:1]`: Extrae monto, banco, fecha e identificador de transferencia desde fotos de comprobantes.
* **68. OCR Local de Respaldo (Tesseract)** `[🔒 Chat Privado 1:1]`: Respaldo autónomo si la API externa de IA no está disponible o la imagen tiene publicidad.
* **69. Idempotencia y Deduplicación Estricta** `[🔒 Chat Privado 1:1]`: Ventana de 10 minutos por hash SHA-256 para ignorar comprobantes reenviados por error.
* **70. Detección Perceptual de Comprobantes Reciclados (pHash)** `[🔒 Chat Privado 1:1]`: Compara distancias de Hamming para detectar comprobantes viejos editados en 1 píxel.
* **71. Alerta de Fraude por Ticket Reciclado** `[🛡️ Admin Consola]`: Si el `operation_id` ya fue aprobado con anterioridad, enciende una alerta roja visual de emergencia en WhatsApp y Telegram.

---

### 9. Bot de WhatsApp: Comandos Administrativos
* **72. `/pagoapro_<ID>`** `[🛡️ Admin Consola]`: Aprueba comprobante individual, renueva 30 días, asienta en finanzas y agradece al cliente.
* **73. `/pagoapro_<ID>_all`** `[🛡️ Admin Consola]`: Renueva en bloque todas las cuentas que tenga contratadas el cliente.
* **74. `/pagoapro_<ID> <monto>`** `[🛡️ Admin Consola]`: Aprueba con un monto especial pactado fuera de tarifa estándar.
* **75. `/pagodene_<ID>`** `[🛡️ Admin Consola]`: Rechaza comprobante inválido y pide al cliente verificar la transferencia.
* **76. `/pagoparcial_<ID> <monto>`** `[🛡️ Admin Consola]`: Registra seña o pago a cuenta e informa el saldo adeudado.
* **77. `/revertir_pago_<ID>`** `[🛡️ Admin Consola]`: Anula el cobro contable y restaura la fecha de vencimiento previa de la cuenta.
* **78. `/baja_<ID>`** `[🛡️ Admin Consola]`: Corta el servicio, marca la cuenta como `por_cambiar_clave` y cancela recordatorios.
* **79. `/caida <ID/correo/cliente>`** `[🛡️ Admin Consola]`: Reemplazo automático en 1 clic asignando stock libre al cliente.
* **80. `/cambiar_<ID>`** `[🛡️ Admin Consola]`: Autoriza reemplazo desde un ticket formal `#C<ID>` y envía datos al cliente.
* **81. `/esperar_<ID>`** `[🛡️ Admin Consola]`: Pone el ticket en espera prioritaria avisándole al cliente que aguarde.

---

### 10. Bot de WhatsApp: Moderación de Grupos (Atlas-MD)
* **82. Auto-Descubrimiento de Grupos** `[👥 Grupos]`: Registra automáticamente cualquier grupo al que el bot sea añadido.
* **83. Mensaje de Bienvenida Configurable** `[👥 Grupos]`: Saluda a nuevos integrantes y comparte las normas de la comunidad.
* **84. Mensaje de Despedida** `[👥 Grupos]`: Envía un mensaje cordial cuando un miembro sale del grupo.
* **85. Antilink con Desofuscación Unicode (Delete)** `[👥 Grupos]`: Borra enlaces no autorizados incluso si usan caracteres invisibles o dominios cortos.
* **86. Antilink con Expulsión Inmediata (Kick)** `[👥 Grupos]`: Expulsa de inmediato al usuario que intente spamear links en el grupo.
* **87. `/tagall` / `/todos` / `@everyone`** `[👥 Grupos]`: Menciona a todos los miembros para anuncios cruciales del administrador.
* **88. `/mute` / `/cerrar`** `[👥 Grupos]`: Cierra el grupo permitiendo mensajes únicamente a administradores.
* **89. `/unmute` / `/abrir`** `[👥 Grupos]`: Abre el grupo para que todos los miembros puedan interactuar.
* **90. `/kick`, `/promote`, `/demote`** `[👥 Grupos]`: Gestión administrativa de participantes por comando en el grupo.

---

### 11. Bot Administrativo de Telegram (`telegram_bot.py`)
* **91. Tarjetas Interactivas de Vencimiento** `[🛡️ Admin Consola]`: Notificaciones enriquecidas con botones de 1 toque (Aprobar, Renovar Todas, Ficha 360°, Abrir WhatsApp).
* **92. Menú de Inicio Interactivo (`/start`, `/menu`)** `[🛡️ Admin Consola]`: Teclado inline de navegación rápida.
* **93. Consulta de Balance en Vivo (`/balance`)** `[🛡️ Admin Consola]`: Ingresos, costos, ganancia neta y proyección del mes.
* **94. Inventario y Alertas de Stock (`/stock`)** `[🛡️ Admin Consola]`: Diagnóstico de existencias por plataforma con semáforo de colores.
* **95. Ficha 360° en Telegram (`/cliente <query>`)** `[🛡️ Admin Consola]`: Despliega perfil, LTV y suscripciones con botones de contacto directo.
* **96. Reemplazo Remoto de Caídas (`/caida`, `/cambiar`, `/esperar`)** `[🛡️ Admin Consola]`: Gestión de tickets y reemplazos desde el chat de Telegram.
* **97. Asiento de Pagos Parciales (`/pagoparcial <ID> <monto>`)** `[🛡️ Admin Consola]`: Registro de señas desde Telegram.
* **98. Reversión Contable (`/revertirpago <ID>`)** `[🛡️ Admin Consola]`: Rollback de pagos erróneos.
* **99. Cifrado y Exportación de Backup (`/backup`)** `[🛡️ Admin Consola]`: Genera y envía el backup comprimido y cifrado al chat.
* **100. Escaneo Manual Forzado (`/escanear`)** `[🛡️ Admin Consola]`: Ejecuta el barrido de vencimientos de todo el mes.
* **101. Consulta de Auditoría (`/auditoria <ID>`)** `[🛡️ Admin Consola]`: Muestra la bitácora inmutable de modificaciones sobre una cuenta o cliente.

---

### 12. Chatwoot CRM: Slash Commands en Vivo
* **102. Venta Rápida Netflix Casa Extra (`/nc_n_casaextra`)** `[🛡️ Admin Consola]`: Asigna 1 pantalla de Netflix y entrega credenciales.
* **103. Venta Rápida Netflix Full (`/nc_n_full`)** `[🛡️ Admin Consola]`: Asigna una cuenta completa de Netflix de 4 pantallas.
* **104. Venta Rápida Disney+ (`/nc_disney`)** `[🛡️ Admin Consola]`: Asigna Disney+ Premium al cliente actual.
* **105. Venta Rápida Max / HBO (`/nc_max`)** `[🛡️ Admin Consola]`: Asigna Max Estándar/Platino.
* **106. Venta Rápida Prime / Spotify / YouTube** `[🛡️ Admin Consola]`: Comandos `/nc_prime`, `/nc_spotify`, `/nc_youtube`.
* **107. Registro de Pago en Vivo (`/pago [monto]`, `/renovar`)** `[🛡️ Admin Consola]`: Renueva 30 días y notifica al cliente y a Telegram.
* **108. Aprobación y Rechazo de Comprobantes (`/pagoapro`, `/pagodene`)** `[🛡️ Admin Consola]`: Opera la bandeja de pagos desde Chatwoot.
* **109. Reemplazo de Caídas en Chatwoot (`/caida`)** `[🛡️ Admin Consola]`: Resuelve caídas del cliente actual sin cambiar de pestaña.
* **110. Consulta de Suscripciones (`/info`, `/stock`, `/cbu`)** `[🛡️ Admin Consola]`: Información operativa al instante en notas privadas.
* **111. Renombrar Contacto (`/nombre <Nuevo Nombre>`)** `[🛡️ Admin Consola]`: Actualiza el nombre en Chatwoot y en la base de datos simultáneamente.

---

### 13. Automatización HTTP Custom VPN
* **112. Interceptación de Mensajes Salientes** `[🔒 Chat Privado 1:1]`: Detecta envíos de usuario, HWID y fecha de vencimiento por WhatsApp o Chatwoot.
* **113. Alta Automática en Base de Datos** `[⚙️ FastMCP Core]`: Registra el servidor VPN y programa sus avisos de vencimiento sin intervención manual.
* **114. Tarifación Inteligente por Tipo de Cliente** `[⚙️ FastMCP Core]`: Aplica tarifa de Consumidor Final o Revendedor e ingresa la ganancia en finanzas.
* **115. Protección de HWID en Autoservicio** `[🔒 Chat Privado 1:1]`: Muestra el identificador HWID ofuscado y seguro en las consultas del cliente.

---

### 14. Dashboard Web BFF & Seguridad
* **116. Panel de Cuentas y Casilleros con PIN** `[🛡️ Admin Consola]`: Gestión visual de cuentas, perfiles, contraseñas maestras y estados.
* **117. Panel CRM 360° con Semáforo de Morosidad** `[🛡️ Admin Consola]`: Fichas de clientes, historial de compras, valor LTV y deudas.
* **118. Panel Financiero y Libro Contable** `[🛡️ Admin Consola]`: Balance en tiempo real, ingresos, costos y cuentas por cobrar a 7 días.
* **119. Visor de Comprobantes Bancarios** `[🛡️ Admin Consola]`: Previsualización de imágenes y PDFs con botones de aprobación y rechazo.
* **120. Panel de Moderación de Grupos WhatsApp** `[🛡️ Admin Consola]`: Configuración de Antilink, bienvenidas, Bot Mode y lista negra de Silent Ban.
* **121. Editor de Catálogo de Precios y Combos** `[🛡️ Admin Consola]`: Configuración de tarifas ARS con cálculo de márgenes netos.
* **122. Doble Factor de Autenticación (2FA)** `[🛡️ Admin Consola]`: Acceso protegido con Google Authenticator (TOTP) o código OTP por Telegram.
* **123. Protección Anti-Fuerza Bruta y Sesiones Cifradas** `[🛡️ Admin Consola]`: Rate limiting en login (5 intentos) y cookies firmadas `HttpOnly`.
* **124. Log de Auditoría Inmutable (Append-Only)** `[🛡️ Admin Consola]`: Registro con firma HMAC de rotación de claves maestras y bajas de cuentas.

---

### 15. Canales de Difusión & Comunidad
* **125. Publicación Automática de Novedades** `[📢 Canales]`: Difusión en 1 clic de stock nuevo y promociones en WhatsApp Channels y Telegram Channels.
* **126. Encuestas Rápidas de Demanda** `[📢 Canales]`: Sondeos de opinión para consultar qué nuevos servicios sumar al catálogo.
* **127. Alertas Masivas de Estado de Red** `[📢 Canales]`: Comunicados ante caídas globales de servidores de streaming para evitar saturación de chats privados.
* **128. Ranking de Miembros Activos (Gamificación)** `[🌐 Comunidad]`: Premiación automática a los miembros más activos del grupo de clientes.
* **129. Auto-Respuesta a FAQs por Palabras Clave** `[🌐 Comunidad]`: El bot atiende consultas frecuentes en el grupo sin molestar a los administradores.
* **130. Sub-Grupos Temáticos Automatizados** `[🌐 Comunidad]`: Distribución comunitaria por afinidad (ej: *Comunidad Netflix*, *Revendedores VIP*).

---

### 16. Motor de Scheduler y Mantenimiento
* **131. Alerta Preventiva de Vencimiento (T-5)** `[🔒 Chat Privado 1:1]`: Recordatorio automático 5 días antes con botón de pago.
* **132. Alerta Prioritaria de Vencimiento (T-2)** `[🔒 Chat Privado 1:1]`: Recordatorio 48 horas antes para asegurar renovación sin cortes.
* **133. Alerta de Día de Vencimiento (T-0)** `[🔒 Chat Privado 1:1]`: Aviso final en la mañana de la fecha límite de pago.
* **134. Alerta de Corte y Rotación** `[🛡️ Admin Consola]`: Notifica al admin las cuentas vencidas no pagadas para proceder con la rotación de credenciales.
* **135. Alerta Matutina Diaria de Stock Bajo** `[🛡️ Admin Consola]`: Envío automático a las 09:00 AM a Telegram con existencias críticas.
* **136. Purga Periódica de Medios (>60 días) con Freno de Emergencia** `[⚙️ FastMCP Core]`: Limpieza de comprobantes antiguos en Base64 para preservar espacio en disco sin alterar registros contables.

---

*Documento consolidado de capacidades de producción de StreamVault v2 / MCP Vencimientos.*
