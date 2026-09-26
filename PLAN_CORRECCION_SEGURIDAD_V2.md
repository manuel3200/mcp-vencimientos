# Plan de corrección de seguridad de StreamVault v2

Fecha: 26 de septiembre de 2026.

Documento complementario a `REVISION_SEGURIDAD_V2_2026-09-26.md`. Conserva sus identificadores: V01–V17, O01–O07 y Q01–Q06 (30 hallazgos).

## Alcance y forma de usar este documento

Este archivo describe **cómo implementaría las correcciones**. No modifica código, datos, configuración, credenciales, workflows ni infraestructura. Ningún comando o ejemplo incluido se ha ejecutado como parte de su elaboración.

Los fragmentos son propuestas de implementación, no parches completos ni código verificado. Los nombres de repositorios, dependencias de autenticación y tablas nuevas que se indican a continuación todavía deben implementarse. No deben copiarse fragmentos aislados a producción: varios dependen de una migración de datos, un contrato común de identidad o cambios coordinados en integraciones.

Las rutas indicadas son relativas a `F:\mcp`. Priorizar la ruta realmente importada por `v2/main.py`; no asumir que una copia bajo `presentation/mcp` es la que ejecuta el servidor.

### Orden recomendado

| Bloque | Trabajo | Dependencias |
|---|---|---|
| 0 | Inventario, copia consistente, entorno de pruebas y pruebas de caracterización | Antes de cambios de datos/claves |
| 1 | V01, V02, V03, V04, V05, V06, V07, V08, V11, V12; O01, O04; Q01 | Cerrar accesos y evitar aprobaciones ficticias |
| 2 | V09, V10, V13, V14, V15, V16, V17; O02, O03 | Sesiones/permisos y secretos independientes |
| 3 | O05, O06, O07; Q02, Q03, Q04, Q05 | Operación y verificación reproducibles |
| 4 | Q06 y documentación final | Con pruebas de regresión ya implementadas |

Las soluciones definitivas de V03/V07 dependen del token de servicio común; V04 depende de una sesión que acredite 2FA; V08 depende de identidad propagada desde la autenticación MCP. Prepararía esos componentes juntos, sin dejar alternativas permisivas mientras se migra.

### Preparación antes de aplicar el plan

1. Identificar commit, imagen y módulos que usa producción; registrar únicamente nombres de variables, nunca sus valores.
2. Preparar entorno aislado con base sintética y destinos de mensajes de prueba. Bloquear envíos a clientes desde ese entorno.
3. Obtener un backup consistente y probar restauración. Conservar de forma protegida las claves necesarias para leerlo.
4. Registrar número de cuentas, pagos, usuarios y filas afectadas para verificar la migración. Usar transacciones e idempotencia.
5. Separar cambios de comportamiento de limpieza de arquitectura. Cada corrección debe incluir su prueba negativa y una prueba del flujo legítimo.
6. No arrancar la aplicación actual contra producción como simple prueba: su inicio puede modificar la contraseña y disparar servicios.

## Contratos comunes propuestos

### Identidad y autorización centralizadas

Una cabecera, un teléfono recibido como argumento o una instrucción al modelo no acreditan identidad. El servidor debe construir un principal después de verificar una sesión/token/evento.

```python
# Propuesta: v2/core/principal.py (archivo futuro)
from dataclasses import dataclass
from typing import FrozenSet

@dataclass(frozen=True)
class Principal:
    subject: str
    kind: str               # human, service, customer
    scopes: FrozenSet[str]
    client_id: int | None = None
    mfa_verified: bool = False

def require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise PermissionError("Acción no autorizada")
```

En HTTP, convertir `PermissionError` a 403 con un manejador controlado. Ausencia de identidad válida es 401. En MCP, mapear al error de protocolo correspondiente sin ejecutar la herramienta. No permitir que el cuerpo de la solicitud construya un `Principal` arbitrario.

### Tokens opacos de servicio

Propuesta de tabla nueva: identificador, hash único del token, sujeto, permisos, expiración, fecha de revocación y creación. El token aleatorio se muestra solo al emitirlo y se entrega al consumidor mediante un almacén de secretos.

```python
import hashlib
import secrets

def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def issue_service_token():
    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)
```

SHA-256 aquí sirve para un token aleatorio de alta entropía. No sustituye el algoritmo lento de hash de contraseñas humanas. La futura función `authenticate_service_token` debe buscar el hash, comprobar expiración/revocación y obtener permisos de la base, sin confiar en permisos enviados por el cliente.

## V01 — Eliminar secretos por defecto y validar el arranque

**Archivos:** `v2/core/config.py`, `v2/main.py` y configuración de despliegue.

**Pasos:**

1. Inventariar consumidores de SESSION, DB, BACKUP, AUDIT, Evolution y OAuth.
2. Retirar defaults utilizables como credenciales. Mantener ejemplos únicamente como instrucciones fuera de valores efectivos.
3. Validar claves obligatorias antes de inicializar base, logger que escriba datos y tareas de fondo. Activar exigencias de cada integración solo cuando esté habilitada explícitamente.
4. Aprovisionar un primer administrador con un flujo separado; no crear `admin` con contraseña fija.
5. Rotar secretos expuestos con el procedimiento V16. Cambiar claves de cifrado sin migrar los datos puede hacerlos ilegibles.

```python
import os

def required_random_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    forbidden = {
        "mcp-super-secret-key-change-in-prod-2026",
        "mcp-evolution-key-2026", "admin123",
    }
    if len(value) < 32 or value in forbidden:
        raise RuntimeError(f"Configuración inválida: {name}")
    return value

# Exigir generación aleatoria mediante el aprovisionamiento.
# La longitud por sí sola no demuestra entropía.
```

