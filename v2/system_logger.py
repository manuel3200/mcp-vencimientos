import os
import sys
import logging
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from collections import deque
from typing import List, Dict, Any, Optional

LOG_DIR = os.getenv("DATA_DIR", "/app/data")
LOG_PATH = os.path.join(LOG_DIR, "system.log")

_START_TIME = datetime.now()
_LOG_BUFFER: deque = deque(maxlen=600)
_LOG_COUNTER = 0
_IS_INITIALIZED = False

class MemoryLogHandler(logging.Handler):
    """Handler que guarda los logs en un buffer circular en memoria para consumo rápido del panel web y API."""
    def emit(self, record: logging.LogRecord):
        global _LOG_COUNTER
        try:
            msg = self.format(record)
            tb = ""
            if record.exc_info:
                tb = "".join(traceback.format_exception(*record.exc_info))

            _LOG_COUNTER += 1
            entry = {
                "id": _LOG_COUNTER,
                "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "module": record.name,
                "message": record.getMessage(),
                "traceback": tb,
                "formatted": msg
            }
            _LOG_BUFFER.append(entry)
        except Exception:
            self.handleError(record)

def setup_system_logging(log_dir: Optional[str] = None):
    """Inicializa la configuración de logging unificada del sistema con consola, archivo rotativo y buffer en memoria."""
    global _IS_INITIALIZED
    if _IS_INITIALIZED:
        return
    _IS_INITIALIZED = True

    target_dir = log_dir or LOG_DIR
    try:
        os.makedirs(target_dir, exist_ok=True)
        file_target = os.path.join(target_dir, "system.log")
    except Exception:
        target_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(target_dir, exist_ok=True)
        file_target = os.path.join(target_dir, "system.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Evitar duplicación de handlers si se recarga
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Consola (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # 2. Archivo rotativo en disco (5MB, 3 backups)
    try:
        file_handler = RotatingFileHandler(
            filename=file_target,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"[system_logger] No se pudo inicializar RotatingFileHandler en {file_target}: {e}", file=sys.stderr)

    # 3. Buffer en memoria para la interfaz web y API
    mem_handler = MemoryLogHandler()
    mem_handler.setFormatter(formatter)
    mem_handler.setLevel(logging.INFO)
    root_logger.addHandler(mem_handler)

    logging.getLogger("system_logger").info(f"Sistema de logging centralizado activado. Archivo: {file_target}")

def get_recent_logs(
    level: Optional[str] = None,
    module: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 150
) -> List[Dict[str, Any]]:
    """Obtiene los logs más recientes desde el buffer en memoria con filtros opcionales."""
    logs = list(_LOG_BUFFER)
    
    if level and level.strip().upper() != "ALL":
        target_level = level.strip().upper()
        logs = [entry for entry in logs if entry["level"] == target_level]

    if module and module.strip():
        m = module.strip().lower()
        logs = [entry for entry in logs if m in entry["module"].lower()]

    if query and query.strip():
        q = query.strip().lower()
        logs = [
            entry for entry in logs 
            if q in entry["message"].lower() or q in entry["module"].lower() or q in entry.get("traceback", "").lower()
        ]

    # Devolver los más recientes primero
    return list(reversed(logs[-limit:]))

def get_system_health_report() -> Dict[str, Any]:
    """Genera un reporte de diagnóstico de salud de los servicios clave del sistema."""
    now = datetime.now()
    uptime_sec = int((now - _START_TIME).total_seconds())
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    # Conteo de errores en memoria
    all_logs = list(_LOG_BUFFER)
    error_logs = [l for l in all_logs if l["level"] in ("ERROR", "CRITICAL")]
    warn_logs = [l for l in all_logs if l["level"] == "WARNING"]
    last_error = error_logs[-1] if error_logs else None

    # Estado de Telegram Bot
    telegram_status = "Inactivo"
    try:
        import telegram_bot
        if getattr(telegram_bot, "_polling_active", False):
            telegram_status = "Activo (Polling)"
        else:
            token, chat = telegram_bot.get_telegram_config()
            telegram_status = "Configurado" if token and chat else "No configurado"
    except Exception as e:
        telegram_status = f"Error: {e}"

    # Estado de Base de Datos
    db_status = "Desconocido"
    db_size_str = "0 KB"
    db_accounts_count = 0
    try:
        import database
        conn = database.get_connection()
        row = conn.execute("SELECT COUNT(*) as c FROM streaming_accounts").fetchone()
        db_accounts_count = row["c"] if row else 0
        conn.close()
        db_status = "Conectada (OK)"
        if os.path.exists(database.DB_PATH):
            size_b = os.path.getsize(database.DB_PATH)
            db_size_str = f"{size_b / 1024:.1f} KB"
    except Exception as e:
        db_status = f"Falla de conexión: {e}"

    # Estado del Scheduler
    scheduler_status = "Inactivo"
    try:
        import scheduler
        if hasattr(scheduler, "scheduler") and scheduler.scheduler.running:
            scheduler_status = f"Activo ({len(scheduler.scheduler.get_jobs())} tareas)"
        else:
            scheduler_status = "Detenido"
    except Exception as e:
        scheduler_status = f"Error: {e}"

    return {
        "status": "OK" if len(error_logs) == 0 else ("WARNING" if len(error_logs) < 5 else "CRITICAL"),
        "uptime": uptime_str,
        "database": {
            "status": db_status,
            "path": getattr(database, "DB_PATH", ""),
            "size": db_size_str,
            "total_accounts": db_accounts_count
        },
        "telegram": {
            "status": telegram_status
        },
        "scheduler": {
            "status": scheduler_status
        },
        "logs_summary": {
            "total_buffered": len(all_logs),
            "errors_count": len(error_logs),
            "warnings_count": len(warn_logs),
            "last_error": {
                "timestamp": last_error["timestamp"],
                "module": last_error["module"],
                "message": last_error["message"]
            } if last_error else None
        }
    }

def get_raw_log_file(max_lines: int = 3000) -> str:
    """Lee el contenido en texto plano del archivo de log para descargarlo."""
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return "".join(lines[-max_lines:])
    except Exception:
        pass
    
    # Fallback al buffer en memoria formateado
    lines = [entry["formatted"] for entry in _LOG_BUFFER]
    return "\n".join(lines[-max_lines:])

def clear_memory_logs():
    """Limpia el buffer de logs en memoria."""
    global _LOG_COUNTER
    _LOG_BUFFER.clear()
    _LOG_COUNTER = 0
