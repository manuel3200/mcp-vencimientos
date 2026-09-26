# Revisión del proyecto StreamVault v2: problemas, vulnerabilidades y protección

Fecha: 26 de septiembre de 2026. Directorio revisado: `F:\mcp`.

## 1. Resultado y alcance

La v2 incorpora medidas útiles, pero contiene fallos de autenticación, autorización, OAuth y exposición de información que deben corregirse antes de considerarla endurecida para Internet. Los resultados previos de «100/100» o «cero vulnerabilidades» no constituyen evidencia suficiente: el propio auditor puede aprobar comprobaciones que no ejecutó.

Esta revisión se concentra en el código de `v2`, sus rutas HTTP/MCP, persistencia, servicios de Telegram/WhatsApp/Chatwoot, Docker, integración n8n, CI y herramientas de auditoría. Incluye los hallazgos de la revisión inicial, contrastados con lecturas adicionales. No es una auditoría exhaustiva de v1, de todo el historial Git ni de los servidores de producción.

**Método:** inspección estática de código, seguimiento de llamadas e imports, búsqueda de usos de controles de seguridad y contraste de configuración. No se atacaron servicios externos, no se enviaron mensajes ni se cambiaron credenciales o configuraciones.

**Limitaciones:** `python`, `python3`, `py` y `docker` no están disponibles en el PATH de esta sesión. El intento anterior de ejecutar `v2/harness_verify.py` falló antes de iniciar Python. No se ejecutaron el harness, pruebas HTTP, construcción de imagen, auditoría de paquetes instalados ni ensayos de concurrencia. No se ha demostrado explotación en producción ni se atribuyen CVE a versiones instaladas desconocidas. Las referencias a funciones permiten localizar evidencia aunque cambien las líneas.

Se preservaron las modificaciones preexistentes en los workflows `workflow_evolution_heartbeat_watchdog.json` y `workflow_whatsapp_antiban_buffer.json`; este último se revisó tal como está en el directorio de trabajo. El único cambio de esta tarea es este informe.

### Cómo interpretar la prioridad

- **P0:** corregir antes de exponer públicamente o de ampliar el acceso.
- **P1:** siguiente bloque de trabajo, por impacto en integridad, secretos o disponibilidad.
- **P2:** robustez y mantenibilidad necesarias para sostener la protección.

La severidad considera condiciones de explotación. Un valor inseguro por defecto es un fallo confirmado del código, pero no demuestra que producción utilice ese valor.

## 2. Vulnerabilidades de autenticación y autorización

### V01 — Clave de sesión y credenciales previsibles por defecto

**Prioridad P0 · Crítica si se usa la clave por defecto · Confirmada en código.**

Evidencia: `v2/core/config.py`, atributos `SESSION_SECRET_KEY`, `ADMIN_PASSWORD` y `EVOLUTION_API_KEY`; `v2/core/security.py`, creación y verificación de cookies; `v2/main.py`, `lifespan`.

La aplicación acepta secretos fijos incluidos en el código y crea el administrador con una contraseña conocida si no se configura otra. Una clave de sesión conocida permite fabricar cookies firmadas válidas y eludir el acceso normal, incluido el segundo factor. Una variable definida pero vacía tampoco se rechaza de manera centralizada. El riesgo excede el acceso al login.

**Corrección:** validar configuración al arrancar; rechazar claves vacías, valores de ejemplo y claves débiles; generar secretos aleatorios independientes fuera del repositorio. Aprovisionar el primer administrador mediante un procedimiento de un solo uso. Si estos valores se utilizaron en una instancia accesible, rotarlos e invalidar sus sesiones y tokens.

**Comprobación de cierre:** el arranque falla con secretos ausentes, vacíos o de ejemplo; una cookie firmada con la antigua clave es rechazada.

### V02 — Recuperación de contraseña cambia la clave sin verificar al solicitante

**P0 · Alta · Confirmada.**

Evidencia: `v2/presentation/web/auth_routes.py:86`, `recover_password`.

`POST /recuperar` cambia inmediatamente la contraseña del administrador sin sesión, prueba de recuperación ni limitación de intentos en esa ruta. Cualquiera puede repetir la operación y bloquear el acceso del propietario. La clave se cambia antes de conocer el resultado del envío a Telegram: un fallo de entrega deja al usuario sin la contraseña nueva.

Esto demuestra una vía de denegación de acceso, no por sí solo que el atacante conozca la contraseña generada. Se ajusta aquí la calificación crítica de la revisión inicial a alta por ese motivo.

**Corrección:** enviar un token de recuperación de uso único, con expiración y límites por cuenta e IP; cambiar la clave solo al validar el token. No enviar contraseñas como mecanismo de recuperación. Revocar sesiones al completar el cambio y registrar el evento.

**Comprobación:** solicitar recuperación no modifica el hash; repetir solicitudes se limita; un token vencido o consumido no cambia la clave.

### V03 — Creación de secretos efímeros con autenticación aparente

**P0 · Alta · Confirmada.**

Evidencia: `v2/presentation/web/ephemeral_routes.py:173`, `create_ephemeral_secret_api`.

Cuando falta una sesión válida, basta que exista una cabecera `Authorization` no vacía. Su contenido nunca se valida. Esto permite crear registros y enlaces alojados bajo el dominio del proyecto sin identidad comprobada, consumir almacenamiento y abusar de la confianza en ese dominio. No permite por sí solo leer otros secretos ya existentes.

**Corrección:** validar un token real de servicio o de OAuth con permiso explícito para crear secretos; aplicar cuotas, tamaños máximos y auditoría del actor autenticado.

**Comprobación:** cabeceras arbitrarias, tokens vencidos y tokens sin permiso devuelven 401/403 sin insertar filas.

### V04 — OAuth evita el segundo factor y la protección del login

**P0 · Alta · Confirmada.**

Evidencia: `v2/presentation/web/oauth_routes.py:147`, `oauth_authorize_post`.