**Validación:** falta, vacío y valor de ejemplo detienen arranque sin efectos; claves válidas permiten iniciar. Cookies antiguas no funcionan tras rotación de firma. No escribir el valor rechazado en el error.

## V02 — Recuperación sin cambio inmediato de contraseña

**Archivos:** `presentation/web/auth_routes.py`, repositorio de administradores y migración nueva.

**Pasos:**

1. `/recuperar` solo solicita un desafío, devuelve respuesta genérica y aplica límites por cuenta e IP.
2. Generar token aleatorio, guardar hash, usuario, expiración y consumo. Enviarlo al canal registrado, nunca a un destino recibido en la petición.
3. Presentar formulario HTTPS de nueva contraseña; confirmar por POST, con límites y protección contra automatización.
4. Consumir token y actualizar contraseña en la misma transacción. Revocar sesiones/preautorizaciones/tokens según política.
5. Si falla el envío, no cambiar la contraseña; invalidar el desafío fallido y registrar el incidente.

```sql
-- Esquema propuesto; adaptarlo al tipo de ID real del administrador.
CREATE TABLE password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
```

```python
# Pseudocódigo transaccional: hash_password ya existe en el proyecto.
conn.execute("BEGIN IMMEDIATE")
try:
    row = conn.execute(
        "SELECT username FROM password_reset_tokens "
        "WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
        (token_digest(raw_token), now),
    ).fetchone()
    if row is None:
        raise ValueError("Desafío inválido")
    # Actualizar password_hash/salt del usuario y revocar sesiones aquí.
    conn.execute(
        "UPDATE password_reset_tokens SET used_at=? WHERE token_hash=?",
        (now, token_digest(raw_token)),
    )
    conn.commit()
except Exception:
    conn.rollback()
    raise
```

**Validación:** solicitar o fallar la entrega no cambia el hash; token vencido/reutilizado falla; dos confirmaciones simultáneas solo permiten una actualización.

## V03 — Autenticar realmente la creación de enlaces secretos

**Archivo:** `presentation/web/ephemeral_routes.py`.

1. Eliminar la condición que acepta cualquier `Authorization` no vacía.
2. Aceptar sesión humana completa o token de servicio verificado, ambos con permiso `secrets:create`.
3. Para sesiones de navegador aplicar CSRF. Para servicios aceptar solo el formato bearer previsto.
4. Limitar bytes, cantidad de elementos, TTL y lecturas. Auditar el sujeto autenticado.

```python
# Integración propuesta; las dependencias deben implementarse y probarse.
@router.post("/api/v1/ephemeral-secrets")
async def create_secret(payload: CreateEphemeralSecretRequest,
                        request: Request):
    principal = authenticate_request(request)  # 401 si no es válido
    require_scope(principal, "secrets:create")
    if principal.kind == "human":
        validate_csrf(request)
    validate_secret_limits(payload)
    return persist_secret(payload, actor=principal.subject)
```

**Validación:** Authorization arbitraria, token revocado y permiso incorrecto no insertan filas; el flujo legítimo crea exactamente un secreto.

## V04 — Unificar OAuth con login y 2FA

**Archivo:** `presentation/web/oauth_routes.py`.

1. Retirar la validación directa de usuario/contraseña de `/oauth/authorize`.
2. Guardar una solicitud pendiente de autorización validada en servidor, con TTL y vínculo a sesión/navegador. No usar un `next` externo arbitrario.
3. Redirigir al login y completar 2FA; volver a consentimiento mediante un identificador opaco de solicitud.
4. Exigir marca de MFA creada por el servidor y autenticación reciente. Proteger consentimiento POST contra CSRF.
5. Limitar intentos de autorización/canje y no registrar códigos/tokens.

```python
# Función futura, no una comprobación de un parámetro del formulario.
def require_recent_mfa(session, now: int):
    if not session.mfa_verified_at:
        raise PermissionError("Completar segundo factor")
    if now - session.mfa_verified_at > 600:
        raise PermissionError("Reautenticación requerida")
```

**Validación:** contraseña correcta sin MFA no emite código; completar MFA permite consentir solo la solicitud pendiente y el cliente previamente validado.

## V05 — Validar OAuth y consumir códigos atómicamente

**Archivos:** `presentation/web/oauth_routes.py`, `db/repositories/settings_repo.py`.

1. Definir registro de redirect URIs con comparación exacta y formato inequívoco, preferentemente una lista JSON.
2. Validar cliente habilitado, `response_type=code`, scopes admitidos y redirect antes de renderizar o redirigir, también en rechazo de consentimiento.
3. Para el flujo previsto exigir PKCE S256; rechazar método ausente/desconocido y desafío/verificador inválidos. Comprobar compatibilidad de clientes antes del corte.
4. Al canjear, comprobar cliente, redirect exacto, expiración, uso previo y PKCE dentro de una transacción.
5. Consumir condicionalmente y comprobar una fila modificada. Emitir/persistir tokens en esa misma transacción para evitar estados parciales.
6. Añadir `Cache-Control: no-store` a respuestas de tokens. Usar origen público fijo en discovery; no derivar issuer de cabeceras Host no confiables.

```python
import base64
import hashlib
import hmac
import re

def validate_pkce(verifier: str, challenge: str, method: str):
    if method != "S256":
        raise ValueError("Método PKCE no permitido")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise ValueError("Verificador inválido")
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, challenge):
        raise ValueError("PKCE inválido")

def validate_redirect(uri: str, registered: set[str]):
    if uri not in registered:
        raise ValueError("Redirección no registrada")
```

