const fs = require('fs');
const path = require('path');
const https = require('https');

/**
 * Comparador semántico de versiones (PEP 440 / SemVer básico) para evitar errores lexicográficos ("10.0.0" vs "9.0.0") (Q02)
 */
function compareVersions(vA, vB) {
  const cleanA = String(vA || '0').replace(/^[^0-9]+/, '').split(/[.\-+]/).map(p => (/^\d+$/.test(p) ? parseInt(p, 10) : p));
  const cleanB = String(vB || '0').replace(/^[^0-9]+/, '').split(/[.\-+]/).map(p => (/^\d+$/.test(p) ? parseInt(p, 10) : p));
  const len = Math.max(cleanA.length, cleanB.length);
  for (let i = 0; i < len; i++) {
    const a = cleanA[i] !== undefined ? cleanA[i] : 0;
    const b = cleanB[i] !== undefined ? cleanB[i] : 0;
    if (typeof a === 'number' && typeof b === 'number') {
      if (a > b) return 1;
      if (a < b) return -1;
    } else {
      const sA = String(a);
      const sB = String(b);
      if (sA > sB) return 1;
      if (sA < sB) return -1;
    }
  }
  return 0;
}

/**
 * Consulta la API pública de OSV.dev para buscar vulnerabilidades conocidas en paquetes PyPI.
 * Rechaza explícitamente ante error HTTP, JSON inválido, error de red o timeout (Q02: nunca convierte fallo en []).
 */
function queryOsvApiOnce(packageName, version, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({
      package: {
        name: packageName,
        ecosystem: 'PyPI'
      },
      version: version
    });

    const options = {
      hostname: 'api.osv.dev',
      port: 443,
      path: '/v1/query',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        'User-Agent': 'StreamVault-SecurityAuditor/2.0'
      },
      timeout: timeoutMs
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) {
          reject(new Error(`Consulta SCA fallida para ${packageName}@${version}: HTTP ${res.statusCode}`));
          return;
        }
        try {
          const parsed = JSON.parse(data);
          if (!parsed || typeof parsed !== 'object') {
            reject(new Error(`Respuesta JSON inválida de OSV.dev para ${packageName}@${version}`));
            return;
          }
          resolve(Array.isArray(parsed.vulns) ? parsed.vulns : []);
        } catch (err) {
          reject(new Error(`Error parseando JSON de OSV.dev para ${packageName}@${version}: ${err.message}`));
        }
      });
    });

    req.on('error', (err) => {
      reject(new Error(`Error de red consultando OSV.dev para ${packageName}@${version}: ${err.message}`));
    });
    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`Timeout (${timeoutMs}ms) consultando OSV.dev para ${packageName}@${version}`));
    });
    req.write(payload);
    req.end();
  });
}

async function queryOsvApi(packageName, version, maxAttempts = 2) {
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await queryOsvApiOnce(packageName, version);
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError;
}

/**
 * Software Composition Analysis (SCA) para dependencias resueltas (requirements.lock) o manifiesto (requirements.txt) (Q02)
 */
async function runDependencyAudit(requirementsPath) {
  const findings = [];
  const scannedPackages = [];
  const auditedAt = new Date().toISOString();

  const lockCandidate = path.join(path.dirname(requirementsPath), 'requirements.lock');
  const sourceFile = fs.existsSync(lockCandidate) ? lockCandidate : requirementsPath;

  if (!fs.existsSync(sourceFile)) {
    return {
      module: 'Software Composition Analysis (SCA)',
      status: 'ERROR',
      auditedAt,
      error: `Archivo ${sourceFile} no encontrado.`,
      packagesScanned: 0,
      findings: []
    };
  }

  const content = fs.readFileSync(sourceFile, 'utf-8');
  const lines = content.split('\n');
  const queryErrors = [];

  for (const line of lines) {
    const cleanLine = line.trim().split(/\s+--hash=/)[0].trim();
    if (!cleanLine || cleanLine.startsWith('#') || cleanLine.startsWith('-')) continue;

    const match = /^([a-zA-Z0-9_\-\[\].]+)\s*([><!=~^]+)\s*([0-9a-zA-Z_.\-]+)/.exec(cleanLine);
    if (match) {
      const rawName = match[1].split('[')[0].toLowerCase();
      const operator = match[2] || '==';
      const version = match[3];

      scannedPackages.push({
        name: rawName,
        operator,
        version,
        pinned: operator === '=='
      });

      try {
        const vulns = await queryOsvApi(rawName, version);
        if (vulns && vulns.length > 0) {
          const uniqueCves = new Set();
          let maxSeverity = 'LOW';
          let latestFixed = null;
          const sevWeight = { 'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'MODERATE': 2, 'LOW': 1 };

          for (const v of vulns) {
            const cveId = (v.aliases && v.aliases[0]) || v.id;
            uniqueCves.add(cveId);

            let sev = 'MEDIUM';
            if (v.database_specific && v.database_specific.severity) {
              sev = v.database_specific.severity.toUpperCase();
            }
            if ((sevWeight[sev] || 1) > (sevWeight[maxSeverity] || 1)) {
              maxSeverity = sev === 'MODERATE' ? 'MEDIUM' : sev;
            }

            if (v.affected && v.affected[0] && v.affected[0].ranges) {
              const range = v.affected[0].ranges.find(r => r.type === 'ECOSYSTEM' || r.type === 'SEMVER');
              if (range && range.events) {
                for (const ev of range.events) {
                  if (ev.fixed && (!latestFixed || compareVersions(ev.fixed, latestFixed) > 0)) {
                    latestFixed = ev.fixed;
                  }
                }
              }
            }
          }

          const topCves = Array.from(uniqueCves).slice(0, 3).join(', ');
          const fixedMsg = latestFixed ? `==${latestFixed}` : 'última versión estable';

          findings.push({
            id: `SCA-${rawName.toUpperCase()}`,
            title: `Versión vulnerable en ${rawName} (${uniqueCves.size} avisos reportados)`,
            package: rawName,
            installedVersion: `${operator}${version}`,
            severity: maxSeverity,
            cwe: 'CWE-1395',
            owasp: 'A06:2021-Vulnerable and Outdated Components',
            summary: `La versión evaluada (${operator}${version}) contiene ${uniqueCves.size} avisos de seguridad conocidos (${topCves}).`,
            remediation: `Fijar a "${rawName}${fixedMsg}" y regenerar requirements.lock.`
          });
        }
      } catch (err) {
        queryErrors.push(err.message);
      }
    }
  }

  return {
    module: 'Software Composition Analysis (SCA)',
    status: queryErrors.length > 0 ? 'ERROR' : 'COMPLETED',
    auditedAt,
    source: 'OSV.dev API v1',
    requirementsFile: sourceFile,
    packagesScanned: scannedPackages.length,
    packages: scannedPackages,
    error: queryErrors.length > 0 ? queryErrors.join('; ') : null,
    findings
  };
}

module.exports = { runDependencyAudit, queryOsvApi, compareVersions };