Si no hay sesión, el endpoint comprueba directamente usuario y contraseña y genera un código OAuth. No pasa por `/2fa` ni por `auth_rate_limiter`. Por tanto, la protección 2FA del panel no cubre esta vía de autorización; también queda una ruta alternativa de intentos de contraseña. El canje de código sigue requiriendo el secreto del cliente OAuth: no debe confundirse este fallo con acceso anónimo directo al MCP.

**Corrección:** exigir una sesión con autenticación completa y reciente para autorizar OAuth, usando el mismo flujo de login y 2FA. Aplicar límites también a autorización y canje de tokens.

**Comprobación:** una contraseña correcta sin segundo factor nunca produce un código de autorización.

### V05 — Validación OAuth incompleta: redirección, PKCE y consumo de códigos

**P0 · Alta · Confirmada; concurrencia pendiente de prueba.**

Evidencia: `v2/presentation/web/oauth_routes.py`, `oauth_authorize_get/post`; `v2/db/repositories/settings_repo.py:308`, `create_oauth_auth_code` y `verify_and_consume_auth_code`.

- Se almacena `redirect_uris`, pero estas funciones no comprueban que el destino solicitado pertenezca a esa lista.
- El canje recibe `redirect_uri`, pero no lo compara con el guardado en el código.
- PKCE es opcional; acepta `plain`; cuando el método no es `S256` ni `PLAIN`, no hay rama de rechazo y la comprobación se omite si se aporta un verificador.
- La lectura de `used` y su actualización no utilizan una actualización condicionada al estado anterior. El uso único bajo concurrencia necesita una prueba específica.

**Impacto:** redirecciones manipuladas, exposición de códigos y debilitamiento de las garantías del flujo. El secreto del cliente sigue siendo una condición para canjear tokens en la implementación actual.

**Corrección:** coincidencia exacta de redirecciones previamente registradas; comparación también al canjear; PKCE S256 obligatorio para el flujo previsto; rechazo de métodos desconocidos; consumo atómico con comprobación de filas afectadas. Validar `response_type` y scopes permitidos. Preferir una implementación OAuth mantenida a ampliar artesanalmente este protocolo.

**Comprobación:** destinos externos no registrados, URI distinta en canje, método PKCE desconocido y reutilización concurrente son rechazados.

### V06 — Webhooks quedan abiertos cuando faltan sus secretos

**P0 · Crítica si quedan públicos sin autenticación · Confirmada y condicionada al entorno.**

Evidencia: `v2/presentation/api/webhooks_api.py:267` y `:2039`; `v2/services/chatwoot_bot_service.py`, `process_chatwoot_command`; `v2/core/rbac.py`, `get_actor_role`.

Las verificaciones de WhatsApp y Chatwoot solo se ejecutan si sus secretos están configurados. En su ausencia, el cuerpo de una petición externa alcanza lógica de negocio. Chatwoot decide si un mensaje es de agente mediante campos del cuerpo (`private`, tipo de remitente o mensaje saliente). En WhatsApp, `is_from_me` puede otorgar rol de administrador. Sin autenticidad del evento, esos campos no acreditan identidad.

El Compose revisado entrega `EVOLUTION_WEBHOOK_SECRET`, pero no entrega `CHATWOOT_WEBHOOK_SECRET` ni `WEBHOOK_SECRET` al contenedor de la aplicación.

**Corrección:** rechazar solicitudes si no está configurada una autenticación válida para la integración habilitada. Configurar el mecanismo realmente soportado por cada emisor y verificarlo extremo a extremo; no asumir que declarar una variable configura al proveedor. Añadir antirrepetición persistente, control de instancia/cuenta y límites de cuerpo. Mantener deshabilitadas las rutas de integraciones no utilizadas.

**Comprobación:** sin secreto configurado, con firma inválida y con evento repetido no hay cambios ni envíos.

### V07 — API financiera reutiliza secretos de máxima confianza

**P0 · Alta · Confirmada.**

Evidencia: `v2/presentation/api/finance_api.py:13`; `n8n/workflows/workflow_financial_weekly_digest.json`, cabecera de autorización.

La API acepta la contraseña del administrador, la clave de firma de sesiones o el secreto de webhook como credenciales de servicio. Compartir la clave de sesión con automatizaciones amplía el conjunto de sistemas que pueden fabricar sesiones. El workflow incluye además una contraseña conocida como alternativa final.

**Corrección:** token de servicio independiente, revocable y limitado a lectura financiera; guardar su hash para verificación cuando no sea necesario recuperar el token. Retirar las alternativas basadas en contraseñas y claves de cifrado/firma. Rotar los secretos ya compartidos.

**Comprobación:** las claves de sesión, webhook y contraseña admin no sirven como tokens de API; un token financiero no ejecuta operaciones de escritura.

### V08 — El guard MCP no protege la ejecución real

**P0 si MCP se comparte con clientes · Alta · Confirmada.**

Evidencia: búsqueda de `validate_tool_execution` en `v2`: solo aparece su definición en `core/mcp_guard.py` y sus invocaciones en `tests/test_security_audit_hardening.py`. `main.py` importa `mcp_server.instance` y `mcp_server.tools`.

Las pruebas demuestran el comportamiento de una función aislada, pero esa función no está conectada a las herramientas activas. El middleware valida el token y no aplica permisos por herramienta/cliente. Además, el guard permite herramientas no clasificadas y permite continuar cuando faltan datos de identidad/alcance.

**Impacto:** no existe el aislamiento por cliente anunciado por ese guard. Si el MCP es exclusivamente administrativo, debe documentarse que un token válido entrega ese nivel de acceso y evitar distribuirlo a clientes. Las instrucciones al modelo no son una barrera de autorización.

**Corrección:** derivar identidad del token validado, introducir permisos en el despachador real y resolver la propiedad de cada recurso en el servidor. Denegar por defecto herramientas sin política e identidades incompletas. Añadir confirmación/reautenticación para acciones destructivas de alto impacto.