```sql
-- Dentro de BEGIN IMMEDIATE, tras validar el registro leído.
UPDATE oauth_auth_codes SET used = 1
WHERE code = ? AND used = 0 AND expires_at > ?;
-- cursor.rowcount debe ser 1 antes de emitir tokens.
```

**Validación:** URI parecida pero distinta, URI distinta en canje, scopes desconocidos y PKCE inválido fallan; canje concurrente produce un único juego de tokens. No usar `startswith()` para validar redirects.

## V06 — Webhooks autenticados y resistentes a repetición

**Archivo:** `presentation/api/webhooks_api.py`; configuración del emisor.

1. Declarar integraciones habilitadas. Ruta deshabilitada devuelve 404/503 sin ejecutar lógica; integración habilitada sin credenciales impide arrancar.
2. Elegir y documentar el mecanismo que el emisor realmente soporta: HMAC del cuerpo o token dedicado mediante cabecera. No inventar un formato de firma incompatible.
3. Verificar identidad antes de interpretar remitente, `private` o `fromMe`.
4. Acotar bytes del cuerpo; validar esquema, cuenta e instancia esperada.
5. Persistir ID de evento con unicidad por proveedor/instancia. Registrar aceptación y trabajo pendiente juntos; procesarlo con estado de reintentos. No marcar como completado antes de aplicar sus efectos.
6. Si el proveedor firma timestamp, validar ventana temporal y firma de acuerdo con su protocolo. Si no lo firma, no asumir que un timestamp del cuerpo evita replay.

```python
# Solo aplicable si el emisor firma EXACTAMENTE el cuerpo con HMAC-SHA256.
def verify_body_hmac(raw: bytes, supplied: str, secret: str):
    if not secret:
        raise RuntimeError("Webhook sin configuración")
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    candidate = supplied.removeprefix("sha256=")
    if not hmac.compare_digest(candidate, digest):
        raise PermissionError("Firma inválida")
```

**Validación:** evento real de prueba aceptado, cuerpo alterado rechazado, secreto vacío falla cerrado y reenvío del mismo evento no duplica operaciones.

## V07 — Token financiero de solo lectura

**Archivos:** `presentation/api/finance_api.py`, workflow financiero y despliegue.

1. Retirar comparaciones con contraseña admin, secreto de webhook y clave de sesión.
2. Emitir token opaco según el contrato común con scopes `finance:read` y expiración.
3. Guardarlo como credencial de n8n; no como literal dentro del JSON versionado.
4. Actualizar workflow y backend en una ventana coordinada; verificar y revocar la credencial anterior.

```python
def authorize_finance(request):
    principal = authenticate_request(request)
    require_scope(principal, "finance:read")
    return principal
```

**Validación:** leer balance funciona; escribir pagos o crear secretos con el mismo token falla; contraseñas y claves antiguas dejan de ser válidas para esta API.

## V08 — Autorización en el despachador MCP real

**Archivos:** `main.py`, `mcp_server/instance.py`, herramientas activas y `core/mcp_guard.py`.

1. Inventariar herramientas registradas en la instancia importada por main y sus recursos afectados.
2. Crear política completa: scope requerido, tipo de actor admitido y regla de propiedad por herramienta. Fallar al registrar una herramienta sin política.
3. Propagar el principal autenticado al contexto de ejecución usando el mecanismo soportado por la versión instalada de FastMCP. No usar una variable global compartida entre solicitudes.
4. Consultar en base la propiedad del ID solicitado; si hay búsqueda por nombre/teléfono, limitar también su consulta al cliente autenticado.
5. Verificar autorización inmediatamente antes del caso de uso, sin rutas alternativas que lo eviten. El modelo no puede decidir `is_admin`.
6. Agregar política explícita para acciones masivas/destructivas y registrar el sujeto real.

```python
# Pseudocódigo independiente de la API específica de FastMCP.
POLICIES = {
    "consultar_ficha_cliente": "clients:read",
    "eliminar_cuenta": "accounts:delete",
}

async def authorized_dispatch(name, args, verified_principal):
    scope = POLICIES.get(name)
    if scope is None:
        raise PermissionError("Herramienta sin política")
    require_scope(verified_principal, scope)
    resource = resolve_target_resource(name, args)
    assert_resource_access(verified_principal, resource)
    return await invoke_registered_tool(name, args)
```

**Validación:** ejecutar pruebas por transporte MCP, no solo llamando al guard. Incluir dos clientes, IDs ajenos, consultas amplias, herramientas omitidas y solicitudes paralelas para detectar mezcla de identidad.

## V09 — Roles estrictos e identificadores completos

**Archivo:** `core/rbac.py`.

1. Quitar elevación automática de roles desconocidos y de entradas incompletas.
2. Rechazar la configuración inválida con mensaje que identifique la entrada sin divulgar otros secretos.
3. Normalizar teléfonos con país explícito; no adivinar país ni usar sufijos para autorizar.
4. Aceptar `fromMe` solamente desde eventos ya autenticados de la instancia prevista.

```python
VALID_ROLES = {"SUPER_ADMIN", "FINANZAS", "SOPORTE"}
def validate_configured_role(role: str) -> str:
    canonical = role.strip().upper()
    if canonical not in VALID_ROLES:
        raise ValueError("Rol administrativo inválido")
    return canonical

# Después de normalizar a un formato canónico completo:
role = admin_map.get(canonical_phone, "UNAUTHORIZED")
```

**Validación:** typo, rol ausente y sufijo coincidente de otro país no conceden privilegios. Probar números reales de prueba de los países atendidos para no bloquear usuarios legítimos por normalización.

## V10 — Permisos Telegram por usuario y por chat

**Archivo:** `infrastructure/external/telegram/bot_app.py`.

