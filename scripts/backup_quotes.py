#!/usr/bin/env python3
"""
Respaldo automático de la base de datos de cotizaciones de
Transportes Mar del Sur.

- Hace una copia CONSISTENTE de quotes.db usando la API de respaldo
  en línea de sqlite3 (segura aunque la BD se esté escribiendo, incl. modo WAL).
- Comprime la copia con gzip: quotes-YYYYMMDD-HHMMSS.db.gz
- Borra respaldos con más de RETENTION_DAYS días.
- Registra cada ejecución en backup.log.
- Maneja errores sin romper; sale con código != 0 ante fallo.
"""

import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta

SRC_DB = "/opt/TransportesMarDelSur/docker-data/quotes/quotes.db"
BACKUP_DIR = "/opt/TransportesMarDelSur/docker-data/backups"
LOG_FILE = os.path.join(BACKUP_DIR, "backup.log")
RETENTION_DAYS = 30


def log(message):
    """Escribe una línea con timestamp en el log y en stdout."""
    line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line)
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:  # noqa: BLE001
        print("WARN: no se pudo escribir el log: %s" % exc)


def consistent_backup(src_path, dst_path):
    """Copia consistente de la BD SQLite usando la API de respaldo en línea."""
    src = sqlite3.connect("file:%s?mode=ro" % src_path, uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def cleanup_old_backups():
    """Elimina respaldos con más de RETENTION_DAYS días de antigüedad."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    removed = 0
    for name in os.listdir(BACKUP_DIR):
        if not (name.startswith("quotes-") and name.endswith(".db.gz")):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as exc:  # noqa: BLE001
            log("WARN: no se pudo evaluar/borrar %s: %s" % (name, exc))
    if removed:
        log("Limpieza: %d respaldo(s) con mas de %d dias eliminado(s)."
            % (removed, RETENTION_DAYS))


def main():
    if not os.path.isfile(SRC_DB):
        log("ERROR: la base de datos no existe: %s" % SRC_DB)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final_path = os.path.join(BACKUP_DIR, "quotes-%s.db.gz" % timestamp)
    tmp_db = None

    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)

        # 1) Copia consistente a un archivo temporal.
        fd, tmp_db = tempfile.mkstemp(suffix=".db", dir=BACKUP_DIR)
        os.close(fd)
        consistent_backup(SRC_DB, tmp_db)

        # 2) Comprime con gzip.
        with open(tmp_db, "rb") as f_in, gzip.open(final_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        size = os.path.getsize(final_path)
        log("OK: respaldo creado %s (%d bytes / %.1f KB)"
            % (os.path.basename(final_path), size, size / 1024.0))
    except Exception as exc:  # noqa: BLE001
        log("ERROR: fallo al crear el respaldo: %s" % exc)
        if final_path and os.path.exists(final_path):
            try:
                os.remove(final_path)
            except OSError:
                pass
        return 2
    finally:
        if tmp_db and os.path.exists(tmp_db):
            try:
                os.remove(tmp_db)
            except OSError:
                pass

    # 3) Retención (no aborta el respaldo si falla).
    try:
        cleanup_old_backups()
    except Exception as exc:  # noqa: BLE001
        log("WARN: fallo durante la limpieza de respaldos antiguos: %s" % exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
