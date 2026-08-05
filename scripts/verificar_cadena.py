#!/usr/bin/env python3
"""Verifica la cadena de integridad completa.

    docker exec -u appuser mardelsur_web python scripts/verificar_cadena.py

Recorre todas las constancias en orden, comprueba que cada una encadena con la
anterior y recalcula su hash desde el contenido guardado. Después coteja cada
anclaje publicado contra la fila que decía anclar — eso último es lo que
detecta una manipulación POSTERIOR a un anclaje, que es el escenario que
importa: una vez que el hash salió por correo a un tercero, ya no se puede
reescribir la historia sin que quede a la vista.

Sale con código != 0 si encuentra cualquier problema, para que sirva en cron.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import crear_app                                       # noqa: E402
from constancias.cadena import verificar_anclajes, verificar_cadena  # noqa: E402
from db import get_db                                           # noqa: E402


def main():
    app = crear_app()
    with app.app_context(), get_db() as conn:
        ok_cadena, n, problemas = verificar_cadena(conn)
        ok_anclajes, n_anclajes, problemas_anclaje = verificar_anclajes(conn)

    if ok_cadena:
        print(f'OK: {n} eslabón(es) verificado(s).')
    else:
        print(f'CADENA ROTA ({len(problemas)} problema(s)):')
        for p in problemas:
            print(f'  - {p}')

    if ok_anclajes:
        print(f'OK: {n_anclajes} anclaje(s) coinciden con su constancia.')
    else:
        print(f'ANCLAJES INCONSISTENTES ({len(problemas_anclaje)}):')
        for p in problemas_anclaje:
            print(f'  - {p}')

    return 0 if (ok_cadena and ok_anclajes) else 1


if __name__ == '__main__':
    sys.exit(main())