1. Configurar IDs numéricos permitidos y roles por usuario; no usar nombres públicos de usuario como identidad.
2. Comprobar chat y `from.id` en mensajes; en callbacks usar `callback_query.from.id`, no el emisor del mensaje original del botón.
3. Denegar mensajes sin identidad de usuario verificable, incluidos administradores anónimos si no existe una política explícita para ellos.
4. Aplicar permisos por comando y proteger reutilización de botones/transacciones mediante estado persistente.

```python
def telegram_actor(msg, allowed_chat_ids, user_roles):
    chat_id = str(msg.get("chat", {}).get("id", ""))
    user_id = str(msg.get("from", {}).get("id", ""))
    if chat_id not in allowed_chat_ids or user_id not in user_roles:
        raise PermissionError("Operador no autorizado")
    return user_id, user_roles[user_id]
```

**Validación:** miembro no autorizado del grupo, callback de otro usuario y mensaje anónimo no ejecutan acciones; cada rol legítimo conserva únicamente sus comandos.

## V11 — Escape HTML y CSP gradual

**Archivos:** `presentation/web/auth_routes.py`, `oauth_routes.py`, `core/templates.py`, plantillas y `main.py`.

1. Corrección inmediata: escapar `error`, `msg`, client ID y cualquier texto no confiable antes de insertarlo en HTML.
2. Migrar plantillas a autoescape, manteniendo una distinción explícita entre datos y fragmentos HTML internos. No marcar datos externos como seguros.
3. Auditar atributos y JavaScript por separado: escape HTML no sirve como serialización JavaScript. Para datos JS, usar un mecanismo de JSON seguro del motor de plantillas.
4. Retirar `innerHTML` donde se muestra texto; usar `textContent`.
5. Mover scripts inline a archivos o nonces; probar CSP primero en modo reporte, revisar dependencias legítimas y luego exigirla.

```python
from html import escape
message_html = f'<div class="error-msg">{escape(error or "")}</div>'
# En un atributo HTML, escape(value, quote=True).
```

```javascript
// Propuesta para texto procedente del servidor o un usuario.
statusElement.textContent = message;
```

```text
# Política objetivo orientativa: adaptar a los recursos realmente utilizados.
default-src 'self'; script-src 'self' 'nonce-VALOR_ALEATORIO_POR_RESPUESTA';
object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self';
```

**Validación:** navegador con caracteres HTML y comillas en parámetros/nombres; deben verse como texto. Revisar consola CSP y funcionamiento del dashboard antes de hacerla obligatoria.

## V12 — Reversión solo mediante POST con CSRF

**Archivo:** `presentation/api/accounts_api.py`; formulario/botón correspondiente.

1. Retirar decorador GET. Una ruta GET informativa, si se conserva, nunca realiza la operación.
2. Añadir token CSRF aleatorio ligado a la sesión; entregarlo en formulario o cabecera desde el origen legítimo.
3. Comprobar token y origen esperado en solicitudes de navegador. No confiar solo en SameSite o en Content-Type.
4. Exigir `payments:reverse`, confirmación del pago concreto y una operación transaccional idempotente.

```python
def compare_csrf(supplied: str, expected: str):
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        raise PermissionError("CSRF inválido")

# Decorador futuro: solo POST; validar identidad/permisos/CSRF antes del caso de uso.
@router.post("/api/payments/reverse/{payment_id}")
async def reverse_payment(payment_id: int, request: Request):
    principal = require_human_session(request)
    require_scope(principal, "payments:reverse")
    validate_csrf(request)
    return reverse_payment_once(payment_id, actor=principal.subject)
```

**Validación:** GET 405 o página informativa sin cambios; POST con token faltante/ajeno falla; doble POST no revierte dos veces.

## V13 — Sesiones revocables y cookies seguras

**Archivos:** `core/security.py`, `presentation/web/auth_routes.py`, repositorio/migración de sesiones.

1. Sustituir la cookie con datos firmados autosuficientes por ID opaco aleatorio; persistir solo hash, usuario, expiración, MFA y revocación.
2. Consultar estado de sesión/usuario en cada solicitud; definir límites absoluto e inactivo.
3. Regenerar ID al completar MFA. La cookie de preautorización nunca autoriza APIs.
4. Logout revoca la sesión en servidor. Recuperación/cambio crítico revoca las sesiones del usuario según política.
5. Emitir cookies HTTPS seguras y borrarlas con los mismos atributos de ruta/dominio. Migrar invalidando las cookies antiguas en una ventana comunicada.

```python
response.set_cookie(
    key="__Host-session", value=raw_session_id,
    secure=True, httponly=True, samesite="lax",
    path="/", max_age=3600,
)
# Prefijo __Host- requiere Secure, Path=/ y no incluir Domain.
```

**Validación:** copiar cookie y cerrar sesión hace fallar la copia; cambiar contraseña invalida acceso anterior; preauth no accede a rutas de sesión. Desarrollo debe usar HTTPS o una configuración local explícita separada.

## V14 — Tokens OAuth cortos, revocación y refresh controlado

**Archivos:** repositorio OAuth, middleware MCP, migraciones.

1. Definir duraciones por riesgo: por ejemplo access de 15 minutos y refresh absoluto de 30 días, ajustadas al cliente real. Son decisiones propuestas, no valores obligatorios universales.
2. Guardar hashes de tokens opacos, expiración, revocación, cliente, usuario y familia de refresh.
3. Rotar refresh dentro de una transacción. Conservar tombstone del usado para detectar replay y revocar la familia conforme a política.
4. No extender indefinidamente el vencimiento absoluto al renovar. Coordinar refresh concurrente del cliente para evitar falsas alarmas.
5. Quitar tokens de query string; redactar Authorization y parámetros heredados en logs del proxy.
6. Migrar o revocar tokens existentes; comunicar necesidad de reconectar clientes.

