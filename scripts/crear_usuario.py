#!/usr/bin/env python3
"""Crea un usuario del panel.

    docker exec -it mardelsur_web python scripts/crear_usuario.py \
        --email nombre@transportesmardelsur.cl --nombre "Nombre Apellido" --rol operador

La contraseña se pide por consola, NUNCA por argumento: en argv quedaría en el
historial del shell y visible en `ps` para cualquiera con acceso al servidor.
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import crear_app                     # noqa: E402
import auth                                   # noqa: E402


def main():
    p = argparse.ArgumentParser(description='Crea un usuario del panel interno.')
    p.add_argument('--email', required=True)
    p.add_argument('--nombre', required=True)
    p.add_argument('--rol', default='operador', choices=['admin', 'operador'])
    p.add_argument('--debe-cambiar', action='store_true',
                   help='Obliga a cambiar la contraseña en el primer ingreso.')
    args = p.parse_args()

    app = crear_app()
    with app.app_context():
        if auth.por_email(args.email):
            print(f'Ya existe un usuario con el correo {args.email}.')
            return 1

        password = getpass.getpass('Contraseña: ')
        if password != getpass.getpass('Repetir: '):
            print('Las contraseñas no coinciden.')
            return 1

        try:
            uid = auth.crear_usuario(args.email, args.nombre, password,
                                     rol=args.rol,
                                     debe_cambiar_password=int(args.debe_cambiar))
        except auth.PasswordDebil as e:
            print(f'Contraseña rechazada: {e}')
            return 1

    print(f'Usuario {args.email} creado (id={uid}, rol={args.rol}).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
