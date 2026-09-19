# StreamVault v2 - High Performance Modular CRM & Financial Platform

StreamVault Versión 2 (`v2`) representa la evolución arquitectónica completa del sistema, pasando de una estructura monolítica a **Clean Architecture + BFF (Backend-for-Frontend)** y **Harness Engineering**.

---

## 🏛️ Arquitectura Modular de StreamVault v2

```
v2/
├── core/                              # Configuración global, seguridad y utilidades transversales
│   ├── config.py                      # Variables de entorno y rutas seguras
│   ├── security.py                    # PBKDF2-SHA256, sesiones HMAC y 2FA/TOTP
│   ├── utils.py                       # Formateador ARS, sanitización telefónica
│   └── templates.py                   # Renderizador de plantillas HTML
│
├── domain/                            # REGLAS PURAS DE NEGOCIO (0 dependencias externas)
│   ├── entities/                      # Client, Account, Payment, CatalogItem
│   ├── rules/                         # Precios escalonados (VIP $3500, Revendedor $4500, Final $8000), HWID
│   └── exceptions.py                  # Excepciones semánticas del negocio
│
├── application/                       # CASOS DE USO Y ORQUESTACIÓN
│   ├── accounts/                      # SellAccount, RenewAccount, RotatePassword, FallenAccount
│   ├── payments/                      # ApprovePayment, RejectPayment, BulkApprove
│   ├── clients/                       # Client 360 y ficha consolidada
│   └── http_custom/                   # Procesamiento de altas y renovaciones automáticas por HWID
│
├── infrastructure/                    # IMPLEMENTACIONES TÉCNICAS Y CONTROLADORES EXTERNOS
│   ├── persistence/                   # Conexión SQLite WAL y repositorios tipados
│   │   ├── connection.py              # Pool de conexiones SQLite con WAL
│   │   ├── schema.py                  # Esquema DDL y migraciones idempotentes
│   │   └── repositories/              # Accounts, Clients, Payments, Catalog, Suppliers, Settings
│   ├── external/                      # Servicios desacoplados de mensajería
│   │   ├── evolution_whatsapp/        # Cliente Evolution API
│   │   ├── telegram/                  # Bot interactivo con polling y notificador
│   │   └── chatwoot/                  # Sincronizador de CRM omnicanal
│   └── ocr/                           # Parser OCR de comprobantes bancarios
│
├── presentation/                      # PRESENTACIÓN, APIS Y BFF
│   ├── web/                           # Controladores web del Dashboard y modales
│   │   ├── dashboard_routes.py        # Rutas HTTP limpias (<150 líneas)
│   │   ├── view_models.py             # Formateo desacoplado de filas y componentes HTML
│   │   ├── auth_routes.py             # Login, logout y verificación 2FA
│   │   └── oauth_routes.py            # Protocolo OAuth 2.0 (RFC 6749)
│   ├── api/                           # Endpoints REST y Webhooks
│   │   ├── accounts_api.py            # API REST de cuentas y stock
│   │   ├── catalog_api.py             # API de precios y combos
│   │   ├── suppliers_api.py           # API de proveedores mayoristas
│   │   ├── tools_api.py               # Herramientas de backup e importación CSV
│   │   └── webhooks_api.py            # Webhooks de WhatsApp y Chatwoot
│   ├── templates/                     # Plantillas HTML optimizadas
│   └── mcp/                           # Servidor FastMCP para Gemini Spark
│
├── scheduler/                         # Tareas en segundo plano (Alertas matutinas a las 09:00 hs)
│   └── task_runner.py                 # Ciclo de vida y disparadores APScheduler
│
├── tests/                             # Test Harness (100% en memoria, aislado)
│   ├── harness_runner.py              # Ejecutor de verificación automática (<0.5s)
│   ├── test_clean_architecture.py     # Verificación de contratos y pureza de capas
│   ├── test_http_custom.py            # Casos de prueba de altas y renovaciones por HWID
│   ├── test_pricing_and_clients.py    # Tarifas VIP, revendedores y preservación de estatus
│   ├── test_payments_approval.py      # Aprobaciones de comprobantes y banderas de notificación
│   └── test_accounts_and_alerts.py    # Ciclo de vida de cuentas y vencimientos
│
├── Dockerfile                         # Imagen Docker multi-arquitectura optimizada
├── requirements.txt                   # Dependencias fijadas y auditadas
└── main.py                            # Entrypoint principal FastAPI con Lifespan
```

---

## 🛡️ Verificación Automática con Harness Engineering

Para verificar el 100% de los contratos del sistema sin tocar la base de datos de producción:

```bash
python v2/harness_verify.py
```

El ejecutor crea una base de datos efímera en memoria/temporal, inicializa las tablas, ejecuta las 5 suites de prueba en menos de 0.5 segundos y limpia los recursos automáticamente.

---

## 🚀 Despliegue y Ejecución

### Local / Desarrollo:
```bash
cd v2
python main.py
```

### Docker / Producción:
El workflow de GitHub Actions (`.github/workflows/docker.yml`) compila automáticamente imágenes multi-arquitectura (`linux/amd64` y `linux/arm64`) tras superar las compuertas de calidad del Harness, y las publica en GitHub Container Registry (`ghcr.io`).