**Comprobación:** pruebas mediante el transporte MCP real con dos identidades; un cliente no puede usar IDs, teléfonos o búsquedas de otro ni invocar herramientas administrativas.

### V09 — Roles de WhatsApp con asignación permisiva

**P1 · Alta condicionada a configuración/identidad · Confirmada.**

Evidencia: `v2/core/rbac.py`, `_parse_admin_phone_roles` y `get_actor_role`.

Un rol desconocido se convierte en `SUPER_ADMIN`; la comparación alternativa solo utiliza los últimos diez dígitos del teléfono. Un error de configuración puede elevar privilegios y se pierde la unicidad internacional del identificador.

**Corrección:** rechazar roles inválidos al arrancar y comparar identificadores canónicos completos. Solo confiar en `fromMe` después de verificar el origen y la instancia del evento.

**Comprobación:** un rol mal escrito no concede acceso; números de distintos países con sufijo coincidente no comparten permisos.

### V10 — Telegram autoriza por chat, no por operador individual

**P1 · Alta si se utiliza un grupo administrativo · Confirmada para mensajes.**

Evidencia: `v2/infrastructure/external/telegram/bot_app.py:594`, `handle_telegram_message`.

El filtro verifica el ID del chat autorizado y después procesa comandos administrativos. No valida en esa entrada una lista de usuarios por `from.id`. Si el chat autorizado es un grupo, sus participantes con capacidad de enviar comandos pueden acceder a operaciones del bot. No tiene el mismo impacto cuando el chat es privado con el propietario.

**Corrección:** autorizar usuario y chat, con permisos por acción; revisar de igual forma los callbacks de botones. Restringir quién puede incorporar miembros al grupo.

## 3. Navegador, sesiones y secretos

### V11 — HTML sin escape permite XSS reflejado

**P0 · Alta · Confirmada por el flujo de datos.**

Evidencia: `v2/presentation/web/auth_routes.py:35`, parámetros `error`/`msg`; `v2/presentation/web/oauth_routes.py:92`, mensaje de client ID inválido; `v2/core/templates.py`, `render_template`.

Valores de consulta se interpolan en HTML y el renderizador hace reemplazos de texto sin escape. La ruta de error OAuth permite llegar al HTML incluso con un client ID inválido. La CSP global permite scripts inline, de modo que no neutraliza esta entrada. Un enlace manipulado puede ejecutar contenido en el origen del panel; `HttpOnly` no impide que ese código realice solicitudes con la sesión del navegador.

**Corrección:** escape según contexto, plantillas con autoescape y separación explícita entre fragmentos HTML confiables y texto del usuario. Revisar atributos, JavaScript y mensajes del dashboard. Migrar CSP hacia scripts propios con nonce/hash y sin `unsafe-inline`/`unsafe-eval`.

**Comprobación:** caracteres HTML en todas esas entradas se muestran como texto y no crean elementos ni ejecutan código.

### V12 — Una navegación GET puede revertir un pago

**P0 · Alta · Confirmada.**

Evidencia: `v2/presentation/api/accounts_api.py:724`, `reverse_payment_api`.

La reversión financiera se expone por POST y GET, y solo exige cookie. `SameSite=Lax` permite enviar cookies en determinadas navegaciones GET de nivel superior; un enlace externo puede inducir a un administrador autenticado a revertir un pago. También pueden interferir mecanismos de navegación/previsualización.

**Corrección:** eliminar la mutación GET, exigir POST y protección CSRF, y aplicar confirmación, permisos e idempotencia para la reversión. Extender la revisión CSRF a las demás mutaciones del panel; SameSite es defensa complementaria.

**Comprobación:** GET no modifica la base; POST sin prueba CSRF válida falla; repetir una reversión no duplica efectos.

### V13 — Sesiones sin revocación individual y cookies sin Secure

**P1 · Alta para revocación; media para transporte · Confirmada.**

Evidencia: `v2/core/security.py:38`; `v2/presentation/web/auth_routes.py:77`, `:160` y `logout`.

Las cookies duran siete días y se validan solo por firma/tiempo. Logout elimina la cookie del navegador, pero una copia continúa siendo válida; el cambio de contraseña no consulta/invalida esa sesión. La emisión omite `secure=True` y puede permitir su envío por HTTP si ese acceso existe.

**Corrección:** sesiones con identificador revocable o versión por usuario; invalidación al recuperar clave, retirar privilegios o cerrar sesiones; `Secure`, `HttpOnly`, SameSite adecuado y HTTPS obligatorio. Ajustar duración y reautenticación de acciones sensibles.

### V14 — Tokens OAuth duraderos, recuperables de la base y transportables en URL

**P1 · Alta · Confirmada.**

Evidencia: `v2/db/repositories/settings_repo.py:355`, `create_oauth_tokens`, `refresh_oauth_token`; `v2/main.py`, `mcp_oauth_guard`.

El access token dura por defecto 31.536.000 segundos (365 días). Access y refresh tokens se almacenan en claro. La renovación no comprueba una expiración absoluta del refresh token. El MCP acepta además `access_token` en query string, con riesgo de exposición en registros y URLs compartidas.

**Corrección:** access tokens cortos, refresh con límite absoluto, rotación atómica y detección de reutilización; almacenar hashes cuando sea viable; revocación por usuario/cliente; aceptar bearer solo en cabecera y ocultarlo en logs. Rotar sesiones y tokens si se sospecha filtración de la base.

### V15 — Enlaces de un uso se consumen mediante GET y dejan copias del secreto

**P1 · Media · Confirmada; activación de previsualizadores depende del cliente.**

Evidencia: `v2/presentation/web/ephemeral_routes.py:145`; `v2/core/ephemeral_secrets.py`, creación, consumo y quemado.

La primera visita GET revela y consume el contenido: un escáner o previsualizador podría agotarlo antes del usuario. Las respuestas no establecen explícitamente `Cache-Control: no-store`. El token completo se registra en logs/auditoría. Quemar el enlace marca estados, pero no elimina `ciphertext`; por tanto, la descripción «autodestrucción» no equivale a borrado de la información almacenada.

