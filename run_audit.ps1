# ==============================================================================
# StreamVault v2 - Runner de Seguridad y Auditoria Autonoma (PowerShell)
# Ejecucion con un solo comando: .\run_audit.ps1
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "[SEC] STREAMVAULT v2 - INICIANDO PIPELINE DE SEGURIDAD Y ROBUSTEZ" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$OrchestratorPath = Join-Path $ScriptDir "security_pipeline\orchestrator.js"

if (-not (Test-Path $OrchestratorPath)) {
    Write-Error "No se encontro el orquestador en $OrchestratorPath"
    exit 1
}

# Comprobar disponibilidad de Node.js
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js no esta instalado o no se encuentra en el PATH del sistema."
    exit 1
}

# Ejecutar orquestador
node $OrchestratorPath
$AuditExitCode = $LASTEXITCODE

Write-Host ""
if ($AuditExitCode -eq 0) {
    Write-Host "[OK] Pipeline completado exitosamente." -ForegroundColor Green
    $ReportPath = Join-Path $ScriptDir "SECURITY_AUDIT_REPORT.md"
    if (Test-Path $ReportPath) {
        Write-Host "[REPORT] El reporte consolidado esta disponible en: $ReportPath" -ForegroundColor White
    }
} else {
    Write-Host "[WARN] El pipeline detecto alertas de seguridad que requieren atencion." -ForegroundColor Yellow
}

exit $AuditExitCode
