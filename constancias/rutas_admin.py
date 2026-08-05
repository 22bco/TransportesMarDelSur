"""Panel interno de constancias.

Hito 1 (walking skeleton): emitir, listar, ver y descargar el PDF. Sin CRUD de
maestros, sin adjuntos, sin firma y sin anulación — eso llega en los hitos 2 y
3. Los maestros se siembran con scripts/seed_dev.sql.
"""
import os
import sqlite3

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, session, url_for,
)

from auth import login_required, rol_requerido
from db import get_db
from .dominio import (
    CantidadInvalida, RutInvalido, a_centesimas, fmt_m3_es, formatear_codigo,
    normalizar_rut,
)
from .pdf import ETIQUETA_METODO, ETIQUETA_OPERACION, respuesta_pdf, url_verificacion
from .ciclo_vida import (
    AnulacionInvalida, anular, datos_para_reemplazo, reemplazar,
)
from .repositorio import DatosInvalidos, emitir, listar, obtener


constancias_bp = Blueprint('constancias', __name__, url_prefix='/admin/constancias')


def _empresa():
    return {
        'nombre': os.environ.get('EMPRESA_NOMBRE', 'Transportes Mar del Sur SPA'),
        'rut': os.environ.get('EMPRESA_RUT', '77.779.818-9'),
        'direccion': os.environ.get('EMPRESA_DIRECCION', 'Puerto Montt, Los Lagos'),
        'resolucion': os.environ.get('EMPRESA_RESOLUCION',
                                     'Resolución Sanitaria N° 2510389969'),
    }


def _catalogos():
    """Opciones de los selectores del formulario."""
    with get_db() as conn:
        q = lambda sql: [dict(f) for f in conn.execute(sql)]
        return {
            'clientes': q("""SELECT id, razon_social, rut FROM cliente
                              WHERE activo = 1 ORDER BY razon_social"""),
            'direcciones': q("""SELECT d.id, d.cliente_id, d.nombre_referencia,
                                       d.calle, d.numero, c.nombre AS comuna
                                  FROM direccion_retiro d
                                  JOIN comuna c ON c.id = d.comuna_id
                                 WHERE d.activo = 1
                                 ORDER BY d.nombre_referencia"""),
            'materiales': q("""SELECT id, nombre FROM tipo_material
                                WHERE activo = 1 ORDER BY nombre"""),
            'contenedores': q("""SELECT id, nombre FROM tipo_contenedor
                                  WHERE activo = 1 ORDER BY nombre"""),
            'destinos': q("""SELECT d.id, d.nombre, d.tipo_operacion,
                                    c.nombre AS comuna
                               FROM destino d JOIN comuna c ON c.id = d.comuna_id
                              WHERE d.activo = 1 ORDER BY d.nombre"""),
            'vehiculos': q("""SELECT id, patente, tipo FROM vehiculo
                               WHERE activo = 1 ORDER BY patente"""),
            'conductores': q("""SELECT id, nombre FROM conductor
                                 WHERE activo = 1 ORDER BY nombre"""),
        }


def _entero(valor):
    valor = (valor or '').strip()
    return int(valor) if valor.isdigit() else None