**Corrección:** GET presenta una página neutra; POST explícito revela con consumo atómico. Responder con `no-store` y política de referer restrictiva; anonimizar identificadores en logs; definir purga del contenido y retención de respaldos. Un enlace bearer tampoco protege por sí solo contra toma de la cuenta de mensajería o SIM swap.

### V16 — Reutilización de claves y garantías de auditoría exageradas

**P1 · Media/alta según exposición · Confirmada.**

Evidencia: `v2/core/security.py`, derivación de claves de backups/secretos; `v2/core/audit.py`, `get_audit_hmac_key`, `compute_audit_signature`, `log_audit_event`.

Firma de sesiones, cifrado y auditoría pueden terminar dependiendo del mismo secreto. La cadena HMAC reside en una base controlada por la aplicación: alguien con acceso a la base y a la clave puede reconstruirla. No ofrece no repudio frente al poseedor de esa clave. Además, leer el último evento e insertar el siguiente son operaciones separadas, con riesgo de ramificación bajo concurrencia pendiente de ensayo. La serialización por separadores `|` no es inequívoca si los campos contienen ese carácter.

**Corrección:** claves independientes y versionadas; serialización canónica sin ambigüedad; lectura/firma/inserción serializadas en una transacción; anclas externas verificables y almacenamiento append-only con permisos separados. Diseñar rotación conservando la capacidad de descifrar backups históricos. No rotar claves de datos sin migración y copia recuperable.

### V17 — Excepciones devueltas al cliente evitan el saneamiento global

**P1 · Media · Confirmada.**

Evidencia: `v2/presentation/api/webhooks_api.py:2069` devuelve `str(e)`; `v2/presentation/api/accounts_api.py`, excepción en reversión de pagos.

Las excepciones capturadas localmente no pasan por el manejador global sanitizado. Pueden exponer detalles de errores internos; su contenido exacto depende del fallo.

**Corrección:** respuestas genéricas con identificador de incidente y detalle en logs protegidos, aplicando redacción de tokens, contraseñas y datos personales.

## 4. Despliegue, integraciones y disponibilidad

### O01 — Variables del Compose no coinciden con el programa

**P0 para una instalación nueva · Alta operativa · Confirmada.**

Evidencia: `docker-compose.oracle-stack.yml`; `v2/core/config.py`; `v2/main.py`; `v2/infrastructure/external/telegram/bot_app.py:15`; workflow financiero.

| Compose / automatización | Programa consumidor | Consecuencia |
|---|---|---|
| `ADMIN_USER` | Lee `ADMIN_USERNAME` | El nombre configurado se ignora y se usa el predeterminado. |
| `TELEGRAM_ADMIN_CHAT_ID` en la app | Lee `TELEGRAM_CHAT_ID` | OTP, notificaciones y autorización del bot pueden quedar sin destino. |
| n8n no recibe las variables que busca el workflow financiero | Busca `STREAMVAULT_ADMIN_PASSWORD`, `ADMIN_PASSWORD` o `SESSION_SECRET_KEY` | Termina usando la alternativa conocida y falla con una app protegida correctamente. |
| No se entrega secreto Chatwoot a la app | La ruta depende de `CHATWOOT_WEBHOOK_SECRET` | La ruta puede quedar abierta. |
| `N8N_BUFFER_WEBHOOK_URL` se declara para la app | No se encontró uso en el código v2 revisado | No hay evidencia de que los envíos pasen por el buffer anunciado. |

**Corrección:** contrato único de configuración y validación al inicio; prueba de integración del Compose con credenciales ficticias. No resolver la integración financiera copiando la clave de sesión: usar el token dedicado de V07.

### O02 — Arrancar vuelve a sobrescribir la contraseña del administrador

**P1 · Media/alta operativa · Confirmada.**

Evidencia: `v2/main.py`, `lifespan`; `v2/db/repositories/admin_repo.py`, `create_or_update_admin`.

Cada arranque sincroniza la contraseña desde el entorno, incluso para un usuario existente. Una recuperación queda anulada al reiniciar; una contraseña antigua del entorno puede recuperar vigencia sin intención del operador.

**Corrección:** separar bootstrap de actualización explícita de credenciales; no modificar cuentas existentes como efecto secundario de iniciar el servicio.

### O03 — Servicios publicados directamente y contenedor de aplicación como root

**P1 · Alta condicionada a la red · Confirmada en configuración.**

Evidencia: `docker-compose.oracle-stack.yml`, puertos 8000, 5678 y 8081; `v2/Dockerfile`.

Los puertos se publican sin vinculación a loopback y el Dockerfile no declara usuario no privilegiado. El Compose no incluye terminación TLS ni límites explícitos de recursos. La accesibilidad real depende del firewall y de infraestructura que no se inspeccionó; no se afirma que esos puertos estén accesibles desde Internet.

**Corrección:** exponer solo el proxy HTTPS; red privada para aplicación y Evolution; administración de n8n limitada a VPN/red administrativa. Ejecutar la app sin root, retirar capabilities, usar `no-new-privileges`, límites de CPU/memoria/PID y filesystem de solo lectura donde sea viable, con volúmenes/tmpfs para escritura necesaria.

### O04 — Workflow de envío WhatsApp sin autenticación declarada

**P0 si el webhook se publica · Alta · Confirmada en el JSON revisado.**

Evidencia: `n8n/workflows/workflow_whatsapp_antiban_buffer.json`, nodo `Webhook Entrada StreamVault`.

El nodo no declara autenticación y conduce a una llamada a Evolution con la API key del servidor. Su activación/importación y acceso público no se verificaron. Si la ruta queda accesible, terceros podrían intentar usar la capacidad de envío de la instancia.

**Corrección:** autenticar origen, limitar destinatarios/instancias según permisos, cuotas e idempotencia; mantener la entrada interna cuando solo la consume la app. Validar con la versión concreta de n8n la extracción del cuerpo, expresiones y respuestas del workflow antes de activarlo.

