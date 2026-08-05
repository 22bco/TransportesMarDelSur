"""Autenticación del panel interno.

Hito 0.5: solo se extrae `login_required` desde admin.py, sin cambiar el
comportamiento. La tabla `usuario` con login nominado llega en el Hito 2; hasta
entonces la sesión sigue siendo la marca booleana `session['admin']` que leen
admin.py y las seis plantillas de templates/admin/.
"""
from functools import wraps

from flask import redirect, request, session, url_for


def login_required(view):
    """Exige sesión iniciada; si no, redirige al login conservando el destino."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def destino_seguro(next_url, por_defecto):
    """Valida el ?next= del login.

    Solo se aceptan rutas internas del panel. Sin esto, un enlace
    `/admin/login?next=https://sitio-malicioso.cl` convertiría el login en un
    redirector abierto: el usuario ve el dominio correcto, se autentica, y
    termina en otra parte.
    """
    if not next_url or not next_url.startswith('/admin'):
        return por_defecto
    # '//otro-host.cl' es una URL protocol-relative: el navegador la trata como
    # absoluta pese a empezar con '/'.
    if next_url.startswith('//'):
        return por_defecto
    return next_url
