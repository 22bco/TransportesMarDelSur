"""Autenticación y usuarios del panel interno.

Hasta el Hito 1 el acceso era una contraseña única compartida. Eso bastaba
para cotizar, pero no para constancias: el documento afirma quién lo emitió, y
esa afirmación solo vale si detrás hay una persona identificable.

MIGRACIÓN SIN CORTE
El login nuevo sigue poniendo `session['admin'] = True` además de
`usuario_id` y `rol`. Así `admin.py` y sus seis plantillas, que leen esa marca,
no se tocan y no se arriesga lo único que ya estaba en producción.
"""
import os
from functools import wraps

from flask import (
    current_app, flash, redirect, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from constancias.dominio import ahora_utc, iso_utc


# scrypt es el default de Werkzeug 3: ~32 MB de RAM y ~100 ms por verificación.
# En un host sin swap eso es un mini-DoS si alguien golpea el login, pero el
# limit_req de nginx (5r/m) lo acota. Si aparece presión de memoria, basta
# cambiar esta variable a pbkdf2:sha256:600000 sin desplegar código.
METODO_HASH = os.environ.get('PASSWORD_HASH_METHOD', 'scrypt:32768:8:1')

LARGO_MINIMO_PASSWORD = 12


class PasswordDebil(ValueError):
    """La contraseña no cumple el mínimo exigido."""


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=METODO_HASH)


def validar_password(password: str, email: str = '') -> None:
    if len(password or '') < LARGO_MINIMO_PASSWORD:
        raise PasswordDebil(
            f'La contraseña debe tener al menos {LARGO_MINIMO_PASSWORD} caracteres.')
    obvias = {'password', 'contraseña', '123456789012', 'mardelsur',
              'transportes', 'qwertyuiop12'}
    if password.lower() in obvias:
        raise PasswordDebil('Esa contraseña es demasiado predecible.')
    if email and password.lower() == email.split('@')[0].lower():
        raise PasswordDebil('La contraseña no puede ser el nombre de la cuenta.')


# --- Usuarios ---------------------------------------------------------