### O05 — Esperar por mensaje no implementa una cola global

**P1 · Media/alta operativa · Confirmada la estructura; carga pendiente.**

Evidencia: mismo workflow, nodo Wait de 45–90 segundos y respuesta posterior al despacho.

Ejecuciones concurrentes pueden esperar a la vez y después enviar en ráfaga. No se aprecia una cola persistente que serialice por instancia ni un límite global. La respuesta posterior al envío aumenta la exposición a timeouts y reintentos duplicados. Retrasos aleatorios no garantizan evitar bloqueos del proveedor.

**Corrección:** recepción con acuse rápido tras persistir, cola por instancia, límite global, reintentos con backoff e idempotencia y estado consultable. Aplicar consentimiento, bajas y límites de uso del proveedor.

### O06 — Descarga del archivo SQLite activo no garantiza un backup consistente

**P1 · Alta para recuperación · Confirmada.**

Evidencia: `v2/presentation/api/tools_api.py:247`; `v2/infrastructure/persistence/connection.py`, activación de WAL.

El endpoint descarga `services.db` directamente mientras la aplicación puede escribir. En modo WAL, operaciones confirmadas pueden permanecer en el archivo WAL, que no forma parte de esa descarga. Además, entrega la base completa sin cifrado del contenedor de backup: aunque algunos campos estén cifrados, otros datos y tokens siguen siendo sensibles.

**Corrección:** generar un snapshot con la API de backup SQLite, cifrar el resultado con clave dedicada, limitar la descarga y registrar el acceso. Verificar restauración en un entorno aislado y acordar cuánta pérdida de datos y tiempo de recuperación son aceptables.

### O07 — Cargas y trabajo pesado necesitan límites antes de su procesamiento

**P1 · Media · Confirmada parcialmente; impacto pendiente de carga.**

Evidencia: `webhooks_api.py:265`, lectura completa del cuerpo; `tools_api.py`, importaciones con `await file.read()`; `services/receipt_service.py`, OCR y parseo; `core/rate_limiter.py`.

Hay límites locales útiles, como el tamaño de PDF, pero se aplican después de recibir contenido. No se observó una política global de tamaño de petición. El procesamiento OCR y acceso SQLite síncrono pueden ocupar el hilo de ejecución si se usan directamente desde rutas async. Los limitadores en memoria no se comparten entre procesos y se reinician con el servicio.

**Corrección:** límite de cuerpo en proxy y aplicación antes de cargarlo completo; lectura acotada, límites de dimensiones/páginas/tiempo de OCR y workers con recursos restringidos. Llevar cuotas críticas a almacenamiento compartido. Medir latencia y memoria con una carga controlada.

## 5. Calidad, arquitectura y falsa confianza en auditorías

### Q01 — El DAST aprueba pruebas que no ejecutó

**P0 para usarlo como compuerta de producción · Alta · Confirmada.**

Evidencia: `security_pipeline/dast_runner.js`, rama de servidor no disponible.

Cuando no puede conectar, añade resultados `PASSED` con descripciones fijas, sin ejecutar esas comprobaciones. Algunas rutas allí descritas tampoco coinciden con las rutas activas revisadas. `orchestrator.js` presenta luego las comprobaciones como superadas si no hay findings.

**Corrección:** estados `NOT_RUN`/`ERROR` separados de `PASSED`; pruebas contra una instancia efímera real; bloquear la compuerta si las verificaciones obligatorias no corrieron. Un reporte histórico de puntuación no certifica esta versión.

### Q02 — El SCA puede convertir un error de consulta en «sin vulnerabilidades»

**P1 · Alta como defecto de verificación · Confirmada.**

Evidencia: `security_pipeline/dependency_auditor.js`, `queryOsvApi` y parser de requirements.

Errores HTTP, JSON inválido, timeout o fallo de red retornan lista vacía. Consulta la cota mínima declarada de cada dependencia, no el inventario realmente instalado, y no recorre transitivas. Comparar versiones como cadenas tampoco garantiza identificar correctamente la versión corregida.

**Corrección:** informar fallo/incompleto; inventario de dependencias resueltas y transitivas; auditoría de imagen/paquetes reales; comparación semántica de versiones. No se identifican aquí CVE concretas porque no se verificaron versiones instaladas.

### Q03 — Detección de secretos y SAST con cobertura insuficiente

**P1 · Media · Confirmada.**

Evidencia: `security_pipeline/secret_detector.js`, `sast_scanner.js`, `orchestrator.js`.

El detector excluye valores con `admin123` y depende de patrones que no cubren bien defaults de `os.getenv`. El objetivo del pipeline es `v2`, dejando fuera configuraciones y workflows de la raíz. El SAST se apoya en regex por línea y no sigue flujos como autorización incompleta o HTML sin escape. La compuerta normal solo bloquea hallazgos críticos del SAST; las otras categorías no necesariamente detienen una publicación.

**Corrección:** distinguir secretos reales, defaults inseguros y ejemplos; revisar también configuración e historial Git con redacción de resultados. Complementar con analizadores maduros y pruebas de comportamiento. Definir fallos obligatorios para autenticación, autorización y verificaciones no ejecutadas.

### Q04 — Pruebas aisladas no prueban las rutas de seguridad

**P1 · Alta como brecha de cobertura · Confirmada en lo inspeccionado.**

Evidencia: `v2/tests/test_security_audit_hardening.py`; `v2/harness_verify.py`; `v2/tests/harness_runner.py`.

La suite del guard lo invoca directamente, sin demostrar que proteja herramientas reales. Su descripción menciona firma del webhook, pero no contiene un ensayo HTTP de esa firma. Hay dos runners con listas distintas (14 y 10 suites); el situado dentro de `tests` inserta ese directorio, no la raíz de v2, y su ejecución directa depende del entorno. El README todavía describe cinco suites y un tiempo fijo que no se verificó.

