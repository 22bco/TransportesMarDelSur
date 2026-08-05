"""CRUD de clientes, direcciones, destinos, vehículos y conductores.

A diferencia de `constancia`, estas tablas SÍ se editan: son datos vivos. La
constancia no depende de ellos una vez emitida porque guarda snapshots — por
eso corregir la razón social de un cliente no altera ni rompe ningún documento
ya emitido.

Se desactivan en vez de borrarse (`activo = 0`): un cliente puede tener
constancias que lo referencian, y esas referencias no pueden quedar colgando.
"""
import sqlite3

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for,
)

from auth import login_required
from db import get_db
from .dominio import RutInvalido, a_centesimas, normalizar_rut


maestros_bp = Blueprint('maestros', __name__, url_prefix='/admin/maestros')


def _entero(v):
    v = (v or '').strip()
    return int(v) if v.lstrip('-').isdigit() else None


def _txt(v):
    return (v or '').strip() or None


def _comunas():
    with get_db() as conn:
        return [dict(f) for f in conn.execute(
            'SELECT id, nombre, region FROM comuna ORDER BY nombre')]


# --- Clientes ---------------------------------------------------------

@maestros_bp.route('/clientes')
@login_required
def clientes():
    with get_db() as conn:
        filas = [dict(f) for f in conn.execute("""
            SELECT c.*, co.nombre AS comuna,
                   (SELECT COUNT(*) FROM direccion_retiro d
                     WHERE d.cliente_id = c.id AND d.activo = 1) AS n_direcciones,
                   (SELECT COUNT(*) FROM constancia k WHERE k.cliente_id = c.id) AS n_constancias
              FROM cliente c LEFT JOIN comuna co ON co.id = c.comuna_id
             ORDER BY c.activo DESC, c.razon_social
        """)]
    return render_template('admin/maestros/clientes.html', clientes=filas)


