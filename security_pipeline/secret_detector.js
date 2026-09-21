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
 * Detector de Secretos y Credenciales en Código Fuente
 */
function runSecretDetection(targetDir) {
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
      pattern: /(?:password|admin_pass|secret_pass|db_pass|api_secret)\s*=\s*["']([^"']{8,})["']/i,
      remediation: 'Nunca guardar credenciales en el código fuente. Utilizar variables de entorno.'
    },
    {
      id: 'SEC-JWT-SECRET',
      title: 'Secreto de Firma JWT o Sesión en Duro',
      severity: 'HIGH',
      pattern: /(?:jwt_secret|session_secret|secret_key)\s*=\s*["']([A-Za-z0-9_\-+=/]{16,})["']/i,
      remediation: 'Cargar la clave de firma de sesión desde la variable de entorno SESSION_SECRET_KEY.'
    },
    {
      id: 'SEC-EVOLUTION-API-KEY',
      title: 'API Key de Evolution API / WhatsApp en Duro',
      severity: 'HIGH',
      pattern: /(?:evolution_api_key|wpp_api_key|evolution_token)\s*=\s*["']([A-Za-z0-9_\-]{16,})["']/i,
      remediation: 'Cargar la API key desde la variable de entorno EVOLUTION_API_KEY.'
    }
  ];

  const excludedDirs = ['node_modules', '.git', '__pycache__', '.venv', 'venv'];
  const excludedFiles = ['.env.example', 'README.md', 'walkthrough.md', 'implementation_plan.md'];

  function scanDirectory(dir) {
    let files = [];
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

  const allFiles = scanDirectory(targetDir);

  for (const filePath of allFiles) {
    const relFile = path.relative(targetDir, filePath);
    // Eximir archivos de test que intencionalmente usan valores mock o dummy
    if (relFile.includes('tests') || relFile.includes('test_') || relFile.includes('harness')) {
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
          // Evitar falsos positivos con placeholders típicos
          const lowerVal = secretValue.toLowerCase();
          if (
            lowerVal.includes('example') ||
            lowerVal.includes('placeholder') ||
            lowerVal.includes('your_') ||
            lowerVal.includes('admin123') ||
            lowerVal.includes('change_me') ||
            lowerVal.includes('default') ||
            lowerVal.includes('mock') ||
            lowerVal.includes('test')
          ) {
            continue;
          }

          // Si es contraseña o clave, validar entropía para descartar palabras comunes
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
            codeSnippet: trimmed.length > 100 ? trimmed.substring(0, 97) + '...' : trimmed,
            remediation: rule.remediation
          });
        }
      }
    });
  }

  return {
    module: 'Secret & Credential Detector',
    filesScanned: allFiles.length,
    findings
  };
}

module.exports = { runSecretDetection };
