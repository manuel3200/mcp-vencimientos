# StreamVault v2 - Modular CRM, Financial & Messaging Platform

StreamVault Versión 2 (`v2`) implementa una arquitectura modular por capas con separación explícita entre módulos canónicos y fachadas de compatibilidad regresiva (`Q06`), controles de seguridad *fail-closed* (`V01–V17`), operación reproducible (`O01–O07`) y verificación automatizada mediante **Harness Engineering** (`Q01–Q06`).

---

## 🏛️ Mapa de Módulos Canónicos y Fachadas (`Q06`)

Para evitar divergencia entre rutas históricas y rutas activas, cada subsistema tiene una **única fuente de verdad (SSOT)** y las rutas alternativas actúan exclusivamente como fachadas ligeras de reexportación (`from <canonico> import *`):

```
v2/
├── core/                              # Seguridad, configuración, identidad y utilidades transversales
│   ├── config.py                      # Contrato de variables de entorno y validación fail-closed (V01, O01)
│   ├── security.py                    # PBKDF2-SHA256, AES-256-GCM con separación de claves y sesiones revocables (V13, V16)
│   ├── principal.py                   # Principal autenticado, Service Tokens con scopes y CSRF (V03, V07, V12)
│   ├── rbac.py                        # Control de acceso basado en roles y teléfono canónico (V09)
│   ├── audit.py                       # Cadena de auditoría tamper-evident con JSON canónico y BEGIN IMMEDIATE (V16)
│   ├── rate_limiter.py                # Lectura acotada de streams HTTP, IP de proxy confiable y cuota SQLite (O07)
│   └── ephemeral_secrets.py           # Secretos efímeros de un solo uso con consumo atómico
│
├── domain/                            # Reglas de negocio (entidades, precios escalonados y parser HWID)
├── application/                       # Casos de uso (cuentas, pagos, clientes, canales y comunidad)
│
├── db/                                # Capa de acceso a datos (Canónica para repositorios principales)
│   ├── connection.py                  # Fachada hacia infrastructure.persistence.connection
│   ├── schema.py                      # Fachada hacia infrastructure.persistence.schema
│   └── repositories/                  # CANÓNICO: accounts, admin, catalog, clients, fallen_reports,
│                                      # finance, groups_moderation, payments_approval, settings, suppliers
│                                      # (audit_repo y growth_repo reexportan desde infrastructure/persistence/repositories/)
│
├── infrastructure/                    # Adaptadores técnicos de persistencia y mensajería externa
│   ├── persistence/
│   │   ├── connection.py              # CANÓNICO: Conexión SQLite con WAL, busy_timeout y foreign_keys
│   │   ├── schema.py                  # CANÓNICO: Esquema DDL e inicialización idempotente (37 tablas)
│   │   └── repositories/              # CANÓNICO para audit_repo y growth_repo; fachada hacia db.repositories.* en el resto
│   ├── external/
│   │   ├── evolution_whatsapp/        # Cliente Evolution API con SSRF allowlist (V15)
│   │   ├── telegram/                  # Bot administrativo con verificación estricta de chat_id/user_id (V10)
│   │   └── chatwoot/                  # client.py + bot_service.py (fachada hacia services.chatwoot_bot_service)
│   └── ocr/                           # receipt_service.py (fachada hacia services.receipt_service)
│
├── services/                          # Servicios de aplicación e infraestructura operativa (CANÓNICOS)
│   ├── receipt_service.py             # CANÓNICO: OCR de comprobantes con límites base64, píxeles y timeout (O07)
│   ├── chatwoot_bot_service.py        # CANÓNICO: Bot omnicanal Chatwoot
│   ├── outbound_queue_service.py      # CANÓNICO: Cola saliente persistente con leases y ritmo por instancia (O05)
│   ├── backup_service.py              # CANÓNICO: Snapshot SQLite en caliente + cifrado AES-GCM + verificación (O06)
│   ├── csv_service.py                 # Exportación/importación CSV con sanitización de fórmulas (V17)
│   ├── finance_service.py             # Balance financiero consolidado
│   └── template_service.py            # Renderizado dinámico de plantillas de WhatsApp
│
├── mcp_server/                        # Servidor FastMCP Canónico (importado por main.py)
│   ├── models.py                      # CANÓNICO: Esquemas Pydantic de herramientas MCP
│   ├── security_layer.py              # Guard runtime default-deny y verificación de titularidad en BD (V08)
│   └── tools/                         # CANÓNICO: account, catalog, client, finance, group, system tools
│
├── presentation/                      # Transporte HTTP (Web Dashboard, REST API, OAuth 2.0 y Fachada MCP)
│   ├── web/                           # auth_routes.py, dashboard_routes.py, oauth_routes.py, view_models.py
│   ├── api/                           # accounts_api, catalog_api, suppliers_api, tools_api, webhooks_api
│   └── mcp/                           # Fachada de compatibilidad hacia mcp_server/* (Q06)
│
├── tests/                             # 18 suites de regresión de negocio, arquitectura y seguridad
├── harness_verify.py                  # Runner canónico de verificación en entorno temporal aislado (Q04)
├── requirements.txt                   # Manifiesto de dependencias directas
├── requirements.lock                  # Lockfile reproducible con versiones exactas '==' auditadas (Q02, Q05)
└── main.py                            # Entrypoint FastAPI con validación fail-closed en arranque
```