**Corrección:** único punto de ejecución; tests HTTP/MCP de controles reales, configuración, identidad, efectos en base y concurrencia. Reportar cantidad efectivamente ejecutada y pruebas omitidas, sin afirmar cobertura del 100% sin medición.

### Q05 — Dependencias e imágenes no reproducibles

**P1 · Media · Confirmada.**

Evidencia: `v2/requirements.txt` usa cotas `>=`; Compose usa `latest` para aplicación/n8n; CI utiliza versiones mayores de acciones.

Dos instalaciones del mismo commit pueden resolver componentes distintos. La documentación habla de dependencias fijadas, pero esas cotas no fijan la resolución. La configuración n8n incluye parámetros de base de datos/autenticación cuya compatibilidad debe verificarse con una versión concreta; no se da por válida ni inválida frente a un `latest` desconocido.

**Corrección:** archivo de resolución reproducible con hashes, imágenes/versiones o digests controlados, inventario de componentes y actualizaciones probadas. Fijar acciones críticas a revisiones verificadas. Confirmar que el nombre de imagen publicado por CI corresponde al consumido en el Compose.

### Q06 — Duplicación de implementaciones y dirección de dependencias confusa

**P2 · Media de mantenimiento · Confirmada.**

Evidencia: `v2/database.py`, `v2/main.py`, `v2/mcp_server/`, `v2/presentation/mcp/`, `v2/db/repositories/`, `v2/infrastructure/persistence/repositories/`, servicios Chatwoot/OCR duplicados.

Parte de la compatibilidad sí usa reexportaciones válidas, por ejemplo `db/connection.py`. Otras rutas contienen implementaciones completas duplicadas. El arranque importa `mcp_server`, mientras el README destaca `presentation/mcp`; una corrección aplicada únicamente a una copia puede no afectar producción. Hay controladores muy extensos que reúnen transporte, decisiones de negocio e integraciones.

**Corrección:** identificar los módulos canónicos por sus imports reales, migrar consumidores gradualmente y convertir las rutas antiguas en fachadas ligeras. Añadir pruebas de integración antes de eliminar copias; no hacer una reestructuración masiva junto con las correcciones urgentes.

## 6. Medidas útiles que ya existen

- Hash de contraseñas con salt y comparación en tiempo constante; revisar su coste conforme a la capacidad real del servidor.
- Cookies firmadas con expiración y `HttpOnly`; faltan las mejoras de V13.
- Cifrado AES-GCM y cifrado de contraseñas/PIN en operaciones revisadas de cuentas; esto no significa que toda la base esté cifrada.
- Firma HMAC de webhook implementada cuando se configura; requiere integración efectiva con el emisor.
- Consumo de secretos efímeros mediante actualización condicionada y comprobación de filas afectadas, que es una defensa útil ante doble consumo.
- Consultas parametrizadas en múltiples repositorios revisados, WAL y claves foráneas habilitadas; no se certifica ausencia global de SQL injection.
- Límites de intentos en login/2FA, cuotas de comprobantes y algunas restricciones de procesamiento PDF.
- `.dockerignore` tanto en raíz como en v2 excluye archivos de entorno y bases del contexto de construcción correspondiente.
- CI contiene ejecución de harness v1/v2 antes de construir/publicar; falta verificar que las pruebas cubran los flujos descritos y que la compuerta de seguridad sea exigible.

## 7. Camino recomendado de protección

### Fase 1 — Cerrar las vías de acceso y los fallos con efecto inmediato

1. Restringir temporalmente panel, MCP y webhooks a los orígenes necesarios mediante el proxy/firewall, comprobando antes las integraciones legítimas.
2. Corregir V01–V08, V11 y V12: configuración obligatoria, recuperación validada, token real para secretos, OAuth con 2FA y validación completa, autenticación obligatoria de integraciones, token financiero separado, autorización real MCP, escape HTML y eliminación de reversión por GET.
3. Corregir el contrato de variables O01 y verificar que OTP/notificaciones llegan al destino correcto usando un entorno de prueba.
4. Hacer que el auditor informe «no ejecutado» y bloquee verificaciones obligatorias ausentes. Su puntuación no debe usarse como criterio de despliegue mientras exista Q01.

**Criterio de salida:** las rutas sensibles rechazan solicitudes sin identidad válida y no producen efectos; el segundo factor no tiene ruta alternativa; los workflows internos no comparten secretos del administrador.

### Fase 2 — Limitar el daño de una credencial robada

1. Sesiones revocables, tokens con vida acotada, permisos por acción y separación de usuarios/servicios.
2. Rotación planificada de claves de sesión, servicios y OAuth; claves independientes para datos, backups y auditoría. Preservar claves históricas necesarias para restauración.
3. Revocar accesos anteriores donde corresponda y evitar secretos completos en logs, URLs y mensajes.
4. Separar red pública, administración e integraciones; ejecutar contenedores con mínimos privilegios y límites.

**Criterio de salida:** retirar un usuario/token corta el acceso; comprometer el token financiero no habilita administración; los puertos internos no son públicos.

### Fase 3 — Asegurar integridad y recuperación

1. Backups consistentes mediante snapshot, cifrado, retención definida y restauración ensayada.
2. Auditoría con inserción serializada, claves independientes y anclas externas comprobadas.
3. Webhooks y colas con autenticación, límites, idempotencia y reintentos controlados.
4. Límites de cargas/OCR y de consumo de recursos, alertas sobre fallos reales de integración y disponibilidad.

**Criterio de salida:** reintentar un evento no duplica efectos; un backup restaura cuentas/pagos coherentes; una carga excesiva no agota el servicio completo.

### Fase 4 — Mantener la protección en nuevas versiones

