const fs = require('fs');
const path = require('path');

/**
 * Genera el reporte consolidado de seguridad en Markdown y JSON
 */
function generateReports(pipelineResults, outputDir) {
  const { sast, secrets, sca, dast, durationMs } = pipelineResults;

  const allFindings = [
    ...(sast.findings || []).map(f => ({ ...f, origin: 'SAST' })),
    ...(secrets.findings || []).map(f => ({ ...f, origin: 'Secret Scanner' })),
    ...(sca.findings || []).map(f => ({ ...f, origin: 'SCA (Dependencias)' })),
    ...(dast.findings || []).map(f => ({ ...f, origin: 'DAST' }))
  ];

  // Cálculo de conteos por severidad
  const counts = {
    CRITICAL: 0,
    HIGH: 0,
    MEDIUM: 0,
    LOW: 0,
    INFO: 0
  };

  allFindings.forEach(f => {
    const sev = (f.severity || 'LOW').toUpperCase();
    if (counts[sev] !== undefined) {
      counts[sev]++;
    } else {
      counts.LOW++;
    }
  });

  // Cálculo de Security Score (0 a 100)
  let score = 100;
  score -= counts.CRITICAL * 25;
  score -= counts.HIGH * 10;
  score -= counts.MEDIUM * 4;
  score -= counts.LOW * 1;
  if (score < 0) score = 0;

  let grade = 'A+';
  if (score < 50) grade = 'F';
  else if (score < 70) grade = 'C';
  else if (score < 85) grade = 'B';
  else if (score < 95) grade = 'A';

  const timestamp = new Date().toISOString();

  // 1. Generar JSON
  const jsonReport = {
    timestamp,
    durationSeconds: (durationMs / 1000).toFixed(2),
    securityScore: score,
    securityGrade: grade,
    summary: {
      totalFindings: allFindings.length,
      critical: counts.CRITICAL,
      high: counts.HIGH,
      medium: counts.MEDIUM,
      low: counts.LOW,
      info: counts.INFO
    },
    phases: {
      sast: {
        filesScanned: sast.filesScanned,
        linesScanned: sast.linesScanned,
        findingsCount: sast.findings.length
      },
      secrets: {
        filesScanned: secrets.filesScanned,
        findingsCount: secrets.findings.length
      },
      sca: {
        packagesScanned: sca.packagesScanned,
        findingsCount: sca.findings.length
      },
      dast: {
        serverOnline: dast.serverOnline,
        probesExecuted: dast.probesExecuted,
        findingsCount: dast.findings.length
      }
    },
    findings: allFindings
  };

  const jsonPath = path.join(outputDir, 'security_audit_report.json');
  fs.writeFileSync(jsonPath, JSON.stringify(jsonReport, null, 2), 'utf-8');

  // 2. Generar Markdown
  let md = `# 🛡️ REPORTE CONSOLIDADO DE AUDITORÍA Y ROBUSTEZ DEVSECOPS
## StreamVault v2 — Pipeline Automatizado de Seguridad

- **Fecha de Ejecución**: \`${timestamp}\`
- **Tiempo de Análisis**: \`${(durationMs / 1000).toFixed(2)} segundos\`
- **Puntuación de Seguridad**: **\`${score}/100\`** (Calificación: **\`${grade}\`**)
- **Total de Hallazgos**: **\`${allFindings.length}\`**

---

### 🚦 Resumen por Nivel de Severidad

| Severidad | Cantidad | Impacto |
| :--- | :---: | :--- |
| 🔴 **CRITICAL** | **${counts.CRITICAL}** | Vulnerabilidades críticas con riesgo inmediato de compromiso total. |
| 🟠 **HIGH** | **${counts.HIGH}** | Fallos de seguridad graves en autenticación, secretos o acceso. |
| 🟡 **MEDIUM** | **${counts.MEDIUM}** | Debilidades moderadas en configuraciones o manejo de errores. |
| 🔵 **LOW** | **${counts.LOW}** | Mejoras preventivas y buenas prácticas de hardening. |

---

### 🧩 Resumen de Fases Ejecutadas

#### 1. Análisis Estático de Código (SAST)
- **Archivos Python Auditados**: \`${sast.filesScanned}\`
- **Líneas de Código Analizadas**: \`${sast.linesScanned.toLocaleString()}\`
- **Hallazgos Detectados**: \`${sast.findings.length}\`
${sast.findings.length === 0 ? '✅ *Cero vulnerabilidades estáticas detectadas (Sin SQLi, Sin Command Injection, Sin Insecure Deserialization).*' : ''}

#### 2. Detección de Secretos y Credenciales
- **Archivos Analizados**: \`${secrets.filesScanned}\`
- **Hallazgos Detectados**: \`${secrets.findings.length}\`
${secrets.findings.length === 0 ? '✅ *Cero credenciales sensibles, tokens de bots o claves privadas expuestas en código fuente.*' : ''}

#### 3. Auditoría de Dependencias (SCA - OSV.dev)
- **Paquetes Evaluados**: \`${sca.packagesScanned}\` (\`v2/requirements.txt\`)
- **Vulnerabilidades Conocidas (CVEs)**: \`${sca.findings.length}\`
${sca.findings.length === 0 ? '✅ *Todas las dependencias están libres de CVEs críticos reportados en la base de datos de seguridad.*' : ''}

#### 4. Pruebas Dinámicas de Robustez (DAST)
- **Estado del Servidor Local**: \`${dast.serverOnline ? 'ONLINE (http://localhost:8000)' : 'SIMULADO / OFFLINE'}\`
- **Probes Dinámicos Ejecutados**: \`${dast.probesExecuted}\`
- **Hallazgos Detectados**: \`${dast.findings.length}\`
`;

  if (dast.probes && dast.probes.length > 0) {
    md += `\n**Detalle de Probes Dinámicos:**\n`;
    dast.probes.forEach(p => {
      md += `- [${p.status === 'PASSED' ? '✅' : '❌'}] **${p.name}** (\`${p.target}\`)${p.details ? `: ${p.details}` : ''}\n`;
    });
  }

  // Tabla de Hallazgos (si los hay)
  if (allFindings.length > 0) {
    md += `\n---\n\n### 📋 Matriz Detallada de Hallazgos y Remediaciones\n\n`;
    md += `| Origen | ID / CVE | Título | Severidad | Ubicación | Remediación Recomendada |\n`;
    md += `| :--- | :--- | :--- | :---: | :--- | :--- |\n`;
    allFindings.forEach(f => {
      const loc = f.file ? `${f.file}:${f.line}` : (f.package || 'N/A');
      md += `| **${f.origin}** | \`${f.id}\` | ${f.title} | **${f.severity}** | \`${loc}\` | ${f.remediation} |\n`;
    });
  } else {
    md += `\n---\n\n### 🏆 Conclusión de Robustez\n\nEl sistema superó exitosamente todas las pruebas de seguridad estáticas, dinámicas y de composición sin registrar fallos de severidad alta o crítica.\n`;
  }

  md += `\n---\n*Reporte generado automáticamente por StreamVault DevSecOps Security Pipeline.*\n`;

  const mdPath = path.join(outputDir, 'SECURITY_AUDIT_REPORT.md');
  fs.writeFileSync(mdPath, md, 'utf-8');

  return { jsonPath, mdPath, score, grade, counts };
}

module.exports = { generateReports };