def _form_a_datos(form):
    """Traduce el formulario a los datos que espera emitir(). Valida el borde."""
    cantidad = a_centesimas(form.get('cantidad_m3'))

    peso = form.get('peso_kg', '').strip()
    peso_cent = a_centesimas(peso) if peso else None

    receptor_rut = (form.get('receptor_rut') or '').strip()
    if receptor_rut:
        receptor_rut = normalizar_rut(receptor_rut)

    datos = {
        'cliente_id': _entero(form.get('cliente_id')),
        'direccion_retiro_id': _entero(form.get('direccion_retiro_id')),
        'fecha_retiro_inicio': (form.get('fecha_retiro_inicio') or '').strip(),
        'fecha_retiro_termino': (form.get('fecha_retiro_termino') or '').strip(),
        'tipo_material_id': _entero(form.get('tipo_material_id')),
        'cantidad_m3_cent': cantidad,
        'metodo_medicion': (form.get('metodo_medicion') or '').strip(),
        'peso_kg_cent': peso_cent,
        'n_contenedores': _entero(form.get('n_contenedores')),
        'tipo_contenedor_id': _entero(form.get('tipo_contenedor_id')),
        'n_viajes': _entero(form.get('n_viajes')) or 1,
        'vehiculo_id': _entero(form.get('vehiculo_id')),
        'conductor_id': _entero(form.get('conductor_id')),
        'destino_id': _entero(form.get('destino_id')),
        'comprobante_destino': (form.get('comprobante_destino') or '').strip() or None,
        'receptor_nombre': (form.get('receptor_nombre') or '').strip() or None,
        'receptor_rut': receptor_rut or None,
        'receptor_cargo': (form.get('receptor_cargo') or '').strip() or None,
        'observaciones': (form.get('observaciones') or '').strip() or None,
        'emitida_por_usuario_id': session.get('usuario_id', 1),
        'reemplaza_a': None,
        'firma_path': None,
        'lat': None,
        'lng': None,
    }

    faltantes = [k for k in ('cliente_id', 'direccion_retiro_id', 'tipo_material_id',
                             'destino_id') if not datos[k]]
    if faltantes:
        raise DatosInvalidos('Faltan campos obligatorios: ' + ', '.join(faltantes))
    if not datos['fecha_retiro_inicio']:
        raise DatosInvalidos('La fecha de retiro es obligatoria.')
    if not datos['fecha_retiro_termino']:
        datos['fecha_retiro_termino'] = datos['fecha_retiro_inicio']
    if datos['metodo_medicion'] not in ETIQUETA_METODO:
        raise DatosInvalidos('Debe elegir un método de medición.')

    return datos


@constancias_bp.route('/')
@login_required
def listado():
    filas = listar()
    return render_template('admin/constancias/listado.html',
                           constancias=filas, fmt_m3_es=fmt_m3_es)


@constancias_bp.route('/emitir', methods=['GET', 'POST'])
@login_required
def emitir_constancia():
    if request.method == 'POST':
        try:
            datos = _form_a_datos(request.form)
            c = emitir(datos)
        except (DatosInvalidos, CantidadInvalida, RutInvalido) as e:
            flash(str(e), 'error')
            return render_template('admin/constancias/emitir.html',
                                   **_catalogos(), previo=request.form)
        except sqlite3.IntegrityError as e:
            # Los CHECK y triggers de la base traen el código de la regla en el
            # mensaje; se muestra tal cual porque es información útil, no un
            # detalle de implementación.
            current_app.logger.warning('Constancia rechazada por la BD: %s', e)
            flash(f'La base de datos rechazó la constancia: {e}', 'error')
            return render_template('admin/constancias/emitir.html',
                                   **_catalogos(), previo=request.form)
        except sqlite3.Error as e:
            current_app.logger.error('Error de BD al emitir constancia: %s', e)
            flash('No se pudo emitir la constancia por un error de base de datos.',
                  'error')
            return render_template('admin/constancias/emitir.html',
                                   **_catalogos(), previo=request.form)

        flash(f'Constancia {c["folio"]} emitida.', 'success')
        return redirect(url_for('constancias.ver', constancia_id=c['id']))

    return render_template('admin/constancias/emitir.html',
                           **_catalogos(), previo=None)


@constancias_bp.route('/<int:constancia_id>')
@login_required
def ver(constancia_id):
    c = obtener(constancia_id)
    if not c:
        abort(404)
    return render_template(
        'admin/constancias/detalle.html',
        c=c,
        empresa=_empresa(),
        cantidad=fmt_m3_es(c['cantidad_m3_cent']),
        peso=fmt_m3_es(c['peso_kg_cent']) if c['peso_kg_cent'] else None,
        metodo=ETIQUETA_METODO.get(c['metodo_medicion'], c['metodo_medicion']),
        operacion=ETIQUETA_OPERACION.get(c['snap_destino_operacion'],
                                         c['snap_destino_operacion']),
        codigo_legible=formatear_codigo(c['codigo_verificacion']),
        url_verificar=url_verificacion(c['codigo_verificacion']),
    )