```sql
-- Fragmento orientativo para tabla futura de refresh tokens.
UPDATE refresh_tokens SET used_at = ?
WHERE token_hash = ? AND client_id = ?
  AND used_at IS NULL AND revoked_at IS NULL
  AND expires_at > ? AND family_expires_at > ?;
-- Exigir rowcount=1; insertar sucesor en la misma transacción.
```

**Validación:** expirado/revocado/reutilizado falla; cliente incorrecto falla; URL con access_token no autentica; refresh legítimo mantiene el flujo.

## V15 — Revelado explícito y retención real de secretos

**Archivos:** `presentation/web/ephemeral_routes.py`, `core/ephemeral_secrets.py`, almacenamiento y logs.

1. GET muestra página neutra, sin descifrar ni consumir. Evitar recursos de terceros en ella.
2. Un POST explícito revela el secreto; aplicar controles de origen/desafío vinculados al navegador para evitar consumo inducido, sin presentar esto como prueba de identidad del destinatario.
3. Mantener la actualización condicional que evita doble consumo.
4. Responder sin caché; no registrar token completo ni contenido. Usar ID de registro independiente para auditoría.
5. Al último consumo, borrar lógicamente ciphertext según política; programar purga de expirados. SQLite, WAL y backups pueden conservar copias físicas: documentar ese límite.
6. Si se necesita garantía más fuerte, diseñar cifrado por secreto con destrucción de clave y retención controlada. Un enlace bearer sigue siendo accesible por quien lo posea.

```python
SECRET_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}
# Aplicar tanto a HTML como a JSON y errores de estas rutas.
```

**Validación:** GET repetido no consume; dos POST simultáneos solo revelan según max_views; logs carecen del token; expiración/purga se prueban con reloj controlado.

## V16 — Claves independientes y auditoría consistente

**Archivos:** `core/security.py`, `core/audit.py`, repositorio de auditoría y migraciones.

1. Separar claves SESSION, DB, BACKUP y AUDIT; quitar fallback a una única clave en producción.
2. Añadir versión de clave a ciphertext y firmas, preservando lector del formato histórico durante migración.
3. Descifrar con clave antigua y recifrar por lotes transaccionales; verificar lectura y recuentos; probar restauración antes de retirar una clave antigua.
4. Canonicalizar payload de auditoría con formato determinista, incluyendo campos que se desean proteger como fecha e identificador.
5. Serializar lectura del último hash, firma e inserción en una transacción `BEGIN IMMEDIATE` usando la misma conexión. No llamar repositorios que abran conexiones independientes dentro de esa secuencia.
6. Registrar versión de cadena/formato: cambiar canonicalización sin versionarla rompería la verificación histórica.
7. Publicar anclas a destino separado con credencial que no permita reescribir historial. Detectar también truncación comparando anclas externas.

```python
import json
canonical = json.dumps(
    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
).encode("utf-8")
signature = hmac.new(audit_key, canonical, hashlib.sha256).hexdigest()
```

**Validación:** separadores y Unicode no generan ambigüedad; inserciones concurrentes producen cadena válida; alterar fila o truncar frente a un ancla se detecta; backups anteriores siguen restaurables. HMAC no da no repudio frente a quien posee la clave.

## V17 — Respuestas de error seguras y trazables

**Archivos:** rutas API y manejador global.

1. Sustituir `str(e)` en respuestas, URLs y mensajes del usuario por códigos estables y textos genéricos.
2. Generar identificador de incidente y registrar detalle en destino restringido.
3. Aplicar redacción antes de persistir logs, incluidos errores de clientes HTTP que podrían contener URLs con tokens.
4. Distinguir error de validación esperado (4xx) de fallo interno (5xx).

```python
incident_id = secrets.token_hex(8)
logger.error("Fallo interno id=%s operation=chatwoot", incident_id)
# El diagnóstico detallado iría a un logger protegido con redacción.
return JSONResponse(
    {"error": "internal_error", "incident_id": incident_id},
    status_code=500,
)
```

**Validación:** forzar excepción con datos sintéticos sensibles y comprobar que no aparecen en cuerpo, Location ni logs generales.

## O01 — Contrato único de variables y comprobación del despliegue

**Archivos:** Compose, ejemplo de entorno, config y workflows.

1. Usar `ADMIN_USERNAME` y `TELEGRAM_CHAT_ID` para la app, que son los nombres consumidos actualmente.
2. Pasar los secretos de cada integración habilitada y las claves independientes del plan.
3. Configurar credencial financiera dedicada en n8n; quitar fallback a password/SESSION.
4. Decidir si el buffer es obligatorio. Si lo es, implementar consumidor real y prueba; si no, retirar la declaración/documentación que da por hecho su uso.
5. Validar configuración sin imprimir secretos renderizados. Evitar compartir salida completa de `docker compose config` si contiene valores resueltos.

```yaml
# Fragmento ilustrativo de environment para streamvault-app.
environment:
  ADMIN_USERNAME: ${ADMIN_USERNAME:?Configurar usuario}
  TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID:?Configurar chat administrativo}
  SESSION_SECRET_KEY: ${SESSION_SECRET_KEY:?Configurar clave de sesion}
  # Añadir DB/BACKUP/AUDIT y secretos de integraciones habilitadas.
```

**Validación:** desplegar configuración sintética, comprobar usuario esperado, OTP de prueba y resumen financiero con credencial de solo lectura; variable necesaria ausente debe fallar explícitamente.

## O02 — Separar bootstrap de actualizaciones de administrador

