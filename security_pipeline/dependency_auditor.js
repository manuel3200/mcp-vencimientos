const fs = require('fs');
const path = require('path');
const https = require('https');

/**
 * Consulta la API pública de OSV.dev para buscar vulnerabilidades conocidas en paquetes PyPI
 */
function queryOsvApi(packageName, version) {
  return new Promise((resolve) => {
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
      timeout: 4000
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          if (res.statusCode === 200) {
            const parsed = JSON.parse(data);
            resolve(parsed.vulns || []);
          } else {
            resolve([]);
          }
        } catch {
          resolve([]);
        }
      });
    });

    req.on('error', () => { resolve([]); });
    req.on('timeout', () => { req.destroy(); resolve([]); });
    req.write(payload);
    req.end();
  });
}

/**
 * Software Composition Analysis (SCA) para dependencias de requirements.txt
 */
async function runDependencyAudit(requirementsPath) {
  const findings = [];
  const scannedPackages = [];

  if (!fs.existsSync(requirementsPath)) {
    return {
      module: 'Software Composition Analysis (SCA)',
      error: `Archivo ${requirementsPath} no encontrado.`,
      packagesScanned: 0,
      findings: []
    };
  }

  const content = fs.readFileSync(requirementsPath, 'utf-8');
  const lines = content.split('\n');

  for (const line of lines) {
    const cleanLine = line.trim();
    if (!cleanLine || cleanLine.startsWith('#')) continue;

    // Parser de especificaciones de versión de pip (ej: fastapi>=0.110.0, pillow>=10.0.0)
    const match = /^([a-zA-Z0-9_\-\[\]]+)\s*([><!=~^]*)\s*([0-9a-zA-Z_\.\-]*)/.exec(cleanLine);
    if (match) {
      const rawName = match[1].split('[')[0].toLowerCase(); // remover extras como uvicorn[standard]
      const operator = match[2] || '>=';
      const version = match[3] || '0.0.1';

      scannedPackages.push({
        name: rawName,
        operator,
        version: version || 'latest'
      });

      // Consultar OSV.dev
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
              const fixedEvent = range.events.find(e => e.fixed);
              if (fixedEvent && (!latestFixed || fixedEvent.fixed > latestFixed)) {
                latestFixed = fixedEvent.fixed;
              }
            }
          }
        }

        const topCves = Array.from(uniqueCves).slice(0, 3).join(', ');
        const fixedMsg = latestFixed ? `>=${latestFixed}` : 'última versión estable';

        findings.push({
          id: `SCA-${rawName.toUpperCase()}`,
          title: `Límite inferior vulnerable en ${rawName} (${uniqueCves.size} avisos reportados)`,
          package: rawName,
          installedVersion: `${operator}${version}`,
          severity: maxSeverity,
          cwe: 'CWE-1395',
          owasp: 'A06:2021-Vulnerable and Outdated Components',
          summary: `La versión base declarada (${operator}${version}) contiene ${uniqueCves.size} avisos de seguridad conocidos (${topCves}).`,
          remediation: `Actualizar la cota mínima en requirements.txt a "${rawName}${fixedMsg}" para prevenir resolución de versiones vulnerables.`
        });
      }
    }
  }

  return {
    module: 'Software Composition Analysis (SCA)',
    requirementsFile: requirementsPath,
    packagesScanned: scannedPackages.length,
    packages: scannedPackages,
    findings
  };
}

module.exports = { runDependencyAudit };