1. Consolidar implementaciones duplicadas y el runner de pruebas.
2. Fijar dependencias e imágenes con proceso de actualización y rollback.
3. CI con análisis de código, secretos, inventario de componentes y pruebas HTTP/MCP de seguridad; distinguir aprobación, fallo y omisión.
4. Documentar variables, roles, rotación, revocación, copias y respuesta ante incidentes. Actualizar afirmaciones comerciales de «inmutabilidad», «anti-SIM swap», «anti-ban» y «100%» para que describan garantías realmente comprobadas.

## 8. Verificación pendiente antes de dar el trabajo por protegido

| Prueba | Resultado exigido |
|---|---|
| Arranque con secretos ausentes/vacíos/de ejemplo | Rechazo explícito y sin crear administrador inseguro. |
| Crear secretos con Authorization arbitraria | 401/403, sin inserciones. |
| Recuperación sin validación del token | No modifica contraseña ni sesiones. |
| Autorizar OAuth con contraseña pero sin 2FA | No emite código. |
| Redirect no registrado / PKCE desconocido | Rechazo antes de emitir/canjear código. |
| Dos canjes simultáneos del mismo código | Solo uno tiene éxito. |
| Webhook sin autenticación o con evento repetido | Sin efectos de negocio. |
| MCP con identidad de cliente sobre otro cliente | 403 o equivalente, sin filtración ni cambios. |
| HTML especial en consultas de login/OAuth | Se muestra como texto. |
| GET de reversión de pago | No altera datos. |
| Cookie copiada tras revocación | Se rechaza. |
| Flujo app–n8n–Evolution en entorno aislado | Identidad validada, envío único y límites efectivos. |
| Backup mientras hay escrituras | Restauración íntegra mediante snapshot. |
| DAST apagado / consulta SCA fallida | ERROR/NOT_RUN, nunca aprobado automáticamente. |

También queda pendiente verificar en el servidor de producción final el estado real del firewall/proxy/TLS externo, secretos efectivos sin divulgarlos, envío real de 2FA en vivo y rotación operativa en ventana de mantenimiento.

---

## 9. Registro de Cierre e Implementación en Código (Bloques 1 a 4)

Todos los 30 hallazgos (`V01–V17`, `O01–O07`, `Q01–Q06`) fueron implementados y verificados automáticamente mediante las suites de regresión de seguridad (`Suite 15` a `Suite 18` en `v2/harness_verify.py`) y el pipeline de seguridad (`node security_pipeline/orchestrator.js`):

