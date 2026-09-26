const fs = require('fs');
const path = require('path');

/**
 * Calcula la entropía de Shannon de una cadena para detectar secretos aleatorios
 */
function shannonEntropy(str) {
  if (!str || str.length === 0) return 0;
  const frequencies = {};
  for (let i = 0; i < str.length; i++) {
    const char = str[i];
    frequencies[char] = (frequencies[char] || 0) + 1;
  }
  let entropy = 0;
  for (const char in frequencies) {
    const p = frequencies[char] / str.length;
    entropy -= p * Math.log2(p);
  }
  return entropy;
}

/**
 * Redacta el valor sensible dentro del snippet antes de persistirlo en reportes (Q03)
 */
function redactSnippet(line, secretValue) {
  const trimmed = String(line || '').trim();
  if (!secretValue || secretValue.length < 4) {
    return trimmed.length > 100 ? trimmed.substring(0, 97) + '...' : trimmed;
  }
  const redacted = `${secretValue.substring(0, 2)}***REDACTED***`;
  const safeLine = trimmed.split(secretValue).join(redacted);
  return safeLine.length > 100 ? safeLine.substring(0, 97) + '...' : safeLine;
}

/**
 * Detector de Secretos, Credenciales y Defaults Inseguros en Código y Configuración (Q03)
 */
function runSecretDetection(targetDir, extraPaths = []) {
  const findings = [];

  const secretPatterns = [
    {
      id: 'SEC-PRIV-KEY',
      title: 'Clave Privada Asimétrica Expuesta (RSA/EC/PGP)',
      severity: 'CRITICAL',
      pattern: /-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----/i,
      remediation: 'Mover la clave privada a un almacén de secretos (Vault o variables de entorno del host).'
    },
    {
      id: 'SEC-TELEGRAM-TOKEN',
      title: 'Token de Bot de Telegram en Duro',
      severity: 'HIGH',
      pattern: /\b[0-9]{8,10}:[a-zA-Z0-9_-]{35}\b/,
      remediation: 'Utilizar os.getenv("TELEGRAM_BOT_TOKEN") y configurar el valor en el entorno.'
    },
    {
      id: 'SEC-HARDCODED-PASS',
      title: 'Contraseña Sensible en Duro en Código Fuente',
      severity: 'HIGH',
      pattern: /\b(?:password|admin_pass|secret_pass|db_pass|api_secret)\s*=\s*["']([^"']{6,})["']/i,
      remediation: 'Nunca guardar credenciales en el código fuente. Utilizar variables de entorno.'
    },
    {
      id: 'SEC-JWT-SECRET',
      title: 'Secreto de Firma JWT o Sesión en Duro',
      severity: 'HIGH',
      pattern: /\b(?:jwt_secret|session_secret|secret_key)\s*=\s*["']([A-Za-z0-9_\-+=/]{12,})["']/i,
      remediation: 'Cargar la clave de firma de sesión desde la variable de entorno SESSION_SECRET_KEY.'
    },
    {
      id: 'SEC-EVOLUTION-API-KEY',
      title: 'API Key de Evolution API / WhatsApp en Duro',
      severity: 'HIGH',
      pattern: /\b(?:evolution_api_key|wpp_api_key|evolution_token)\s*=\s*["']([A-Za-z0-9_\-]{12,})["']/i,
      remediation: 'Cargar la API key desde la variable de entorno EVOLUTION_API_KEY.'
    },
    {
      id: 'SEC-ENV-DEFAULT',
      title: 'Default Literal Inseguro en Variable de Entorno Sensible (os.getenv)',
      severity: 'HIGH',
      pattern: /os\.(?:getenv|environ\.get)\(\s*["'](?:[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|HMAC_KEY|ENCRYPTION_KEY)[A-Z0-9_]*)["']\s*,\s*["']([^"']{3,})["']\s*\)/,
      remediation: 'No proveer secretos por defecto en os.getenv(). Usar cadena vacía "" y fallar cerrado si falta.'
    }
  ];

  const excludedDirs = ['node_modules', '.git', '__pycache__', '.venv', 'venv', 'security_pipeline'];
  const excludedFiles = [
    '.env.example',
    'oracle.env.example',
    'README.md',
    'walkthrough.md',
    'implementation_plan.md',
    'SECURITY_AUDIT_REPORT.md',
    'security_audit_report.json',
    'PLAN_CORRECCION_SEGURIDAD_V2.md',
    'REVISION_SEGURIDAD_V2_2026-09-26.md'
  ];

  function scanDirectory(dir) {
    let files = [];
    if (!fs.existsSync(dir)) return files;
    const stat = fs.statSync(dir);
    if (stat.isFile()) {
      return [dir];
    }
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!excludedDirs.includes(entry.name)) {
          files = files.concat(scanDirectory(path.join(dir, entry.name)));
        }
      } else if (entry.isFile()) {
        if (!excludedFiles.includes(entry.name)) {
          const ext = path.extname(entry.name).toLowerCase();
          if (['.py', '.js', '.json', '.env', '.yml', '.yaml', '.sh'].includes(ext)) {
            files.push(path.join(dir, entry.name));
          }
        }
      }
    }
    return files;
  }

  let allFiles = scanDirectory(targetDir);
  for (const extra of extraPaths) {
    allFiles = allFiles.concat(scanDirectory(extra));
  }
  allFiles = Array.from(new Set(allFiles));

  for (const filePath of allFiles) {
    const relFile = path.relative(targetDir, filePath);
    // Eximir únicamente suites de pruebas automatizadas identificadas por ruta
    const normalizedRel = relFile.replace(/\\/g, '/');
    if (
      normalizedRel.startsWith('tests/') ||
      normalizedRel.includes('/tests/') ||
      path.basename(filePath).startsWith('test_') ||
      path.basename(filePath).startsWith('harness_')
    ) {
      continue;
    }

    const content = fs.readFileSync(filePath, 'utf-8');
    const lines = content.split('\n');

    lines.forEach((line, idx) => {
      const trimmed = line.trim();
      if (trimmed.startsWith('#') || trimmed.startsWith('//')) return;

      for (const rule of secretPatterns) {
        const match = rule.pattern.exec(line);
        if (match) {
          const secretValue = match[1] || match[0];
          const lowerVal = secretValue.toLowerCase();
          // NOTA Q03: NO se excluyen 'admin123', 'default' ni 'change_me' porque son contraseñas débiles reales si aparecen en código.
          if (
            lowerVal.includes('example') ||
            lowerVal.includes('placeholder') ||
            lowerVal.includes('your_') ||
            lowerVal.startsWith('${') ||
            lowerVal.startsWith('={{')
          ) {
            continue;
          }

          const entropy = shannonEntropy(secretValue);
          findings.push({
            id: rule.id,
            title: rule.title,
            severity: rule.severity,
            cwe: 'CWE-798',
            owasp: 'A07:2021-Identification and Authentication Failures',
            file: relFile,
            line: idx + 1,
            entropy: entropy.toFixed(2),
            codeSnippet: redactSnippet(trimmed, secretValue),
            remediation: rule.remediation
          });
        }
      }
    });
  }

  return {
    module: 'Secret & Credential Detector',
    status: 'COMPLETED',
    filesScanned: allFiles.length,
    findings
  };
}

module.exports = { runSecretDetection, redactSnippet };