def crear_usuario(email, nombre, password, rol='operador',
                  debe_cambiar_password=0):
    validar_password(password, email)
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO usuario (email, nombre, password_hash, rol,
                                 debe_cambiar_password, created_at)
            VALUES (?,?,?,?,?,?)
        """, (email.strip().lower(), nombre.strip(), hash_password(password),
              rol, debe_cambiar_password, iso_utc(ahora_utc())))
        return cur.lastrowid


def por_email(email):
    with get_db() as conn:
        fila = conn.execute(
            'SELECT * FROM usuario WHERE email = ? AND activo = 1',
            ((email or '').strip().lower(),)
        ).fetchone()
        return dict(fila) if fila else None


def por_id(usuario_id):
    with get_db() as conn:
        fila = conn.execute('SELECT * FROM usuario WHERE id = ?',
                            (usuario_id,)).fetchone()
        return dict(fila) if fila else None


def autenticar(email, password):
    """Devuelve el usuario si las credenciales son correctas, o None.

    Cuando el email no existe se verifica igual contra un hash señuelo: si no,
    el tiempo de respuesta delataría qué cuentas existen.
    """
    usuario = por_email(email)
    hash_guardado = usuario['password_hash'] if usuario and usuario['password_hash'] \
        else _hash_senuelo()
    valido = check_password_hash(hash_guardado, password or '')
    return usuario if (usuario and valido) else None


_SENUELO = None


def _hash_senuelo():
    global _SENUELO
    if _SENUELO is None:
        _SENUELO = hash_password('senuelo-para-igualar-el-tiempo-de-respuesta')
    return _SENUELO


def registrar_ingreso(usuario_id):
    with get_db() as conn:
        conn.execute('UPDATE usuario SET last_login_at = ? WHERE id = ?',
                     (iso_utc(ahora_utc()), usuario_id))


def cambiar_password(usuario_id, password_nueva):
    usuario = por_id(usuario_id)
    if not usuario:
        raise ValueError('El usuario no existe.')
    validar_password(password_nueva, usuario['email'])
    with get_db() as conn:
        conn.execute("""
            UPDATE usuario SET password_hash = ?, debe_cambiar_password = 0
             WHERE id = ?
        """, (hash_password(password_nueva), usuario_id))


def bootstrap_admin():
    """Crea el primer usuario si la tabla está vacía.

    Evita que el primer despliegue deje a todos fuera y evita también tener una
    contraseña escrita en un archivo de migración. Nace con
    debe_cambiar_password=1: la del .env es de arranque, no definitiva.
    """
    email = (os.environ.get('ADMIN_EMAIL') or '').strip()
    password = os.environ.get('ADMIN_PASSWORD') or ''
    if not email or not password:
        return None

    with get_db() as conn:
        if conn.execute('SELECT COUNT(*) AS n FROM usuario '
                        'WHERE password_hash IS NOT NULL').fetchone()['n']:
            return None

    try:
        validar_password(password, email)
    except PasswordDebil:
        # No se bloquea el arranque por esto, pero queda en el log.
        current_app.logger.warning(
            'ADMIN_PASSWORD es débil; el usuario inicial se crea igual y '
            'deberá cambiarla al entrar.')

    with get_db() as conn:
        existente = conn.execute('SELECT id FROM usuario WHERE email = ?',
                                 (email.lower(),)).fetchone()
        if existente:
            conn.execute("""
                UPDATE usuario SET password_hash = ?, rol = 'admin',
                                   debe_cambiar_password = 1
                 WHERE id = ?
            """, (hash_password(password), existente['id']))
            return existente['id']
        cur = conn.execute("""
            INSERT INTO usuario (email, nombre, password_hash, rol,
                                 debe_cambiar_password, created_at)
            VALUES (?,?,?, 'admin', 1, ?)
        """, (email.lower(), 'Administrador', hash_password(password),
              iso_utc(ahora_utc())))
        return cur.lastrowid


def permitir_login_legacy() -> bool:
    """Ventana de compatibilidad con la contraseña única compartida.

    Se apaga poniendo PERMITIR_LOGIN_LEGACY=0, y entonces se borra
    ADMIN_PASSWORD del .env.
    """
    return os.environ.get('PERMITIR_LOGIN_LEGACY', '1') == '1'


def iniciar_sesion(usuario=None):
    """Marca la sesión. `session['admin']` se conserva por compatibilidad."""
    session.clear()
    session['admin'] = True
    session.permanent = True
    if usuario:
        session['usuario_id'] = usuario['id']
        session['usuario_nombre'] = usuario['nombre']
        session['rol'] = usuario['rol']
        session['debe_cambiar_password'] = bool(usuario['debe_cambiar_password'])
    else:
        # Acceso legacy: no hay persona detrás. Las constancias emitidas así se
        # atribuyen al usuario semilla, que es justamente lo que se quiere
        # dejar de hacer.
        session['rol'] = 'admin'


# --- Decoradores ------------------------------------------------------

def login_required(view):
    """Exige sesión iniciada; si no, redirige al login conservando el destino."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin.login', next=request.path))
        if session.get('debe_cambiar_password') and \
                request.endpoint != 'admin.cambiar_password':
            return redirect(url_for('admin.cambiar_password'))
        return view(*args, **kwargs)
    return wrapped


def rol_requerido(*roles):
    """Restringe una vista a ciertos roles.

    El acceso legacy queda como 'admin' porque es la contraseña maestra; en
    cuanto se apague, solo los usuarios nominados con rol admin pasarán.
    """
    def decorador(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get('rol') not in roles:
                flash('No tiene permisos para esa acción.', 'error')
                return redirect(url_for('admin.cotizaciones'))
            return view(*args, **kwargs)
        return wrapped
    return decorador


def destino_seguro(next_url, por_defecto):
    """Valida el ?next= del login.

    Solo rutas internas del panel. Sin esto, un enlace
    `/admin/login?next=https://sitio-malicioso.cl` convertiría el login en un
    redirector abierto: el usuario ve el dominio correcto, se autentica, y
    termina en otra parte. `//otro-host.cl` empieza por '/' pero el navegador
    la trata como absoluta, así que también se descarta.
    """
    if not next_url or not next_url.startswith('/admin'):
        return por_defecto
    if next_url.startswith('//'):
        return por_defecto
    return next_url
