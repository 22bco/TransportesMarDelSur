"""Acceso a la base de datos SQLite, compartido por todos los módulos.

Extraído de admin.py en el Hito 0.5 para que el panel de cotizaciones y el
módulo de constancias usen la misma conexión, la misma configuración y —lo que
importa de verdad— la misma base, de modo que las claves foráneas entre tablas
de ambos módulos sean reales.
"""
import contextlib
import os
import sqlite3
from pathlib import Path

from flask import current_app


# Ruta por defecto. `QUOTES_DB_DIR` se mantiene por compatibilidad con el
# despliegue actual; `APP_DB_PATH` es la forma nueva y prepara el renombre
# futuro de quotes.db a mardelsur.db.
DB_DIR_DEFECTO = Path(os.environ.get('QUOTES_DB_DIR', '/app/data'))
DB_PATH_DEFECTO = Path(os.environ.get('APP_DB_PATH', DB_DIR_DEFECTO / 'quotes.db'))


def db_path() -> Path:
    """Ruta del archivo .db en uso.

    Prefiere `current_app.config['DB_PATH']` cuando hay contexto de aplicación:
    es lo que permite que los tests apunten a una base temporal sin tocar
    variables de entorno ni levantar Docker.
    """
    try:
        configurado = current_app.config.get('DB_PATH')
        if configurado:
            return Path(configurado)
    except RuntimeError:
        # Fuera de contexto de aplicación (scripts de cron, python -m ...).
        pass
    return DB_PATH_DEFECTO


@contextlib.contextmanager
def get_db():
    """Context manager de conexión a la BD.

    Garantiza que la conexión SIEMPRE se cierre: hace commit si el bloque
    termina bien, rollback si lanza una excepción, y close() en todos los casos.
    """
    ruta = db_path()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    # Sin este PRAGMA las claves foráneas son decorativas: SQLite las ignora
    # por defecto, y hay que activarlas en CADA conexión.
    conn.execute("PRAGMA foreign_keys = ON")
    # Espera hasta 5s si la BD está bloqueada por otro worker antes de
    # lanzar "database is locked" (mejora la robustez bajo concurrencia).
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_wal():
    """Activa WAL: los lectores no bloquean al escritor y viceversa.

    Es un ajuste persistente del archivo .db, basta ejecutarlo una vez, pero es
    idempotente y barato.
    """
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