**Archivos:** `main.py`, repositorio admin y futuro comando de administración.

1. Eliminar actualización de contraseña del `lifespan`.
2. Crear comando de bootstrap de un solo uso que falle si el administrador ya existe; solicitar clave sin eco o mediante secreto temporal.
3. Crear comando/flujo explícito de cambio de clave con auditoría y revocación.
4. Limpiar la contraseña de bootstrap del entorno una vez provisionada, coordinando operación.

```python
# Pseudocódigo de bootstrap explícito, no ejecutado al iniciar el servidor.
if get_admin_user(username) is not None:
    raise RuntimeError("Administrador existente; usar cambio de clave")
create_admin_once(username, securely_supplied_password)
```

**Validación:** arrancar/reiniciar no cambia password_hash, salt ni TOTP; bootstrap repetido no sobrescribe usuario.

## O03 — Reducir exposición y privilegios de contenedores

**Archivos:** `v2/Dockerfile`, Compose y configuración del proxy/firewall.

1. Diseñar entrada HTTPS pública y rutas necesarias; panel de administración de n8n por VPN/red privada.
2. Retirar publicación directa de servicios internos o vincular a loopback si el proxy vive en el host.
3. Crear usuario no root y preparar propietarios de volúmenes antes del despliegue. Probar actualización de permisos con backup; no aplicar cambios recursivos indiscriminados.
4. Añadir límites y endurecimiento compatibles con OCR y volúmenes de datos.
5. Verificar salud y reinicio con permisos nuevos.

```dockerfile
# Fragmento futuro al final de la preparación de la imagen.
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
RUN chown app:app /app/data
USER 10001:10001
```

```yaml
# Orientativo; dimensionar mediante pruebas y adaptar al runtime.
user: "10001:10001"
read_only: true
tmpfs:
  - /tmp:size=128m
cap_drop: [ALL]
security_opt: ["no-new-privileges:true"]
pids_limit: 128
mem_limit: 1g
cpus: 1.0
# Mantener /app/data como volumen escribible.
# Si corresponde proxy en host: ports: ["127.0.0.1:8000:8000"]
```

**Validación:** probar login, OCR, logs, SQLite y salud como no-root; verificar desde fuera que solo se exponen los puertos previstos.

## O04 — Proteger la entrada n8n de envío WhatsApp

**Archivo:** workflow del buffer y credenciales/configuración n8n.

1. Configurar autenticación de cabecera con credencial dedicada en el nodo Webhook de la versión instalada.
2. No incluir secretos en el JSON exportado. Coordinar el emisor con la misma credencial.
3. Validar esquema, instancia permitida, destinatarios autorizados, tamaño y cuota antes del despacho.
4. Rechazar campos extra que pretendan cambiar URL/base/credenciales del proveedor.
5. Probar con el formato real de entrada del nodo. No asumir si el payload está en `$json` o `$json.body` sin inspeccionar una ejecución de prueba.

```json
{
  "recipient": "NUMERO_DE_PRUEBA_CANONICO",
  "message": "Mensaje de prueba",
  "idempotency_key": "identificador-unico-de-operacion"
}
```

La instancia debe derivarse de configuración o de una allowlist vinculada al emisor, no aceptarse libremente del cuerpo.

**Validación:** sin cabecera/credencial incorrecta no se alcanza Evolution; instancia ajena y tamaño excesivo se rechazan; rotar la credencial corta la anterior.

## O05 — Cola persistente con ritmo por instancia

**Componentes:** recepción del buffer, persistencia, worker de despacho y respuestas.

1. Persistir una tarea con clave idempotente única por emisor y operación. Responder 202 con ID después de confirmar la escritura.
2. Worker reclama tareas atómicamente con lease/expiración y limita envíos por instancia.
3. Reservar un único slot temporal de envío por instancia; varias tareas no deben superar juntas la cadencia.
4. Guardar respuesta del proveedor y estado. Reintentar solo errores recuperables con backoff y máximo de intentos.
5. Manejar resultado ambiguo: si el proveedor aceptó y se perdió la respuesta, consultar estado cuando sea posible antes de repetir. No prometer exactamente una entrega si el proveedor no ofrece idempotencia.
6. Proporcionar consulta de estado y cola de fallos para revisión.

```sql
-- Esquema conceptual: agregar timestamps, leases y límites reales.
CREATE TABLE outbound_jobs (
    id INTEGER PRIMARY KEY,
    producer TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    instance TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    UNIQUE(producer, idempotency_key)
);
```

**Validación:** ráfaga concurrente respeta límite global por instancia; reinicio conserva pendientes; reintento HTTP de recepción no crea nueva tarea; timeout ambiguo no se reenvía ciegamente.

## O06 — Snapshot SQLite consistente y backup cifrado

**Archivos:** `presentation/api/tools_api.py`, servicios de backup y tareas programadas.

1. Reemplazar descarga del archivo activo por snapshot usando API SQLite.
2. Ejecutar snapshot/cifrado fuera del hilo async de atención para no bloquear el servidor.
3. Proteger temporales y cifrar antes de entregar/subir; limpiar temporal tras completarse la descarga, no antes.
4. Unificar todas las vías de backup, incluidas las enviadas por Telegram, sobre el mismo servicio.
5. Conservar versión de formato y clave; ensayar restauración aislada con comprobaciones de integridad y totales de negocio.

```python
import sqlite3

def create_sqlite_snapshot(source_path: str, destination_path: str):
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(destination_path)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError("Snapshot inválido")
    finally:
        target.close()
        source.close()
# destination_path: archivo temporal privado, nunca ruta suministrada por usuario.
# Cifrar este snapshot mediante el servicio de backup y clave dedicada.
```