---

## 🔒 Garantías Técnicas de Seguridad y Alcance Real

Las siguientes medidas técnicas están implementadas y verificadas por pruebas automatizadas (evitando afirmaciones absolutas no verificables):

1. **Configuración *Fail-Closed* y Separación de Claves (`V01`, `V16`, `O01`, `O02`)**:
   - El arranque en producción (`validate_runtime_security_settings`) rechaza secretos vacíos, débiles o valores de ejemplo conocidos.
   - El inicio del servidor no sobrescribe contraseñas de administradores existentes; el aprovisionamiento inicial utiliza `bootstrap_admin_once`.
   - Claves independientes por propósito criptográfico: `SESSION_SECRET_KEY` (cookies de sesión), `DB_ENCRYPTION_KEY` (credenciales de cuentas y tokens en reposo), `BACKUP_ENCRYPTION_KEY` (snapshots SQLite) y `AUDIT_HMAC_KEY` (cadena de auditoría).

2. **Autenticación, Sesiones Revocables, OAuth 2.0 + 2FA y CSRF (`V02–V05`, `V07`, `V12–V14`)**:
   - Recuperación de contraseña mediante token aleatorio de un solo uso (hash SHA-256 con expiración de 15 min) consumido atómicamente en `POST /reset-password`.
   - Sesiones web registradas en `admin_sessions` con `jti`, revocables individual o globalmente en el servidor, y protegidas con cookie/cabecera CSRF de doble envío.
   - Flujo OAuth 2.0 con `redirect_uri` exacta pre-registrada, PKCE `S256` obligatorio, segundo factor (TOTP/OTP) obligatorio antes de emitir `authorization_code`, canje atómico de un solo uso y rotación de *refresh tokens* por familia (`family_id`) con revocación completa ante detección de reutilización.
   - Tokens de servicio con alcances granulares (`ephemeral_secrets:write`, `finance:read`, `mcp:invoke`, `outbound_queue:write`) distintos de la sesión del administrador.

3. **Autorización de Integraciones, MCP y Bot (`V06`, `V08`, `V09`, `V10`, `V15`)**:
   - Webhooks de WhatsApp/Evolution y Chatwoot verifican firma HMAC en modo *fail-closed* y registran `event_key` en `webhook_events_seen` contra ataques de repetición.
   - El guard MCP (`mcp_server/security_layer.py`) aplica política *default-deny* para herramientas no clasificadas y valida en base de datos que el recurso (`account_id`, `client_id`, `email`) pertenezca al teléfono autenticado antes de ejecutar herramientas de cliente.
   - Los comandos y callbacks de Telegram validan tanto `chat_id` como `user_id` contra listas autorizadas.

4. **Cadena de Auditoría *Tamper-Evident* (`V16`)**:
   - Los eventos críticos se insertan mediante transacciones `BEGIN IMMEDIATE` en `audit_logs`, encadenando firmas `HMAC-SHA256` sobre una tupla JSON canónica (`json.dumps([prev_hash, actor, action, ...])`). Esto permite detectar alteraciones o inserciones fuera de orden en la base de datos mediante `verify_audit_chain()`, aunque la inmutabilidad física frente a un atacante con acceso de escritura al host requiere anclar periódicamente el último hash en un sistema externo de solo anexado.

5. **Cola Persistente con Ritmo por Instancia (`O05`)**:
   - El envío saliente cuenta con una cola SQLite persistente (`outbound_jobs` y `outbound_instance_pacing`) con claves de idempotencia `(producer, idempotency_key)`, reserva atómica de *leases* y espaciado temporal por instancia. Ante errores de red ambiguos tras enviar la petición al proveedor, el trabajo pasa a `ambiguous_review` para evitar reenvíos automáticos duplicados. Este control regula la cadencia de salida propia, pero no garantiza inmunidad frente a políticas externas de WhatsApp/Meta.

6. **Snapshots SQLite Consistentes y Límites de Recursos (`O06`, `O07`)**:
   - Los respaldos se obtienen en caliente mediante `sqlite3.Connection.backup()` fuera del *event loop* asíncrono, se validan con `PRAGMA integrity_check` y se cifran con `BACKUP_ENCRYPTION_KEY`.
   - Las entradas HTTP y OCR aplican lectura acotada por *chunks* (`read_bounded_body`), límite de longitud base64 previo a decodificar, verificación de dimensiones máximas de imagen (`<= 4096x4096`, `16 MP`) y `timeout` de subproceso en `pytesseract`.

---

## 🛡️ Verificación Automática (18 Suites Canónicas + Security Pipeline)

Para ejecutar las **18 suites canónicas** en una base de datos temporal aislada:

```bash
python v2/harness_verify.py
```

Para ejecutar el pipeline integral de seguridad (`SAST`, `Secret Detector`, `SCA` contra `v2/requirements.lock` y `DAST`):

```bash
node security_pipeline/orchestrator.js
```

En CI/CD o pre-despliegue estricto (donde `NOT_RUN` o `ERROR` en cualquier escáner bloquea la compuerta con código de salida distinto de cero):

```bash
node security_pipeline/orchestrator.js --strict-gate
```