@maestros_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@maestros_bp.route('/clientes/<int:cliente_id>', methods=['GET', 'POST'])
@login_required
def cliente_form(cliente_id=None):
    with get_db() as conn:
        actual = conn.execute('SELECT * FROM cliente WHERE id = ?',
                              (cliente_id,)).fetchone() if cliente_id else None
    if cliente_id and not actual:
        abort(404)

    if request.method == 'POST':
        try:
            rut = normalizar_rut(request.form.get('rut', ''))
        except RutInvalido as e:
            flash(f'RUT inválido: {e}', 'error')
            return render_template('admin/maestros/cliente_form.html',
                                   c=dict(actual) if actual else None,
                                   previo=request.form, comunas=_comunas())

        campos = (
            _txt(request.form.get('razon_social')), rut,
            _txt(request.form.get('giro')), _txt(request.form.get('direccion_tributaria')),
            _entero(request.form.get('comuna_id')),
            _txt(request.form.get('contacto_nombre')),
            _txt(request.form.get('contacto_email')),
            _txt(request.form.get('contacto_telefono')),
            1 if request.form.get('activo') else 0,
        )
        if not campos[0]:
            flash('La razón social es obligatoria.', 'error')
            return render_template('admin/maestros/cliente_form.html',
                                   c=dict(actual) if actual else None,
                                   previo=request.form, comunas=_comunas())
        try:
            with get_db() as conn:
                if cliente_id:
                    conn.execute("""
                        UPDATE cliente SET razon_social=?, rut=?, giro=?,
                               direccion_tributaria=?, comuna_id=?, contacto_nombre=?,
                               contacto_email=?, contacto_telefono=?, activo=?
                         WHERE id=?
                    """, campos + (cliente_id,))
                else:
                    from .dominio import ahora_utc, iso_utc
                    conn.execute("""
                        INSERT INTO cliente (razon_social, rut, giro,
                               direccion_tributaria, comuna_id, contacto_nombre,
                               contacto_email, contacto_telefono, activo, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, campos + (iso_utc(ahora_utc()),))
        except sqlite3.IntegrityError:
            flash(f'Ya existe un cliente con el RUT {rut}.', 'error')
            return render_template('admin/maestros/cliente_form.html',
                                   c=dict(actual) if actual else None,
                                   previo=request.form, comunas=_comunas())

        flash('Cliente guardado.', 'success')
        return redirect(url_for('maestros.clientes'))

    return render_template('admin/maestros/cliente_form.html',
                           c=dict(actual) if actual else None,
                           previo=None, comunas=_comunas())


# --- Direcciones de retiro --------------------------------------------

@maestros_bp.route('/direcciones')
@login_required
def direcciones():
    with get_db() as conn:
        filas = [dict(f) for f in conn.execute("""
            SELECT d.*, c.razon_social AS cliente, co.nombre AS comuna
              FROM direccion_retiro d
              JOIN cliente c ON c.id = d.cliente_id
              JOIN comuna co ON co.id = d.comuna_id
             ORDER BY d.activo DESC, c.razon_social, d.nombre_referencia
        """)]
        clientes = [dict(f) for f in conn.execute(
            'SELECT id, razon_social FROM cliente WHERE activo = 1 ORDER BY razon_social')]
    return render_template('admin/maestros/direcciones.html',
                           direcciones=filas, clientes=clientes, comunas=_comunas())


@maestros_bp.route('/direcciones/nueva', methods=['POST'])
@login_required
def direccion_nueva():
    with get_db() as conn:
        conn.execute("""
            INSERT INTO direccion_retiro (cliente_id, nombre_referencia, calle,
                                          numero, comuna_id, referencia)
            VALUES (?,?,?,?,?,?)
        """, (_entero(request.form.get('cliente_id')),
              _txt(request.form.get('nombre_referencia')) or 'Sin nombre',
              _txt(request.form.get('calle')) or 'Sin calle',
              _txt(request.form.get('numero')),
              _entero(request.form.get('comuna_id')),
              _txt(request.form.get('referencia'))))
    flash('Dirección de retiro agregada.', 'success')
    return redirect(url_for('maestros.direcciones'))


@maestros_bp.route('/direcciones/<int:direccion_id>/activar', methods=['POST'])
@login_required
def direccion_activar(direccion_id):
    """Alterna activo. No se borra: puede estar referenciada por constancias."""
    with get_db() as conn:
        conn.execute('UPDATE direccion_retiro SET activo = 1 - activo WHERE id = ?',
                     (direccion_id,))
    return redirect(url_for('maestros.direcciones'))


# --- Destinos, vehículos, conductores ---------------------------------

@maestros_bp.route('/destinos', methods=['GET', 'POST'])
@login_required
def destinos():
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute("""
                INSERT INTO destino (nombre, direccion, comuna_id, tipo_operacion,
                                     autorizacion)
                VALUES (?,?,?,?,?)
            """, (_txt(request.form.get('nombre')) or 'Sin nombre',
                  _txt(request.form.get('direccion')),
                  _entero(request.form.get('comuna_id')),
                  request.form.get('tipo_operacion', 'disposicion_final'),
                  _txt(request.form.get('autorizacion'))))
        flash('Destino agregado.', 'success')
        return redirect(url_for('maestros.destinos'))

    with get_db() as conn:
        filas = [dict(f) for f in conn.execute("""
            SELECT d.*, c.nombre AS comuna FROM destino d
              JOIN comuna c ON c.id = d.comuna_id
             ORDER BY d.activo DESC, d.nombre
        """)]
    return render_template('admin/maestros/destinos.html',
                           destinos=filas, comunas=_comunas())


@maestros_bp.route('/flota', methods=['GET', 'POST'])
@login_required
def flota():
    """Vehículos y conductores en una sola pantalla: son listas cortas."""
    if request.method == 'POST':
        tipo = request.form.get('tipo_registro')
        try:
            with get_db() as conn:
                if tipo == 'vehiculo':
                    capacidad = request.form.get('capacidad_m3', '').strip()
                    conn.execute("""
                        INSERT INTO vehiculo (patente, tipo, capacidad_m3_cent)
                        VALUES (?,?,?)
                    """, ((_txt(request.form.get('patente')) or '').upper(),
                          _txt(request.form.get('tipo')),
                          a_centesimas(capacidad) if capacidad else None))
                    flash('Vehículo agregado.', 'success')
                else:
                    rut = request.form.get('rut', '').strip()
                    conn.execute("""
                        INSERT INTO conductor (nombre, rut, telefono) VALUES (?,?,?)
                    """, (_txt(request.form.get('nombre')) or 'Sin nombre',
                          normalizar_rut(rut) if rut else None,
                          _txt(request.form.get('telefono'))))
                    flash('Conductor agregado.', 'success')
        except RutInvalido as e:
            flash(f'RUT inválido: {e}', 'error')
        except sqlite3.IntegrityError:
            flash('Ya existe un registro con esa patente o ese RUT.', 'error')
        return redirect(url_for('maestros.flota'))

    with get_db() as conn:
        vehiculos = [dict(f) for f in conn.execute(
            'SELECT * FROM vehiculo ORDER BY activo DESC, patente')]
        conductores = [dict(f) for f in conn.execute(
            'SELECT * FROM conductor ORDER BY activo DESC, nombre')]
    return render_template('admin/maestros/flota.html',
                           vehiculos=vehiculos, conductores=conductores)


@maestros_bp.route('/catalogos', methods=['GET', 'POST'])
@login_required
def catalogos():
    """Materiales, tipos de contenedor y comunas."""
    if request.method == 'POST':
        tipo = request.form.get('tipo_registro')
        try:
            with get_db() as conn:
                if tipo == 'material':
                    conn.execute('INSERT INTO tipo_material (nombre) VALUES (?)',
                                 (_txt(request.form.get('nombre')),))
                elif tipo == 'contenedor':
                    cap = request.form.get('capacidad_m3', '').strip()
                    conn.execute("""
                        INSERT INTO tipo_contenedor (nombre, capacidad_m3_cent)
                        VALUES (?,?)
                    """, (_txt(request.form.get('nombre')),
                          a_centesimas(cap) if cap else None))
                else:
                    conn.execute('INSERT INTO comuna (nombre, region) VALUES (?,?)',
                                 (_txt(request.form.get('nombre')),
                                  _txt(request.form.get('region')) or 'Los Lagos'))
            flash('Registro agregado.', 'success')
        except sqlite3.IntegrityError:
            flash('Ese registro ya existe.', 'error')
        return redirect(url_for('maestros.catalogos'))

    with get_db() as conn:
        materiales = [dict(f) for f in conn.execute(
            'SELECT * FROM tipo_material ORDER BY activo DESC, nombre')]
        contenedores = [dict(f) for f in conn.execute(
            'SELECT * FROM tipo_contenedor ORDER BY activo DESC, nombre')]
    return render_template('admin/maestros/catalogos.html',
                           materiales=materiales, contenedores=contenedores,
                           comunas=_comunas())