**Validación:** durante escrituras, restaurar backup y verificar coherencia; clave incorrecta falla; descarga exige permisos; no quedan snapshots sin cifrar accesibles.

## O07 — Límites de cuerpo, OCR y consumo de recursos

**Componentes:** proxy, middleware ASGI, importaciones, OCR y limitadores.

1. Establecer máximos distintos por endpoint; considerar expansión base64 y no solo bytes del archivo final.
2. Contar bytes reales del stream y cortar antes de almacenarlo completo. `Content-Length` puede faltar o mentir.
3. Para multipart, limitar recepción/parser antes de `UploadFile.read`; leer luego en bloques acotados.
4. Rechazar dimensiones/páginas excesivas y descompresiones peligrosas; OCR en proceso/worker con límite real de tiempo/memoria. Un timeout de coroutine no mata automáticamente un proceso OCR.
5. Persistir cuotas críticas compartidas y caducar claves de limitación. Configurar proxy de confianza para obtener IP real sin aceptar X-Forwarded-For arbitrario.

```python
# Lector orientativo para cuerpo raw; el límite debe aplicarse también
# antes del parser multipart y en el proxy para proteger todas las rutas.
async def bounded_body(request, limit: int) -> bytes:
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="Contenido demasiado grande")
        chunks.extend(chunk)
    return bytes(chunks)
```

**Validación:** requests chunked sin longitud, base64 grande, imagen con dimensiones extremas y OCR lento; servicio sigue respondiendo, devuelve 413/429 según corresponda y no supera recursos previstos.

## Q01 — DAST con estados honestos y compuerta obligatoria

**Archivos:** `security_pipeline/dast_runner.js`, `orchestrator.js`, generador de reportes.

1. Eliminar resultados PASSED fabricados cuando el servidor no responde.
2. Marcar error/no ejecutado y emitir razón; no sumar pruebas omitidas como aprobadas.
3. Iniciar instancia efímera preparada para pruebas y usar endpoints reales.
4. CI debe fallar si un control obligatorio no se ejecutó o falló. Una simulación puede existir, pero separada y sin llamarla DAST exitoso.

```javascript
if (!healthCheck.reachable) {
  return {
    status: 'NOT_RUN', serverOnline: false,
    probesExecuted: 0, probes: [], findings: [],
    error: 'Servidor de pruebas no disponible'
  };
}

// Orquestador: además de políticas de severidad.
if (dast.status === 'NOT_RUN' || dast.status === 'ERROR') {
  process.exitCode = 1;
}
```

**Validación:** servidor apagado nunca produce aprobación; endpoint inesperado/500 no se interpreta como protección correcta; reporte conserva evidencia del resultado real.

## Q02 — Auditar componentes realmente instalados

**Archivo:** `security_pipeline/dependency_auditor.js` y CI.

1. Generar inventario del entorno/imagen resuelta con paquetes transitivos.
2. Consultar vulnerabilidades para esas versiones, no para la cota `>=` del manifiesto.
3. Tratar errores de servicio/red/JSON como verificación incompleta; aplicar reintentos acotados y después fallar la compuerta obligatoria.
4. Comparar versiones con parser adecuado al ecosistema. Separar vulnerabilidad confirmada, no afectado y desconocido.
5. Guardar fecha y procedencia de la consulta sin afirmar vigencia indefinida.

```javascript
// Dentro de queryOsvApi: rechazar, no resolver [] ante error.
if (res.statusCode !== 200) {
  reject(new Error(`Consulta SCA fallida: HTTP ${res.statusCode}`));
  return;
}
// req.on('error', reject); timeout también debe producir ERROR.
```

**Validación:** simular timeout, 500 y JSON inválido produce ERROR; comparar inventario con paquetes instalados; incluir una transitiva vulnerable de prueba sin instalarla en producción.

## Q03 — Ampliar análisis y política de bloqueo

**Archivos:** analizadores, orquestador y CI.

1. Escanear código y configuración de raíz, v2, workflows y cambios de Git; aplicar exclusiones justificadas a fixtures sintéticos, no a contraseñas inseguras por su contenido.
2. Detectar defaults de `os.getenv`, además de asignaciones literales.
3. Complementar regex con análisis de flujos y pruebas de autorización; los scanners no sustituyen esas pruebas.
4. Redactar secretos en reportes; ante secreto real confirmado, revocar antes de limpiar historial con un procedimiento separado.
5. Definir controles obligatorios y excepciones con responsable/caducidad. No aprobar por ausencia de críticos en una sola herramienta.

```javascript
const required = [sast, secrets, sca, dast];
const incomplete = required.some(r => ['ERROR', 'NOT_RUN'].includes(r.status));
const blocking = required.some(r => r.findings.some(f =>
  ['CRITICAL', 'HIGH'].includes(f.severity) && !hasValidException(f)
));
process.exitCode = incomplete || blocking ? 1 : 0;
// hasValidException debe consultar excepciones revisadas, no un flag del finding.
```

**Validación:** fixtures de defaults inseguros son detectados; salida redactada; fallo del scanner bloquea y excepción vencida no permite aprobar.

## Q04 — Unificar harness y probar las fronteras reales

**Archivos:** `v2/harness_verify.py`, `v2/tests/harness_runner.py`, suites y README.

1. Conservar un único punto de ejecución y discovery/lista canónica; si hay wrapper, que solo delegue al runner principal.
2. Crear fixtures de base temporal, claves sintéticas y proveedores mock; activar la aplicación con lifespan controlado sin tareas/envíos reales.
3. Probar HTTP y MCP además de funciones: autenticación, MFA, CSRF, propiedad de recursos y ausencia de efectos al denegar.
4. Incluir concurrencia en canje OAuth, refresh, pago, auditoría y consumo de secretos.
5. Emitir recuentos reales de tests, fallos y omisiones; no prometer duración fija.

