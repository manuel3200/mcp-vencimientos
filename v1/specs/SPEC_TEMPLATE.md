# [SPEC-ID]: Nombre Breve de la Funcionalidad

> **Estado:** Borrador | En Revisión | Aprobado | Implementado  
> **Autor:** Arquitecto / Agente Líder  
> **Fecha:** AAAA-MM-DD  

---

## 1. Objetivo de Negocio y Justificación (El "Por Qué")
Explica claramente el problema de negocio que resuelve esta funcionalidad y el valor que aporta al sistema o al usuario.

---

## 2. Reglas de Dominio e Invariantes Sagradas (El "Qué")
Reglas puras que deben cumplirse siempre, independientemente de la base de datos o interfaz:
- **Regla 1:** Ej: Un cliente categorizado como VIP nunca puede ser cobrado con tarifa de consumidor final.
- **Regla 2:** Ej: El HWID debe ser alfanumérico de 8 a 64 caracteres.

---

## 3. Contratos de Entrada y Salida (DTOs / Interfaces)

### Input Contract (Entrada)
```python
# Ejemplo de DTO
class RegistrarVentaInput:
    sender_phone: str
    username: str
    hwid: str
    expiry_date: str # Formato ISO YYYY-MM-DD
```

### Output Contract (Respuesta Exitosa)
```python
class RegistrarVentaOutput:
    account_id: int
    client_id: int
    monto_cobrado: float
    ganancia_neta: float
    fecha_vencimiento: str
```

### Error Contract (Errores Previstos)
- `400 Bad Request`: Formato de HWID inválido.
- `404 Not Found`: Cliente no localizado y sin número de teléfono para asociar.

---

## 4. Batería de Pruebas del Harness (Criterios de Aceptación)
Todo código implementado debe pasar estos casos de prueba en el arnés antes de considerarse terminado:

1. **Happy Path:** Entrada válida produce el resultado esperado y registra la transacción en finanzas.
2. **Edge Case 1:** Mensaje recibido con espacios adicionales o mayúsculas.
3. **Edge Case 2:** Cliente existente con pagos pendientes es auto-aprobado.
4. **Failure Case:** Entrada corrupta devuelve error limpio sin romper la base de datos ni generar pagos fantasmas.

---

## 5. Cambios en Infraestructura y Persistencia
- [ ] Requiere migración aditiva en SQLite (`ALTER TABLE ... ADD COLUMN ...`).
- [ ] Requiere nuevo endpoint HTTP o webhook.
- [ ] Requiere plantilla de WhatsApp o botón en Telegram.

---

## 6. Observabilidad y Trazabilidad
- **Mensaje de Log Estructurado:** `[HTTP_CUSTOM_SALE] client_id={id} acc_id={id} amount={amount}`
- **Alerta de Telegram:** Confirmación con formato y emoticón distintivo.