@constancias_bp.route('/<int:constancia_id>/pdf')
@login_required
def pdf(constancia_id):
    c = obtener(constancia_id)
    if not c:
        abort(404)
    try:
        return respuesta_pdf(c, _empresa())
    except Exception as e:
        current_app.logger.error('Error al generar el PDF de %s: %s', constancia_id, e)
        flash('No se pudo generar el PDF. Intente nuevamente.', 'error')
        return redirect(url_for('constancias.ver', constancia_id=constancia_id))


@constancias_bp.route('/<int:constancia_id>/anular', methods=['GET', 'POST'])
@rol_requerido('admin')
def anular_constancia(constancia_id):
    """Anular es la única forma de 'corregir' una constancia emitida."""
    c = obtener(constancia_id)
    if not c:
        abort(404)

    if request.method == 'POST':
        try:
            anular(constancia_id, request.form.get('motivo'),
                   usuario_id=session.get('usuario_id'))
        except AnulacionInvalida as e:
            flash(str(e), 'error')
            return render_template('admin/constancias/anular.html', c=c,
                                   previo=request.form)
        flash(f'Constancia {c["folio"]} anulada.', 'success')
        return redirect(url_for('constancias.ver', constancia_id=constancia_id))

    return render_template('admin/constancias/anular.html', c=c, previo=None)


@constancias_bp.route('/<int:constancia_id>/reemplazar', methods=['GET', 'POST'])
@rol_requerido('admin')
def reemplazar_constancia(constancia_id):
    """Emite una constancia nueva y deja la anterior como 'reemplazada'."""
    anterior = obtener(constancia_id)
    if not anterior:
        abort(404)

    if request.method == 'POST':
        try:
            datos = _form_a_datos(request.form)
            nueva = reemplazar(constancia_id, datos, request.form.get('motivo'),
                               usuario_id=session.get('usuario_id'))
        except (DatosInvalidos, CantidadInvalida, RutInvalido,
                AnulacionInvalida) as e:
            flash(str(e), 'error')
            return render_template('admin/constancias/emitir.html',
                                   **_catalogos(), previo=request.form,
                                   reemplaza=anterior)
        except sqlite3.IntegrityError as e:
            current_app.logger.warning('Reemplazo rechazado por la BD: %s', e)
            flash(f'La base de datos rechazó el reemplazo: {e}', 'error')
            return render_template('admin/constancias/emitir.html',
                                   **_catalogos(), previo=request.form,
                                   reemplaza=anterior)

        flash(f'Constancia {nueva["folio"]} emitida en reemplazo de '
              f'{anterior["folio"]}.', 'success')
        return redirect(url_for('constancias.ver', constancia_id=nueva['id']))

    return render_template('admin/constancias/emitir.html', **_catalogos(),
                           previo=datos_para_reemplazo(constancia_id),
                           reemplaza=anterior)


@constancias_bp.route('/integridad')
@login_required
def integridad():
    """Estado de la cadena y de los anclajes publicados."""
    from .cadena import verificar_anclajes, verificar_cadena

    with get_db() as conn:
        ok_cadena, n, problemas = verificar_cadena(conn)
        ok_anclajes, n_anclajes, problemas_anclaje = verificar_anclajes(conn)
        anclajes = [dict(f) for f in conn.execute("""
            SELECT * FROM anclaje_diario ORDER BY fecha DESC LIMIT 30
        """)]

    return render_template('admin/constancias/integridad.html',
                           ok=(ok_cadena and ok_anclajes), n=n,
                           n_anclajes=n_anclajes,
                           problemas=problemas + problemas_anclaje,
                           anclajes=anclajes)