```python
# Ejemplo de contrato de prueba; client y db son fixtures futuras aisladas.
def test_secret_creation_rejects_fake_authorization(client, db):
    before = db.count_ephemeral_secrets()
    response = client.post(
        "/api/v1/ephemeral-secrets",
        headers={"Authorization": "Bearer valor-invalido"},
        json={"title": "Prueba", "items": []},
    )
    assert response.status_code in (401, 403)
    assert db.count_ephemeral_secrets() == before
```

Antes de usar ese fixture, adaptar el payload para que sea válido según el modelo real: un 422 de esquema no demuestra rechazo de autenticación.

**Validación:** introducir temporalmente un fallo controlado en una rama de pruebas debe hacer fallar la compuerta correspondiente; revertirlo antes de entregar. Todos los hallazgos críticos tienen prueba negativa y positiva.

## Q05 — Dependencias, imágenes y acciones reproducibles

**Archivos:** requirements, archivo de resolución futuro, Dockerfile, Compose y CI.

1. Resolver dependencias en entorno limpio con versión de Python elegida y generar lock/hashes de directas y transitivas.
2. Verificar instalación tanto AMD64 como ARM64; no suponer que un lock de una plataforma cubre todas.
3. Instalar desde resolución comprobada y generar inventario de imagen final.
4. Publicar etiqueta inmutable por commit y consumir digest aprobado. Mantener imagen anterior para rollback compatible con la base.
5. Fijar acciones CI a SHA verificado y establecer actualizaciones revisadas.
6. Probar compatibilidad de n8n/Evolution con versión exacta, incluidos DB_TYPE, autenticación, firmas y expresiones de workflows; actualizar documentación acorde.

```text
# Ejemplo conceptual, no un digest real para copiar:
ghcr.io/ORGANIZACION/REPOSITORIO@sha256:DIGEST_VERIFICADO_DE_LA_IMAGEN
```

No se incluyen versiones «seguras» inventadas: deberán determinarse con el inventario y consultas vigentes al implementar.

**Validación:** dos builds del mismo origen resuelven los mismos paquetes; imagen publicada coincide con la desplegada; pruebas pasan en ambas arquitecturas y una actualización puede revertirse sin perder datos.

## Q06 — Consolidar copias sin romper compatibilidad

**Áreas:** `mcp_server`/`presentation/mcp`, `db/repositories`/`infrastructure/persistence/repositories`, servicios duplicados.

1. Dibujar grafo de imports desde main, scheduler, Telegram y tools; identificar qué implementación se ejecuta.
2. Elegir módulo canónico por componente. Aplicar primero las correcciones críticas a la ruta activa.
3. Añadir pruebas de contratos actuales y migrar un componente por cambio.
4. Convertir la ruta histórica en reexportación explícita; evitar ejecutar decoradores de registro MCP dos veces.
5. Actualizar mocks, imports, tests y documentación; retirar fachada solo cuando no tenga consumidores.
6. Extraer lógica de negocio de controladores extensos después de estabilizar cobertura y seguridad.

```python
# Ejemplo de fachada futura: solo tras elegir/verificar la ruta canónica.
from infrastructure.persistence.repositories.clients_repo import (
    get_client_by_phone,
    register_or_update_client,
)

__all__ = ["get_client_by_phone", "register_or_update_client"]
```

**Validación:** funciones históricas y nuevas apuntan a la misma implementación; número/nombres de herramientas MCP se mantienen sin duplicados; pruebas de negocio y seguridad siguen pasando.

## Despliegue de las correcciones cuando se autorice su implementación

1. Implementar y revisar por bloques, con migraciones versionadas y pruebas en entorno aislado.
2. Ensayar actualización desde una copia sintética representativa del esquema actual, no solo una base nueva.
3. Preparar rollback de aplicación y plan de recuperación de datos. Una migración o rotación puede impedir volver a una imagen anterior: documentar el punto de no retorno antes del despliegue.
4. Comunicar sesiones/conexiones que deberán renovarse y coordinar credenciales de integraciones en ambos extremos.
5. Aplicar cambios en ventana controlada; verificar salud, login/2FA, MCP, webhook legítimo, financiero, colas y backup con destinos de prueba.
6. Observar errores de autorización, fallos de firmas, backlog y recursos; nunca volver a defaults inseguros para resolver una incidencia.
7. Actualizar el informe original solo después de aportar evidencia de cierre; marcar cada hallazgo como pendiente, implementado, verificado o riesgo aceptado con responsable.

## Criterios de finalización

- Los 30 identificadores tienen cambio concreto o decisión documentada; «existe una función» no cuenta como integración verificada.
- Ningún flujo alternativo evita MFA, permisos por recurso o autenticación de servicios.
- Denegaciones de acceso no producen escrituras, envíos ni lectura de datos ajenos.
- Las migraciones preservan datos y las claves permiten restaurar los backups requeridos.
- CI distingue pruebas aprobadas, fallidas y no ejecutadas; la imagen desplegada corresponde a la verificada.
- Se completan pruebas del sistema real en entorno aislado. Los ejemplos de este documento, por sí solos, no demuestran una corrección aplicada.

**Estado de este documento:** Plan de corrección 100% implementado y verificado en código a través de los Bloques 1, 2, 3 y 4 (`V01–V17`, `O01–O07`, `Q01–Q06`), con cobertura automatizada en las suites 15 a 18 de `v2/harness_verify.py` y auditoría del pipeline en `security_pipeline/orchestrator.js`.

