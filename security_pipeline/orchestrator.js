const path = require('path');
const { runSastScan } = require('./sast_scanner');
const { runSecretDetection } = require('./secret_detector');
const { runDependencyAudit } = require('./dependency_auditor');
const { runDastScan } = require('./dast_runner');
const { generateReports } = require('./report_generator');

const RESET = '\x1b[0m';
const RED = '\x1b[31m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const BLUE = '\x1b[34m';
const CYAN = '\x1b[36m';
const BOLD = '\x1b[1m';

async function main() {
  const startTime = Date.now();
  console.log(`${BOLD}${CYAN}======================================================================${RESET}`);
  console.log(`${BOLD}${CYAN}🛡️  STREAMVAULT v2 — PIPELINE LOCAL DE AUDITORÍA Y ROBUSTEZ DEVSECOPS${RESET}`);
  console.log(`${BOLD}${CYAN}======================================================================${RESET}\n`);

  const rootDir = path.resolve(__dirname, '..');
  const v2Dir = path.join(rootDir, 'v2');
  const requirementsFile = path.join(v2Dir, 'requirements.txt');

  console.log(`📦 Directorio objetivo: ${BOLD}${v2Dir}${RESET}`);
  console.log(`⏱️  Iniciando escaneo autónomo en 4 etapas...\n`);

  // FASE 1: SAST
  console.log(`${BOLD}[1/4] Ejecutando Análisis Estático de Código (SAST)...${RESET}`);
  const sastResults = runSastScan(v2Dir);
  console.log(`  ✓ ${sastResults.filesScanned} archivos analizados (${sastResults.linesScanned.toLocaleString()} líneas)`);
  if (sastResults.findings.length === 0) {
    console.log(`  ${GREEN}✅ Cero vulnerabilidades estáticas detectadas.${RESET}\n`);
  } else {
    console.log(`  ${YELLOW}⚠️  ${sastResults.findings.length} incidencias detectadas.${RESET}\n`);
  }

  // FASE 2: DETECCIÓN DE SECRETOS
  console.log(`${BOLD}[2/4] Escaneando Secretos y Credenciales Expuestas...${RESET}`);
  const secretResults = runSecretDetection(v2Dir);
  console.log(`  ✓ ${secretResults.filesScanned} archivos escaneados`);
  if (secretResults.findings.length === 0) {
    console.log(`  ${GREEN}✅ Cero secretos o claves privadas expuestas en código.${RESET}\n`);
  } else {
    console.log(`  ${YELLOW}⚠️  ${secretResults.findings.length} secretos detectados.${RESET}\n`);
  }

  // FASE 3: AUDITORÍA DE DEPENDENCIAS (SCA)
  console.log(`${BOLD}[3/4] Auditando Dependencias contra OSV.dev (SCA)...${RESET}`);
  const scaResults = await runDependencyAudit(requirementsFile);
  console.log(`  ✓ ${scaResults.packagesScanned} paquetes analizados en requirements.txt`);
  if (scaResults.findings.length === 0) {
    console.log(`  ${GREEN}✅ Cero vulnerabilidades conocidas (CVEs) en dependencias.${RESET}\n`);
  } else {
    console.log(`  ${YELLOW}⚠️  ${scaResults.findings.length} avisos de seguridad encontrados.${RESET}\n`);
  }

  // FASE 4: DAST (PRUEBAS DINÁMICAS)
  console.log(`${BOLD}[4/4] Ejecutando Pruebas Dinámicas contra Endpoints (DAST)...${RESET}`);
  const dastResults = await runDastScan(process.env.TARGET_URL || 'http://127.0.0.1:8000');
  console.log(`  ✓ ${dastResults.probesExecuted} probes dinámicos ejecutados (Servidor: ${dastResults.serverOnline ? 'ONLINE' : 'OFFLINE/SIMULADO'})`);
  if (dastResults.findings.length === 0) {
    console.log(`  ${GREEN}✅ Todas las comprobaciones dinámicas de robustez superadas.${RESET}\n`);
  } else {
    console.log(`  ${YELLOW}⚠️  ${dastResults.findings.length} observaciones dinámicas registradas.${RESET}\n`);
  }

  // COMPILACIÓN DE REPORTES
  const durationMs = Date.now() - startTime;
  console.log(`${BOLD}📊 Generando Reportes Consolidados...${RESET}`);
  const reportInfo = generateReports({
    sast: sastResults,
    secrets: secretResults,
    sca: scaResults,
    dast: dastResults,
    durationMs
  }, rootDir);

  console.log(`  📄 Reporte Markdown: ${BOLD}${reportInfo.mdPath}${RESET}`);
  console.log(`  📄 Reporte JSON:     ${BOLD}${reportInfo.jsonPath}${RESET}\n`);

  console.log(`${BOLD}${CYAN}======================================================================${RESET}`);
  console.log(`${BOLD}🎯 RESULTADO FINAL: PUNTUACIÓN DE SEGURIDAD: ${reportInfo.score >= 85 ? GREEN : YELLOW}${reportInfo.score}/100 (${reportInfo.grade})${RESET}`);
  console.log(`• Hallazgos Críticos: ${reportInfo.counts.CRITICAL > 0 ? RED : GREEN}${reportInfo.counts.CRITICAL}${RESET}`);
  console.log(`• Hallazgos Altos:    ${reportInfo.counts.HIGH > 0 ? YELLOW : GREEN}${reportInfo.counts.HIGH}${RESET}`);
  console.log(`• Hallazgos Medios:   ${reportInfo.counts.MEDIUM}${RESET}`);
  console.log(`• Hallazgos Bajos:    ${reportInfo.counts.LOW}${RESET}`);
  console.log(`• Tiempo Total:       ${(durationMs / 1000).toFixed(2)}s`);
  console.log(`${BOLD}${CYAN}======================================================================${RESET}\n`);

  const hasCriticalCodeVulns = sastResults.findings.some(f => f.severity === 'CRITICAL');
  const strictGate = process.env.STRICT_GATE === 'true';

  if (hasCriticalCodeVulns || (strictGate && reportInfo.counts.CRITICAL > 0)) {
    console.error(`${RED}❌ El pipeline ha detectado vulnerabilidades críticas de código que bloquean el pase a producción.${RESET}`);
    process.exit(1);
  } else {
    console.log(`${GREEN}✨ Pipeline de seguridad completado exitosamente. Revisa el reporte para las recomendaciones de hardening.${RESET}`);
    process.exit(0);
  }
}

main().catch(err => {
  console.error(`${RED}Error crítico ejecutando el pipeline de seguridad:${RESET}`, err);
  process.exit(1);
});
