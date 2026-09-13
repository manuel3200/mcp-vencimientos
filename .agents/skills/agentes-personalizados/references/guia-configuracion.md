# Guía de Configuración Técnica: Agentes Personalizados

Esta guía detalla la especificación de configuración, campos del frontmatter, políticas de seguridad y ciclo de vida de los agentes personalizados en Antigravity 2.0 y la CLI (`agy`).

---

## 1. Campos del Frontmatter YAML

| Propiedad | Tipo | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `name` | `string` | *(Obligatorio)* | Nombre identificador único. Debe estar en minúsculas y usar guiones (`kebab-case`). |
| `description` | `string` | *(Obligatorio)* | Descripción detallada de capacidades y casos de uso. El agente coordinador la usa para decidir la delegación. |
| `tools` | `string[]` | `[]` | Lista explícita de herramientas permitidas. |
| `mainAgent` | `boolean` | `true` | Habilita el agente como agente principal en el selector de la UI y en la CLI. |
| `subagent` | `boolean` | `true` | Permite que el agente sea invocado programáticamente por un agente padre. |
| `model` | `string` | `inherit` | Nivel o modelo de IA (`inherit`, `flash`, `pro`). |
| `commandExecutionPolicy` | `string` | `sandbox` | Política de ejecución de comandos en terminal (`auto`, `sandbox`, `eager`, `off`). |
| `permissionMode` | `string` | - | Nivel de permisos de edición (`acceptEdits`, `bypassPermissions`, etc.). |
| `mcpServers` | `object[]` | `[]` | Servidores MCP accesibles de manera exclusiva para este agente. |
| `skills` / `plugins` | `string[]` | `[]` | Rutas a habilidades o plugins específicos que este agente puede cargar. |

---

## 2. Diferenciadores Clave de Antigravity

### 2.1 Simetría Total de Ejecución (`mainAgent` vs. `subagent`)
A diferencia de otros entornos donde los agentes personalizados sólo pueden ser subagentes ocultos:
- **Como Agente Principal (`mainAgent: true`)**: Puedes iniciar sesión directamente con él desde el selector de la interfaz gráfica de Antigravity 2.0 o mediante el comando de terminal:
  ```bash
  agy --agent <nombre-del-agente>
  ```
  En este modo, sus instrucciones constituyen el prompt del sistema y se aplican sus parámetros de ejecución directamente.
- **Como Subagente (`subagent: true`)**: Puede ser instanciado de forma asíncrona por un agente coordinador para realizar tareas aisladas en paralelo.

### 2.2 Política de Ejecución de Comandos (`commandExecutionPolicy`)
Para evitar la frustración de confirmaciones constantes en tareas automatizadas:
- **`auto`**: Permite ejecutar comandos de desarrollo y pruebas no destructivos de forma autónoma en segundo plano, manteniendo protegidas las operaciones críticas (como borrado masivo o modificación del sistema).
- **`sandbox`**: Ejecuta las operaciones en un entorno aislado con verificaciones de límites.
- **`off`**: Requiere aprobación interactiva para cualquier ejecución en consola.

### 2.3 Curación de Habilidades y Herramientas Acotadas
En lugar de cargar todas las herramientas y MCPs del espacio de trabajo:
- Especificar únicamente las `tools` requeridas reduce la posibilidad de alucinación y selección errónea de herramientas.
- Asociar únicamente las `skills` pertinentes mantiene el consumo de tokens bajo y el foco en la tarea concreta.

---

## 3. Ciclo de Vida y Estados del Subagente

Cuando un subagente es ejecutado en segundo plano:

1. **Running (En Ejecución):**
   - El subagente ejecuta sus herramientas, analiza código y procesa información.
   - Puede ser detenido manualmente por el usuario o interrumpido por el agente padre.
2. **Idle (Inactivo / En Espera):**
   - Una vez finalizada su tarea, envía el resultado al agente padre y se suspende.
   - **Retención de contexto:** Si recibe un nuevo mensaje del agente padre o de un par, se reactiva conservando todo el contexto de sus turnos anteriores.
3. **Killed (Finalizado):**
   - El agente termina definitivamente.
   - Los árboles de trabajo temporales de Git (*worktrees*) se limpian automáticamente.
   - Los registros de conversación permanecen accesibles en el historial JSONL.

---

## 4. Límites de Anidamiento y Seguridad

- **Límite de profundidad de anidamiento:** Se permite un máximo estricto de **10 niveles** de subagentes anidados para prevenir recursiones infinitas y agotamiento de recursos.
- **Herencia de permisos:** Los subagentes heredan los prefijos de comandos de terminal permitidos por el agente padre, las carpetas de lectura/escritura y las directivas de seguridad.
- **Escalación de permisos (*Permission Bubbling*):** Si un subagente requiere una acción de alto impacto no autorizada previamente, la solicitud escala a la interfaz del usuario.