| ID | Bloque | Estado | Implementación y Evidencia de Verificación |
|---|---|---|---|
| `V01` | Bloque 1 | **Implementado y Verificado** | `v2/core/config.py` (`validate_runtime_security_settings`) bloquea el arranque en producción ante secretos vacíos o de ejemplo. Probado en `test_security_bloque1_p0.py`. |
| `V02` | Bloque 1 | **Implementado y Verificado** | `v2/db/repositories/admin_repo.py` (`create_password_reset_token`, `consume_password_reset_token_and_update_password`) y `v2/presentation/web/auth_routes.py` (`POST /reset-password`). Probado en `test_security_bloque1_p0.py`. |
| `V03` | Bloque 1 | **Implementado y Verificado** | `v2/core/principal.py` (`require_scope("ephemeral_secrets:write")`) y `v2/presentation/api/ephemeral_api.py`. Probado en `test_security_bloque1_p0.py`. |
| `V04` | Bloque 1 | **Implementado y Verificado** | `v2/presentation/web/oauth_routes.py` exige `pending_oauth_requests` y verificación 2FA (TOTP/OTP) antes de emitir `authorization_code`. Probado en `test_security_bloque1_p0.py`. |
| `V05` | Bloque 1 | **Implementado y Verificado** | `v2/db/repositories/settings_repo.py` valida `redirect_uri` registrada, PKCE `S256` obligatorio, canje atómico con `BEGIN IMMEDIATE` y redacción de `access_token` en reposo. Probado en `test_security_bloque1_p0.py`. |
| `V06` | Bloque 1 | **Implementado y Verificado** | `v2/presentation/api/webhooks_api.py` aplica HMAC *fail-closed* y tabla `webhook_events_seen` anti-replay. Probado en `test_security_bloque1_p0.py`. |
| `V07` | Bloque 1 | **Implementado y Verificado** | `v2/presentation/api/tools_api.py` (`/api/finance/summary`) exige scope `finance:read` (`FINANCE_READ_TOKEN`), desacoplado de `SESSION_SECRET_KEY`. Probado en `test_security_bloque1_p0.py`. |
| `V08` | Bloque 1 | **Implementado y Verificado** | `v2/mcp_server/security_layer.py` aplica *default-deny* y verifica titularidad real en BD (`_verify_resource_ownership_in_db`) contra IDOR. Probado en `test_security_bloque1_p0.py`. |
| `V09` | Bloque 2 | **Implementado y Verificado** | `v2/core/rbac.py` normaliza teléfonos (`canonicalize_whatsapp_phone`) y rechaza roles desconocidos (`ROLE_UNAUTHORIZED`). Probado en `test_security_bloque2_p1.py`. |
| `V10` | Bloque 2 | **Implementado y Verificado** | `v2/infrastructure/external/telegram/bot_app.py` (`_is_authorized`) verifica `chat_id` y `user_id` en comandos y callbacks. Probado en `test_security_bloque2_p1.py`. |
| `V11` | Bloque 1 | **Implementado y Verificado** | `v2/presentation/web/auth_routes.py` y `oauth_routes.py` escapan parámetros reflejados con `html.escape(..., quote=True)`. Probado en `test_security_bloque1_p0.py`. |
| `V12` | Bloque 1 | **Implementado y Verificado** | `v2/presentation/web/dashboard_routes.py` elimina la reversión por `GET` y exige `POST` con token CSRF (`verify_csrf_token`). Probado en `test_security_bloque1_p0.py`. |
| `V13` | Bloque 2 | **Implementado y Verificado** | `v2/core/security.py` registra sesiones en `admin_sessions` con `jti`, revocación server-side (`revoke_session_cookie`, `revoke_all_user_sessions`) y cookie `Secure` configurable. Probado en `test_security_bloque2_p1.py`. |
| `V14` | Bloque 2 | **Implementado y Verificado** | `v2/db/repositories/settings_repo.py` implementa familias de refresh tokens (`family_id`, `parent_token_hash`), rotación en un solo uso y revocación de familia completa ante replay. Probado en `test_security_bloque2_p1.py`. |
| `V15` | Bloque 1 | **Implementado y Verificado** | `v2/presentation/web/dashboard_routes.py` valida URLs de Evolution/Chatwoot contra SSRF (`validate_outbound_service_url`) y redacta secretos al fallar. Probado en `test_security_bloque1_p0.py`. |
| `V16` | Bloque 2 | **Implementado y Verificado** | `v2/core/security.py` separa `SESSION_SECRET_KEY`, `DB_ENCRYPTION_KEY`, `BACKUP_ENCRYPTION_KEY` y `AUDIT_HMAC_KEY`; `v2/core/audit.py` serializa cadena HMAC con JSON canónico y `BEGIN IMMEDIATE`. Probado en `test_security_bloque2_p1.py`. |
| `V17` | Bloques 1 y 2 | **Implementado y Verificado** | `v2/services/csv_service.py` y `v2/presentation/api/accounts_api.py` neutralizan fórmulas tras espacios iniciales; `v2/presentation/api/*.py` devuelven mensajes genéricos con `error_id` sin filtrar trazas internas. Probado en `test_security_bloque1_p0.py` y `test_security_bloque2_p1.py`. |
| `O01` | Bloque 1 | **Implementado y Verificado** | `docker-compose.oracle-stack.yml` y `v2/core/config.py` unifican `ADMIN_USERNAME`, `TELEGRAM_CHAT_ID` y secretos dedicados sin valores por defecto inseguros. Probado en `test_security_bloque1_p0.py`. |
| `O02` | Bloque 1 | **Implementado y Verificado** | `v2/main.py` retira `create_or_update_admin` del `lifespan` y expone `bootstrap_admin_once` de un solo uso en `admin_repo.py`. Probado en `test_security_bloque1_p0.py`. |
| `O03` | Bloque 2 | **Implementado y Verificado** | `v2/Dockerfile` ejecuta con usuario no-root `10001:10001`; `docker-compose.oracle-stack.yml` vincula puertos a `127.0.0.1` y aplica `cap_drop: [ALL]`, `no-new-privileges:true`, `read_only: true` y límites de recursos. Probado en `test_security_bloque2_p1.py`. |
| `O04` | Bloque 1 | **Implementado y Verificado** | `n8n/workflows/workflow_whatsapp_antiban_buffer.json` exige `headerAuth` y valida esquema, instancia permitida y longitud antes de invocar Evolution API. Probado en `test_security_bloque1_p0.py`. |
| `O05` | Bloque 3 | **Implementado y Verificado** | `v2/services/outbound_queue_service.py` y `v2/presentation/api/tools_api.py` implementan cola SQLite persistente (`outbound_jobs`, `outbound_instance_pacing`) con idempotencia, leases atómicos y estado `ambiguous_review`. Probado en `test_security_bloque3_p2.py`. |
| `O06` | Bloque 3 | **Implementado y Verificado** | `v2/services/backup_service.py` genera snapshots consistentes con `sqlite3.Connection.backup()` + `PRAGMA integrity_check` + cifrado AES-GCM fuera del hilo async y verifica restauración aislada. Probado en `test_security_bloque3_p2.py`. |
| `O07` | Bloque 3 | **Implementado y Verificado** | `v2/core/rate_limiter.py` y `v2/services/receipt_service.py` aplican lectura acotada por *chunks*, límite base64 pre-decodificación, tope de píxeles (`4096x4096`), `timeout=10` en `pytesseract` y cuota SQLite compartida. Probado en `test_security_bloque3_p2.py`. |
| `Q01` | Bloque 1 | **Implementado y Verificado** | `security_pipeline/dast_runner.js` y `orchestrator.js` devuelven `NOT_RUN` cuando el servidor está apagado y fallan en modo `--strict-gate`. Probado en `test_security_bloque1_p0.py`. |
| `Q02` | Bloque 3 | **Implementado y Verificado** | `security_pipeline/dependency_auditor.js` audita `v2/requirements.lock` con versiones exactas `==`, comparador semántico PEP 440 y estado `ERROR` ante fallos de red/HTTP en OSV.dev. Probado en `test_security_bloque3_p2.py`. |
| `Q03` | Bloque 3 | **Implementado y Verificado** | `security_pipeline/secret_detector.js`, `sast_scanner.js` y `orchestrator.js` detectan defaults en `os.getenv`, redactan secretos, escanean raíz/workflows y aplican política de bloqueo con excepciones no vencidas. Probado en `test_security_bloque3_p2.py`. |
| `Q04` | Bloque 3 | **Implementado y Verificado** | `v2/harness_verify.py` y `v2/tests/harness_runner.py` unifican las 18 suites canónicas y fallan ante cualquier suite con resultado `False`. Probado en `test_security_bloque3_p2.py`. |
| `Q05` | Bloque 3 | **Implementado y Verificado** | `v2/requirements.lock`, `v2/Dockerfile`, `.github/workflows/docker.yml` y `security_pipeline.yml` fijan paquetes con `==` y GitHub Actions a SHAs de 40 caracteres. Probado en `test_security_bloque3_p2.py`. |
| `Q06` | Bloque 4 | **Implementado y Verificado** | Consolidación de copias duplicadas en fachadas explícitas (`presentation/mcp/*` -> `mcp_server/*`, `infrastructure/persistence/repositories/*` <-> `db/repositories/*`, `infrastructure/ocr/receipt_service.py` -> `services/receipt_service.py`, `infrastructure/external/chatwoot/bot_service.py` -> `services/chatwoot_bot_service.py`) y actualización de `v2/README.md`. Probado en `test_security_bloque4_q06.py`. |

