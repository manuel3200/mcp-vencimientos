---
name: agentes-personalizados
description: >-
  Guía completa, especificación técnica y flujo de trabajo para crear, configurar,
  administrar y orquestar Agentes Personalizados (Custom Agents y Subagents) en Google
  Antigravity (Antigravity 2.0, Antigravity CLI 'agy' y Antigravity IDE).
  Utiliza esta habilidad cuando el usuario quiera definir agentes especializados con roles específicos,
  restringir herramientas o modelos, aplicar políticas de seguridad (commandExecutionPolicy),
  asociar habilidades (skills) dedicadas a un agente, o diseñar arquitecturas multiagente.
---

# Habilidad: Agentes Personalizados (Custom Agents) en Google Antigravity

Esta habilidad proporciona el estándar, las mejores prácticas y los pasos de ejecución para diseñar e implementar **Agentes Personalizados** en Google Antigravity, según la arquitectura introducida en Antigravity 2.0 y la CLI (`agy`).

---

## 1. ¿Qué son los Agentes Personalizados?

Los agentes personalizados resuelven dos problemas fundamentales en el desarrollo asistido por IA:
1. **Falta de especialización:** Un asistente generalista desconoce convenciones específicas del proyecto, frameworks o pipelines sin explicaciones repetitivas.
2. **Saturación de contexto (*Context Window Bloat*):** Inyectar reglas globales masivas, linters y todas las herramientas disponibles degrada la calidad de respuesta y desperdicia tokens.

Un **Agente Personalizado** es un archivo Markdown (`.md`) con encabezado YAML frontmatter que define:
- Un **rol especializado** con instrucciones de sistema dedicadas.
- Un **conjunto acotado de herramientas** (`tools`) y servidores MCP (`mcpServers`).
- Un **subconjunto seleccionado de habilidades** (`skills`).
- **Políticas de ejecución segura** (`commandExecutionPolicy`) y nivel de permisos.
- **Simetría de ejecución:** Capacidad de operar como agente principal (`mainAgent`), subagente delegado (`subagent`), o ambos.

---

## 2. Ubicación y Descubrimiento (Discovery)

Antigravity detecta automáticamente los archivos de agentes en las siguientes rutas:

| Ámbito | Rutas Soportadas | Uso Recomendado |
| :--- | :--- | :--- |
| **Proyecto / Espacio de trabajo** | `.agents/agents/<nombre>.md`<br>`.agents/agents/<nombre>/agent.md` | Compartir agentes en el repositorio con todo el equipo. |
| **Global (Usuario)** | `~/.gemini/config/agents/<nombre>.md`<br>`~/.gemini/config/agents/<nombre>/agent.md` | Asistentes personales disponibles en todos los proyectos locales. |
| **Plugins** | `plugins/<plugin_name>/agents/` | Empaquetados dentro de un plugin reutilizable. |

---

## 3. Estructura Básica de un Agente (`blueprint.md`)

Todo agente consta de un bloque YAML frontmatter y el cuerpo en Markdown:

```markdown
---
name: nombre-del-agente
description: >-
  Descripción concisa del rol del agente y cuándo debe ser invocado por el planificador.
model: inherit
mainAgent: true
subagent: true
commandExecutionPolicy: auto
permissionMode: acceptEdits
tools:
  - view_file
  - replace_file_content
  - run_command
skills:
  - skills/mi-habilidad-especifica
---

# Instrucciones del Sistema
Eres un especialista en [rol]. Tu objetivo principal es [objetivo].

## Directrices Operativas
1. Analiza antes de modificar.
2. Utiliza las herramientas asignadas de forma segura.
3. Valida los resultados tras cada cambio.
```

---

## 4. Parámetros del Frontmatter (YAML)

Consulte la [Guía de Configuración Detallada](./references/guia-configuracion.md) para la referencia completa de campos:

- **`name`** *(string, obligatorio)*: Identificador único en minúsculas y separado por guiones (kebab-case).
- **`description`** *(string, obligatorio)*: Usado por el agente coordinador para decidir la delegación de tareas.
- **`tools`** *(lista de strings)*: Lista estricta de herramientas permitidas (ej. `view_file`, `replace_file_content`, `grep_search`, `run_command`, `manage_task`).
  > [!WARNING]
  > Escribir incorrectamente el nombre de una herramienta puede suspender la ejecución del subagente. Valide siempre los nombres exactos.
- **`mainAgent`** *(boolean, por defecto `true`)*: Si es `true`, puede seleccionarse en la interfaz visual de Antigravity 2.0 o ejecutarse vía CLI (`agy --agent <nombre>`).
- **`subagent`** *(boolean, por defecto `true`)*: Si es `true`, puede ser invocado automáticamente por un agente coordinador.
- **`model`** *(string, por defecto `inherit`)*: `inherit`, `flash`, o `pro`.
- **`commandExecutionPolicy`** *(string, por defecto `sandbox`)*:
  - `auto`: Ejecuta comandos estándar de compilación y pruebas de forma autónoma sin bloquear pidiendo confirmación repetitiva. Operaciones de alto riesgo siguen protegidas.
  - `sandbox`: Ejecución en entorno restringido.
  - `eager`: Ejecución rápida con confirmación mínima.
  - `off`: Requiere aprobación manual para cada ejecución.
- **`skills`**: Lista de rutas a habilidades que este agente puede utilizar (ej. `skills/mi-habilidad`).
- **`mcpServers`**: Lista de servidores MCP específicos accesibles para este agente.

---

## 5. Flujo de Trabajo para Crear un Agente

Cuando se requiera implementar un nuevo agente personalizado en un proyecto:

1. **Definir el alcance:**
   - ¿Qué rol específico desempeñará? (ej. revisor de seguridad, modernizador de dependencias, generador de pruebas, documentador).
   - ¿Necesita ser interactivo (`mainAgent: true`) o sólo subagente en segundo plano (`mainAgent: false, subagent: true`)?

2. **Seleccionar las herramientas estrictamente necesarias:**
   - No añadir herramientas superfluas para evitar dispersión y consumo innecesario de contexto.

3. **Configurar la política de comandos adecuada:**
   - Si ejecuta tests o builds continuos en background, usar `commandExecutionPolicy: auto`.

4. **Crear el archivo en el proyecto:**
   - Guardar en `.agents/agents/<nombre-del-agente>.md`.

5. **Verificar y Probar:**
   - Probar desde la CLI: `agy --agent <nombre-del-agente>`
   - O delegar desde una sesión interactiva llamando al subagente.

---

## 6. Documentación de Referencia y Ejemplos

- [Guía de Configuración y Políticas](./references/guia-configuracion.md): Especificación técnica de parámetros y ciclo de vida de los subagentes.
- [Catálogo de Ejemplos de Agentes](./references/ejemplos-agentes.md): Plantillas listas para usar (Auditor de Código, Modernizador de Dependencias, Ejecutor de Pruebas).
- [Ejemplo: Dependency Modernizer](./examples/dependency-modernizer.md): Agente especializado en actualización de paquetes y verificación de builds.
- [Ejemplo: Code Auditor](./examples/code-auditor.md): Agente enfocado en auditorías de seguridad y análisis estático.
