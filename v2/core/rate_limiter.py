import time
import threading
from collections import deque
from typing import Dict, List, Tuple, Optional


class TokenBucket:
    """Implementación clásica de Token Bucket con reposición por tiempo."""

    def __init__(self, capacity: float, fill_rate: float):
        """
        capacity: Número máximo de tokens que puede contener el bucket.
        fill_rate: Tasa de recarga en tokens por segundo.
        """
        self.capacity: float = float(capacity)
        self.tokens: float = float(capacity)
        self.fill_rate: float = float(fill_rate)
        self.last_update: float = time.time()
        self._lock = threading.Lock()

    def consume(self, amount: float = 1.0) -> bool:
        """Intenta consumir 'amount' tokens del bucket.
        Retorna True si había suficientes tokens, o False en caso contrario.
        """
        with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)

            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False

    def get_tokens(self) -> float:
        """Retorna la cantidad actual de tokens disponibles tras recarga proporcional."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            return min(self.capacity, self.tokens + elapsed * self.fill_rate)


class RateLimiter:
    """Gestor de limitación de tasa y throttling perimetral para usuarios, grupos y comandos."""

    def __init__(self):
        self._lock = threading.Lock()
        # sender_phone -> list of request timestamps in last 60s
        self._user_requests: Dict[str, List[float]] = {}
        # sender_phone -> (cooldown_until, violation_level)
        self._user_cooldowns: Dict[str, Tuple[float, int]] = {}
        # group_jid -> list of message timestamps in last 60s
        self._group_requests: Dict[str, List[float]] = {}
        # group_jid -> last /tagall timestamp
        self._tagall_last: Dict[str, float] = {}
        # admin_id -> last /backup timestamp
        self._backup_last: Dict[str, float] = {}

    def check_user_rate_limit(self, sender_phone: str) -> Tuple[bool, str]:
        """Permite máx 10 peticiones/minuto por remitente.
        Si excede, aplica cooldown progresivo (60s, 300s, 900s) y retorna (False, "user_rate_limit_exceeded").
        """
        if not sender_phone:
            return True, "ok"

        phone = str(sender_phone).strip()
        now = time.time()

        with self._lock:
            # 1. Verificar si está en cooldown activo
            if phone in self._user_cooldowns:
                cooldown_until, level = self._user_cooldowns[phone]
                if now < cooldown_until:
                    return False, "user_rate_limit_exceeded"
                else:
                    # El cooldown expiró; si pasaron más de 10 minutos sin infracciones, reseteamos nivel
                    if now - cooldown_until > 600:
                        del self._user_cooldowns[phone]

            # 2. Filtrar peticiones del último minuto (60s)
            reqs = [t for t in self._user_requests.get(phone, []) if now - t < 60.0]

            if len(reqs) >= 10:
                current_level = self._user_cooldowns.get(phone, (0.0, 0))[1]
                new_level = current_level + 1
                cooldown_durations = {1: 60.0, 2: 300.0}
                cooldown_sec = cooldown_durations.get(new_level, 900.0)

                self._user_cooldowns[phone] = (now + cooldown_sec, new_level)
                self._user_requests[phone] = reqs
                return False, "user_rate_limit_exceeded"

            reqs.append(now)
            self._user_requests[phone] = reqs
            return True, "ok"

    def get_user_cooldown(self, sender_phone: str) -> Tuple[bool, float]:
        """Consulta si el usuario está en cooldown y cuántos segundos le restan."""
        if not sender_phone:
            return False, 0.0
        phone = str(sender_phone).strip()
        now = time.time()
        with self._lock:
            if phone in self._user_cooldowns:
                cooldown_until, _ = self._user_cooldowns[phone]
                if now < cooldown_until:
                    return True, max(0.0, cooldown_until - now)
            return False, 0.0

    def check_group_rate_limit(self, group_jid: str) -> Tuple[bool, str]:
        """Permite máx 25 mensajes/minuto por grupo.
        Si excede, retorna (False, "group_rate_limit_exceeded").
        """
        if not group_jid:
            return True, "ok"

        jid = str(group_jid).strip()
        now = time.time()

        with self._lock:
            reqs = [t for t in self._group_requests.get(jid, []) if now - t < 60.0]
            if len(reqs) >= 25:
                self._group_requests[jid] = reqs
                return False, "group_rate_limit_exceeded"

            reqs.append(now)
            self._group_requests[jid] = reqs
            return True, "ok"

    def check_tagall_throttle(self, group_jid: str) -> Tuple[bool, str]:
        """Restringe /tagall a 1 ejecución cada 10 minutos (600s) por grupo.
        Retorna (False, "tagall_throttled: <segundos_restantes>").
        """
        if not group_jid:
            return True, "ok"

        jid = str(group_jid).strip()
        now = time.time()

        with self._lock:
            if jid in self._tagall_last:
                elapsed = now - self._tagall_last[jid]
                if elapsed < 600.0:
                    remaining = int(600.0 - elapsed)
                    return False, f"tagall_throttled: {remaining}"

            self._tagall_last[jid] = now
            return True, "ok"

    def check_backup_throttle(self, admin_id: str) -> Tuple[bool, str]:
        """Restringe /backup a 1 cada 15 minutos (900s).
        Retorna (False, "backup_throttled: <segundos_restantes>").
        """
        if not admin_id:
            return True, "ok"

        aid = str(admin_id).strip()
        now = time.time()

        with self._lock:
            if aid in self._backup_last:
                elapsed = now - self._backup_last[aid]
                if elapsed < 900.0:
                    remaining = int(900.0 - elapsed)
                    return False, f"backup_throttled: {remaining}"

            self._backup_last[aid] = now
            return True, "ok"


class ReceiptQuotaManager:
    """Control de cuota horaria de comprobantes y caché perceptual de no-comprobantes."""

    def __init__(self, max_cache_size: int = 500):
        self._lock = threading.Lock()
        # sender_phone -> list of timestamps in last 3600s
        self._receipt_requests: Dict[str, List[float]] = {}
        # FIFO cache of non-receipt pHashes (16-char hex)
        self._non_receipt_cache: deque = deque(maxlen=max_cache_size)
        self._non_receipt_set: set = set()

    @staticmethod
    def _hamming_distance(h1: str, h2: str) -> int:
        """Calcula distancia Hamming entre dos hashes hexadecimales de 64 bits (16 chars)."""
        if not h1 or not h2:
            return 999
        h1_clean = str(h1).strip().lower()
        h2_clean = str(h2).strip().lower()
        if len(h1_clean) != 16 or len(h2_clean) != 16:
            return 999
        try:
            val1 = int(h1_clean, 16)
            val2 = int(h2_clean, 16)
            return bin(val1 ^ val2).count("1")
        except (ValueError, TypeError):
            return 999

    def check_receipt_quota(self, sender_phone: str) -> Tuple[bool, str]:
        """Límite de 5 comprobantes por hora por teléfono.
        Si supera 5, retorna (False, "receipt_hourly_quota_exceeded").
        """
        if not sender_phone:
            return True, "ok"

        phone = str(sender_phone).strip()
        now = time.time()

        with self._lock:
            timestamps = [t for t in self._receipt_requests.get(phone, []) if now - t < 3600.0]
            if len(timestamps) >= 5:
                self._receipt_requests[phone] = timestamps
                return False, "receipt_hourly_quota_exceeded"

            timestamps.append(now)
            self._receipt_requests[phone] = timestamps
            return True, "ok"

    @staticmethod
    def validate_image_constraints(image_bytes: bytes) -> Tuple[bool, str]:
        """Pre-filtro de tamaño y resolución para comprobantes."""
        if not image_bytes:
            return False, "bytes de imagen vacíos"
        if len(image_bytes) > 8 * 1024 * 1024:
            return False, "Imagen rechazada por tamaño excesivo (> 8MB)"
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
                if width < 200 or height < 200:
                    return False, f"Imagen rechazada por resolución insuficiente ({width}x{height} < 200x200)"
            return True, "ok"
        except Exception as e:
            return False, f"Error comprobando imagen: {e}"

    def is_known_non_receipt(self, phash: str) -> bool:
        """Consulta caché en memoria de pHashes de imágenes previamente clasificadas como NO comprobante.
        Si la distancia Hamming <= 4 contra alguno registrado, retorna True.
        """
        if not phash:
            return False

        h_clean = str(phash).strip().lower()
        if len(h_clean) != 16:
            return False

        with self._lock:
            if h_clean in self._non_receipt_set:
                return True

            for cached_hash in self._non_receipt_cache:
                if self._hamming_distance(h_clean, cached_hash) <= 4:
                    return True

        return False

    def record_non_receipt(self, phash: str) -> None:
        """Registra un pHash en la caché de no-comprobantes (mantiene hasta 500 registros FIFO)."""
        if not phash:
            return

        h_clean = str(phash).strip().lower()
        if len(h_clean) != 16:
            return

        with self._lock:
            if h_clean in self._non_receipt_set:
                return

            # Si el deque está al tope, eliminar el elemento expulsado del set
            if len(self._non_receipt_cache) >= self._non_receipt_cache.maxlen:
                oldest = self._non_receipt_cache[0]
                self._non_receipt_set.discard(oldest)

            self._non_receipt_cache.append(h_clean)
            self._non_receipt_set.add(h_clean)


class CircuitBreaker:
    """Circuit Breaker para llamadas externas (Gemini Vision, APIs).
    Estados: 'CLOSED', 'OPEN', 'HALF_OPEN'.
    fail_threshold: 3 fallos consecutivos.
    reset_timeout: 60.0 segundos.
    """

    def __init__(self, fail_threshold: int = 3, reset_timeout: float = 60.0):
        self.fail_threshold: int = fail_threshold
        self.reset_timeout: float = reset_timeout
        self._state: str = "CLOSED"
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """Determina si la llamada protegida puede ejecutarse según el estado del circuito."""
        with self._lock:
            now = time.time()
            if self._state == "CLOSED":
                return True
            elif self._state == "OPEN":
                if now - self._last_failure_time >= self.reset_timeout:
                    self._state = "HALF_OPEN"
                    return True
                return False
            elif self._state == "HALF_OPEN":
                # Permite una petición de prueba
                return True
            return True

    def record_success(self) -> None:
        """Registra un éxito y restablece el circuito a estado CLOSED."""
        with self._lock:
            self._failure_count = 0
            self._state = "CLOSED"

    def record_failure(self) -> None:
        """Registra un fallo. Si supera el umbral o está en HALF_OPEN, abre el circuito."""
        with self._lock:
            self._last_failure_time = time.time()
            if self._state == "HALF_OPEN":
                self._state = "OPEN"
            else:
                self._failure_count += 1
                if self._failure_count >= self.fail_threshold:
                    self._state = "OPEN"

    def get_state(self) -> str:
        """Retorna el estado actual del circuito ('CLOSED', 'OPEN', 'HALF_OPEN')."""
        with self._lock:
            now = time.time()
            if self._state == "OPEN" and (now - self._last_failure_time >= self.reset_timeout):
                self._state = "HALF_OPEN"
            return self._state


class AuthRateLimiter:
    """Limitador de intentos de autenticación y protección contra ataques de fuerza bruta.
    Máx 5 intentos en ventana de 15 minutos; bloqueo de 30 minutos al superar el umbral.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: float = 900.0,
        lockout_seconds: float = 1800.0,
        max_failed_attempts: Optional[int] = None
    ):
        self.max_attempts: int = int(max_failed_attempts) if max_failed_attempts is not None else int(max_attempts)
        self.window_seconds: float = float(window_seconds)
        self.lockout_seconds: float = float(lockout_seconds)
        self._failed_attempts: Dict[str, List[float]] = {}
        self._lockouts: Dict[str, float] = {}
        self._lock = threading.Lock()

    def record_failed_attempt(self, identifier: str) -> None:
        """Registra un intento fallido para el identificador (ej: IP o usuario).
        Aplica bloqueo si alcanza 5 fallos dentro de la ventana de 15 minutos.
        """
        if not identifier:
            return

        ident = str(identifier).strip().lower()
        now = time.time()

        with self._lock:
            attempts = [t for t in self._failed_attempts.get(ident, []) if now - t < self.window_seconds]
            attempts.append(now)
            self._failed_attempts[ident] = attempts

            if len(attempts) >= self.max_attempts:
                self._lockouts[ident] = now + self.lockout_seconds

    def is_locked_out(self, identifier: str) -> Tuple[bool, int]:
        """Retorna (True, segundos_restantes) si acumula 5 intentos fallidos dentro de la ventana de 30 minutos de bloqueo.
        Retorna (False, 0) si no está bloqueado.
        """
        if not identifier:
            return False, 0

        ident = str(identifier).strip().lower()
        now = time.time()

        with self._lock:
            if ident in self._lockouts:
                cooldown_until = self._lockouts[ident]
                if now < cooldown_until:
                    remaining = int(cooldown_until - now)
                    return True, max(1, remaining)
                else:
                    # Bloqueo concluido
                    del self._lockouts[ident]
                    self._failed_attempts.pop(ident, None)

            return False, 0

    def reset_attempts(self, identifier: str) -> None:
        """Limpia el historial de fallos tras un inicio de sesión o verificación exitosa."""
        if not identifier:
            return

        ident = str(identifier).strip().lower()
        with self._lock:
            self._failed_attempts.pop(ident, None)
            self._lockouts.pop(ident, None)


# Instancias singleton modulares preconfiguradas
rate_limiter = RateLimiter()
receipt_quota_manager = ReceiptQuotaManager()
gemini_circuit_breaker = CircuitBreaker(fail_threshold=3, reset_timeout=60.0)
auth_rate_limiter = AuthRateLimiter(max_attempts=5, window_seconds=900.0, lockout_seconds=1800.0)
