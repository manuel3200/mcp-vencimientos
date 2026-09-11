import os
import sqlite3
from datetime import datetime, date
from typing import List, Dict, Any, Optional

DB_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DB_DIR, "services.db")

def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa la base de datos y crea la tabla si no existe."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT 'Servicio',
                    expiry_date TEXT NOT NULL,
                    recurrence TEXT DEFAULT 'mensual',
                    cost TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    last_alert_sent TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    finally:
        conn.close()

def add_service(name: str, expiry_date: str, category: str = "Servicio", 
                recurrence: str = "mensual", cost: str = "", notes: str = "") -> Dict[str, Any]:
    """Registra un nuevo servicio o suscripción."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO services (name, category, expiry_date, recurrence, cost, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name.strip(), category.strip(), expiry_date.strip(), recurrence.strip(), cost.strip(), notes.strip()))
            service_id = cursor.lastrowid
            
            row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
            return dict(row)
    finally:
        conn.close()

def list_services() -> List[Dict[str, Any]]:
    """Obtiene todos los servicios ordenados por fecha de vencimiento."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM services ORDER BY expiry_date ASC").fetchall()
        today = date.today()
        result = []
        for row in rows:
            data = dict(row)
            try:
                exp_date = datetime.strptime(data["expiry_date"], "%Y-%m-%d").date()
                diff_days = (exp_date - today).days
                data["days_remaining"] = diff_days
                if diff_days < 0:
                    data["status"] = "vencido"
                elif diff_days <= 2:
                    data["status"] = "por_vencer"
                else:
                    data["status"] = "activo"
            except Exception:
                data["days_remaining"] = None
                data["status"] = "fecha_invalida"
            result.append(data)
        return result
    finally:
        conn.close()

def get_expiring_services(days_window: int = 2) -> List[Dict[str, Any]]:
    """Devuelve los servicios que vencen en los próximos `days_window` días o ya vencidos."""
    all_svcs = list_services()
    expiring = []
    for s in all_svcs:
        days = s.get("days_remaining")
        if days is not None and days <= days_window:
            expiring.append(s)
    return expiring

def delete_service(service_id: int) -> bool:
    """Elimina un servicio por su ID."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()

def update_service_date(service_id: int, new_expiry_date: str) -> bool:
    """Actualiza la fecha de vencimiento de un servicio (ej. tras renovar)."""
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("""
                UPDATE services 
                SET expiry_date = ?, last_alert_sent = ''
                WHERE id = ?
            """, (new_expiry_date.strip(), service_id))
            return cursor.rowcount > 0
    finally:
        conn.close()

def mark_alert_sent(service_id: int, alert_date: str):
    """Marca la fecha en que se envió la última alerta para evitar duplicados en el mismo día."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE services 
                SET last_alert_sent = ?
                WHERE id = ?
            """, (alert_date, service_id))
    finally:
        conn.close()
