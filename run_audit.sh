#!/usr/bin/env bash
# ==============================================================================
# StreamVault v2 — Runner de Seguridad y Auditoría Autónoma (Bash / Linux / macOS)
# Ejecución con un solo comando: ./run_audit.sh
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_PATH="${SCRIPT_DIR}/security_pipeline/orchestrator.js"

if [ ! -f "${ORCHESTRATOR_PATH}" ]; then
  echo "❌ Error: No se encontró el orquestador en ${ORCHESTRATOR_PATH}"
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "❌ Error: Node.js no está instalado o no se encuentra en el PATH."
  exit 1
fi

node "${ORCHESTRATOR_PATH}"
EXIT_CODE=$?

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "✅ Pipeline completado exitosamente."
  if [ -f "${SCRIPT_DIR}/SECURITY_AUDIT_REPORT.md" ]; then
    echo "📄 Reporte consolidado disponible en: ${SCRIPT_DIR}/SECURITY_AUDIT_REPORT.md"
  fi
else
  echo "❌ El pipeline detectó alertas de seguridad que requieren atención."
fi

exit ${EXIT_CODE}
