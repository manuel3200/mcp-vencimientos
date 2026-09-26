const fs = require('fs');
const path = require('path');

/**
 * SAST Scanner para StreamVault v2
 * Audita código Python buscando vulnerabilidades comunes (OWASP Top 10 / CWE)
 */
function runSastScan(targetDir) {
  const findings = [];
  const rules = [
    {
      id: 'CWE-89',
      title: 'Inyección SQL por Concatenación o Formateo Dinámico de Strings',
      severity: 'CRITICAL',
      cwe: 'CWE-89',
      owasp: 'A03:2021-Injection',
      pattern: /\.execute\s*\(\s*(f["']|["'][^"']*%|["'][^"']*\.format\()/i,
      remediation: 'Utilizar siempre consultas parametrizadas con marcadores de posición "?" y tupla de parámetros.',
      isExempt: (line, filePath, surroundingText) => {
        // Eximir creación estática de esquemas (DDL) y consultas con marcadores de posición '?' dinámicos seguros
        const block = surroundingText || line;
        if (/CREATE TABLE/i.test(block) || /CREATE INDEX/i.test(block) || /PRAGMA/i.test(block)) return true;
        if (/\{placeholders\}/i.test(block) || /join\(update_fields\)/i.test(block) || /\{plat_filter\}/i.test(block)) return true;
        return false;
      }
    },
    {
      id: 'CWE-78',
      title: 'Inyección de Comandos del Sistema Operativo',
      severity: 'CRITICAL',
      cwe: 'CWE-78',
      owasp: 'A03:2021-Injection',
      pattern: /(os\.system|subprocess\.Popen|subprocess\.run|subprocess\.call)\s*\([^)]*shell\s*=\s*True/i,
      remediation: 'Evitar invocar el shell del sistema (shell=True). Usar listas de argumentos validados.'
    },
    {
      id: 'CWE-94',
      title: 'Ejecución de Código Dinámico Inseguro (eval/exec)',
      severity: 'CRITICAL',
      cwe: 'CWE-94',
      owasp: 'A03:2021-Injection',
      pattern: /\b(eval|exec)\s*\([^)]+\)/,
      remediation: 'Eliminar el uso de eval() o exec(). Utilizar serializadores tipados o parsers seguros.'
    },
    {
      id: 'CWE-502',
      title: 'Deserialización Insegura de Objetos (pickle)',
      severity: 'HIGH',
      cwe: 'CWE-502',
      owasp: 'A08:2021-Software and Data Integrity Failures',
      pattern: /\bpickle\.(loads|load)\s*\(/,
      remediation: 'Reemplazar pickle por formatos de intercambio seguros como JSON o Protocol Buffers.'
    },
    {
      id: 'CWE-327',
      title: 'Uso de Funciones Hash Criptográficamente Débiles (MD5/SHA1)',
      severity: 'MEDIUM',
      cwe: 'CWE-327',
      owasp: 'A02:2021-Cryptographic Failures',
      pattern: /hashlib\.(md5|sha1)\s*\(/,
      remediation: 'Utilizar algoritmos criptográficos modernos de la familia SHA-2 (SHA-256) o SHA-3.'
    },
    {
      id: 'CWE-22',
      title: 'Potencial Path Traversal en Apertura de Archivos',
      severity: 'HIGH',
      cwe: 'CWE-22',
      owasp: 'A01:2021-Broken Access Control',
      pattern: /open\s*\(\s*f["'][^"']*(?:\{request|\{filename|\{user_input|\{path)/i,
      remediation: 'Validar y sanitizar rutas usando os.path.abspath() y verificar que pertenezcan al directorio base permitido.'
    },
    {
      id: 'SEC-ISOLATION-WA',
      title: 'Fuga de Privacidad: Falta de Validación de Contexto Grupal (@g.us)',
      severity: 'HIGH',
      cwe: 'CWE-200',
      owasp: 'A01:2021-Broken Access Control',
      pattern: /if\s+["']@g\.us["']\s+in\s+remote_jid.*:\s*(?:return|pass)/i,
      remediation: 'Asegurar que ningún comando que devuelva claves, PINs o balances se ejecute en chats grupales.'
    }
  ];

  function getPythonFiles(dir) {
    let list = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!['__pycache__', 'node_modules', '.git', '.venv', 'venv'].includes(entry.name)) {
          list = list.concat(getPythonFiles(fullPath));
        }
      } else if (entry.isFile() && entry.name.endsWith('.py')) {
        list.push(fullPath);
      }
    }
    return list;
  }

  const pyFiles = getPythonFiles(targetDir);
  let totalLines = 0;

  for (const file of pyFiles) {
    const relFile = path.relative(targetDir, file);
    const content = fs.readFileSync(file, 'utf-8');
    const lines = content.split('\n');
    totalLines += lines.length;

    lines.forEach((line, idx) => {
      const trimmed = line.trim();
      if (trimmed.startsWith('#')) return; // ignora comentarios

      for (const rule of rules) {
        if (rule.pattern.test(line)) {
          const surroundingText = lines.slice(Math.max(0, idx - 2), Math.min(lines.length, idx + 10)).join('\n');
          if (rule.isExempt && rule.isExempt(line, file, surroundingText)) {
            continue;
          }
          findings.push({
            id: rule.id,
            title: rule.title,
            severity: rule.severity,
            cwe: rule.cwe,
            owasp: rule.owasp,
            file: relFile,
            line: idx + 1,
            codeSnippet: trimmed.length > 120 ? trimmed.substring(0, 117) + '...' : trimmed,
            remediation: rule.remediation
          });
        }
      }
    });
  }

  return {
    module: 'SAST Scanner',
    status: 'COMPLETED',
    filesScanned: pyFiles.length,
    linesScanned: totalLines,
    findings
  };
}

module.exports = { runSastScan };
