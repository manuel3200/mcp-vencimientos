const http = require('http');
const https = require('https');
const url = require('url');

/**
 * Helper para realizar peticiones HTTP de prueba
 */
function sendRequest(targetUrl, options = {}) {
  return new Promise((resolve) => {
    const parsed = new URL(targetUrl);
    const isHttps = parsed.protocol === 'https:';
    const client = isHttps ? https : http;

    const reqOptions = {
      hostname: parsed.hostname,
      port: parsed.port || (isHttps ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: options.method || 'GET',
      headers: options.headers || {},
      timeout: options.timeout || 3000
    };

    const req = client.request(reqOptions, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        resolve({
          reachable: true,
          status: res.statusCode,
          headers: res.headers,
          body: data
        });
      });
    });

    req.on('error', (err) => {
      resolve({ reachable: false, error: err.message });
    });

    req.on('timeout', () => {
      req.destroy();
      resolve({ reachable: false, error: 'Timeout de conexión' });
    });

    if (options.body) {
      req.write(typeof options.body === 'string' ? options.body : JSON.stringify(options.body));
    }
    req.end();
  });
}

/**
 * Dynamic Application Security Testing (DAST)
 */
async function runDastScan(baseUrl = 'http://127.0.0.1:8000') {
  const probes = [];
  const findings = [];

  // Verificar si el servidor local está activo
  const healthCheck = await sendRequest(`${baseUrl}/health`);
  const isServerRunning = healthCheck.reachable;

  if (!isServerRunning) {
    return {
      module: 'Dynamic Application Security Testing (DAST)',
      status: 'NOT_RUN',
      serverOnline: false,
      targetUrl: baseUrl,
      probesExecuted: 0,
      probes: [],
      findings: [],
      error: healthCheck.error || 'Servidor de pruebas no disponible'
    };
  }

  // 1. Prueba de Headers de Seguridad
  const headers = healthCheck.headers || {};
  const hasNosniff = Boolean(headers['x-content-type-options']);
  const hasXfo = Boolean(headers['x-frame-options']);
  probes.push({
    name: 'Auditoría de Cabeceras de Seguridad HTTP',
    target: `${baseUrl}/health`,
    status: (healthCheck.status === 200 && hasNosniff && hasXfo) ? 'PASSED' : 'FAILED'
  });

  if (!hasNosniff) {
    findings.push({
      id: 'DAST-HEAD-01',
      title: 'Falta cabecera X-Content-Type-Options: nosniff',
      severity: 'LOW',
      cwe: 'CWE-16',
      owasp: 'A05:2021-Security Misconfiguration',
      remediation: 'Configurar middleware para añadir "X-Content-Type-Options: nosniff" en todas las respuestas.'
    });
  }

  if (!hasXfo) {
    findings.push({
      id: 'DAST-HEAD-02',
      title: 'Falta cabecera X-Frame-Options (Protección contra Clickjacking)',
      severity: 'MEDIUM',
      cwe: 'CWE-1021',
      owasp: 'A05:2021-Security Misconfiguration',
      remediation: 'Añadir cabecera "X-Frame-Options: DENY" o "SAMEORIGIN".'
    });
  }

  // 2. Intento de Bypass de Autenticación en /mcp
  const mcpProbe = await sendRequest(`${baseUrl}/mcp/`, { method: 'GET' });
  if (mcpProbe.reachable) {
    const isProtected = mcpProbe.status === 401 || mcpProbe.status === 403;
    probes.push({
      name: 'Control de Acceso en Endpoints MCP (/mcp/)',
      target: `${baseUrl}/mcp/`,
      status: isProtected ? 'PASSED' : 'FAILED'
    });

    if (!isProtected) {
      findings.push({
        id: 'DAST-AUTH-01',
        title: `Respuesta inesperada (${mcpProbe.status}) sin autenticación en /mcp/`,
        severity: 'HIGH',
        cwe: 'CWE-306',
        owasp: 'A01:2021-Broken Access Control',
        remediation: 'Garantizar que mcp_oauth_guard bloquee peticiones sin Bearer token válido con HTTP 401/403.'
      });
    }
  }

  // 3. Control de Acceso en Endpoint Financiero (/api/v1/finance/summary)
  const finProbe = await sendRequest(`${baseUrl}/api/v1/finance/summary`, { method: 'GET' });
  if (finProbe.reachable) {
    const isFinProtected = finProbe.status === 401 || finProbe.status === 403;
    probes.push({
      name: 'Control de Acceso en API Financiera (/api/v1/finance/summary)',
      target: `${baseUrl}/api/v1/finance/summary`,
      status: isFinProtected ? 'PASSED' : 'FAILED'
    });
    if (!isFinProtected) {
      findings.push({
        id: 'DAST-AUTH-02',
        title: `Endpoint financiero accesible o con error (${finProbe.status}) sin token válido`,
        severity: 'HIGH',
        cwe: 'CWE-306',
        owasp: 'A01:2021-Broken Access Control',
        remediation: 'Exigir token de servicio con scope finance:read o sesión autenticada.'
      });
    }
  }

  // 4. Fuzzing de Entrada con JSON Malformado y Verificación Fail-Closed en Webhook
  const fuzzProbe = await sendRequest(`${baseUrl}/api/webhook/whatsapp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{"invalid_payload": [malformed, syntax'
  });

  if (fuzzProbe.reachable) {
    const exposesTraceback = Boolean(fuzzProbe.body && (
      fuzzProbe.body.includes('Traceback (most recent call last)') ||
      fuzzProbe.body.includes('sqlite3.OperationalError')
    ));
    const validStatus = [400, 401, 403, 422, 503].includes(fuzzProbe.status);
    probes.push({
      name: 'Manejo de Errores y Fail-Closed en Webhook (/api/webhook/whatsapp)',
      target: `${baseUrl}/api/webhook/whatsapp`,
      status: (!exposesTraceback && validStatus) ? 'PASSED' : 'FAILED'
    });

    if (exposesTraceback) {
      findings.push({
        id: 'DAST-ERR-01',
        title: 'Exposición de Stack Trace Interno en Error HTTP',
        severity: 'MEDIUM',
        cwe: 'CWE-209',
        owasp: 'A04:2021-Insecure Design',
        remediation: 'Implementar manejador global de excepciones para retornar mensajes genéricos al cliente.'
      });
    }
  }

  const hasBlockingFinding = findings.some(f => ['CRITICAL', 'HIGH'].includes(f.severity));
  return {
    module: 'Dynamic Application Security Testing (DAST)',
    status: hasBlockingFinding ? 'FAILED' : 'COMPLETED',
    serverOnline: true,
    targetUrl: baseUrl,
    probesExecuted: probes.length,
    probes,
    findings
  };
}

module.exports = { runDastScan };
