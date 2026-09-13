# 🤖 Equipo de Agentes Personalizados: Desarrollo Web

Este directorio contiene la configuración de agentes especializados para proyectos de desarrollo web en este espacio de trabajo.
El equipo implementa el patrón de **división estricta de responsabilidades**, donde el agente principal lidera sin programar directamente, dos especialistas construyen las capas y un auditor valida la calidad.

---

## 👥 Miembros del Equipo

| Agente | Archivo | Rol | Herramientas Principales | Restricción Clave |
| :--- | :--- | :--- | :--- | :--- |
| **Orquestador** | [`orquestador.md`](./orquestador.md) | Agente Principal / Coordinador | `view_file`, `list_dir`, `grep_search`, `schedule` | **No programa:** Solo planifica, delega y valida. |
| **Frontend** | [`frontend.md`](./frontend.md) | Especialista UI / UX | `write_to_file`, `replace_file_content`, `generate_image`, `browser_subagent` | **No toca lógica de datos:** Solo interfaz, estilos y responsive. |
| **Backend** | [`backend.md`](./backend.md) | Especialista de Lógica y Datos | `write_to_file`, `replace_file_content`, `run_command` | **No toca diseño visual:** Solo estructuras, persistencia y validaciones. |
| **QA** | [`qa.md`](./qa.md) | Especialista de Calidad y Pruebas | `view_file`, `list_dir`, `grep_search`, `run_command`, `browser_subagent` | **No modifica código:** Solo prueba y reporta fallos. |

---

## 🔄 Flujo de Trabajo en Cascada Iterativa

```
  [ Usuario: Petición ]
           │
           ▼
   [ 1. Orquestador ]
           │
     ┌─────┴─────────────────────────┐
     ▼                               ▼
[ 2. Backend ]                 [ 3. Frontend ]
(Estructuras, persistencia)     (HTML, CSS, UI, Dark Mode)
     │                               │
     └──────────────┬────────────────┘
                    ▼
               [ 4. QA ]
       (Pruebas de estrés y UI)
                    │
            ¿Encontró fallos?
           /                 \
         Sí                   No
         /                     \
[ Reporte a Orquestador ]  [ Entrega final al Usuario ]
```

---

## 🚀 Cómo Usar el Equipo

### 1. Desde la Interfaz Gráfica de Antigravity 2.0
Selecciona **`orquestador`** en el selector de agentes de la ventana de chat e ingresa tu requerimiento web (ejemplo: *"Crea una aplicación web de lista de tareas con categorías, modo oscuro y persistencia local"*).

### 2. Desde la Terminal con Antigravity CLI (`agy`)
Inicia una sesión directa con el orquestador:
```bash
agy --agent orquestador
```

O invoca directamente a un especialista para una tarea puntual:
```bash
# Para rediseñar o pulir la interfaz visual:
agy --agent frontend

# Para definir modelos o almacenamiento:
agy --agent backend

# Para auditar y buscar errores:
agy --agent qa
```
