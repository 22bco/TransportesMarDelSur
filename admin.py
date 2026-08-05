"""
Blueprint de administración: sistema de cotizaciones para Transportes Mar del Sur.

Rutas (todas bajo /admin):
    /admin/login                       - login con contraseña
    /admin/logout                      - cerrar sesión
    /admin/cotizar                     - formulario nueva cotización
    /admin/cotizaciones                - listado del historial
    /admin/cotizaciones/<id>           - vista web de una cotización
    /admin/cotizaciones/<id>/pdf       - descarga del PDF
    /admin/cotizaciones/<id>/eliminar  - eliminar (POST)
"""
import base64
import io
import os
import sqlite3
import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, send_file, current_app
)
from weasyprint import HTML

from auth import destino_seguro, login_required
from db import get_db, init_wal


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# --- BD ---------------------------------------------------------------
# get_db() vive en db.py: la comparten este módulo y el de constancias.


def init_db():
    init_wal()
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS quotes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            year            INTEGER NOT NULL,
            seq             INTEGER NOT NULL,
            number          TEXT NOT NULL UNIQUE,
            created_at      TEXT NOT NULL,
            valid_until     TEXT NOT NULL,
            validity_days   INTEGER NOT NULL,

            client_name     TEXT NOT NULL,
            client_rut      TEXT,
            client_address  TEXT,
            client_phone    TEXT,
            client_email    TEXT,

            origin          TEXT,
            destination     TEXT,

            items_json      TEXT NOT NULL,

            iva_applies     INTEGER NOT NULL DEFAULT 1,
            iva_rate        REAL    NOT NULL DEFAULT 0.19,
            subtotal        INTEGER NOT NULL,
            iva_amount      INTEGER NOT NULL,
            total           INTEGER NOT NULL,

            payment_terms   TEXT,
            notes           TEXT,

            UNIQUE(year, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_quotes_year_seq ON quotes(year, seq);
        CREATE INDEX IF NOT EXISTS idx_quotes_client   ON quotes(client_name);
        """)


def next_number(conn, year):
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM quotes WHERE year = ?",
        (year,)
    ).fetchone()
    seq = row['s'] + 1
    return seq, f"COT-{year}-{seq:03d}"


def insert_quote(year, data):
    """Inserta una cotización calculando su número de forma atómica.

    Problema: con 4 workers, dos POST simultáneos pueden calcular el mismo
    seq con SELECT MAX y el segundo INSERT violaría UNIQUE -> error 500.

    Solución: cada intento abre una transacción BEGIN IMMEDIATE (toma el
    lock de escritura antes de leer el MAX, serializando los workers). Si aun
    así colisiona (p.ej. por timeout), se captura IntegrityError y se reintenta.
    Devuelve (quote_id, number).
    """
    attempts = 3
    last_err = None
    for _ in range(attempts):
        try:
            with get_db() as conn:
                # BEGIN IMMEDIATE adquiere el lock de escritura ya: el cálculo
                # del seq y el INSERT quedan serializados frente a otros workers.
                conn.execute("BEGIN IMMEDIATE")
                seq, number = next_number(conn, year)
                cur = conn.execute("""
                    INSERT INTO quotes (
                        year, seq, number, created_at, valid_until, validity_days,
                        client_name, client_rut, client_address, client_phone, client_email,
                        origin, destination,
                        items_json,
                        iva_applies, iva_rate, subtotal, iva_amount, total,
                        payment_terms, notes
                    ) VALUES (?,?,?,?,?,?, ?,?,?,?,?, ?,?, ?, ?,?,?,?,?, ?,?)
                """, (
                    year, seq, number, data['created_at'], data['valid_until'], data['validity_days'],
                    data['client_name'], data['client_rut'], data['client_address'], data['client_phone'], data['client_email'],
                    data['origin'], data['destination'],
                    data['items_json'],
                    data['iva_applies'], data['iva_rate'], data['subtotal'], data['iva_amount'], data['total'],
                    data['payment_terms'], data['notes'],
                ))
                return cur.lastrowid, number
        except sqlite3.IntegrityError as e:
            # Colisión de UNIQUE(year, seq) / number: reintentar con un seq nuevo.
            last_err = e
            continue
    # Agotados los reintentos: propaga para que el caller lo maneje (no 500 opaco).
    raise last_err


# --- Auth -------------------------------------------------------------
# login_required y destino_seguro viven en auth.py.


@admin_bp.route('/', methods=['GET'])
def index():
    if session.get('admin'):
        return redirect(url_for('admin.cotizaciones'))
    return redirect(url_for('admin.login'))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    expected = os.environ.get('ADMIN_PASSWORD', '')
    error = None
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if expected and pw == expected:
            session.clear()
            session['admin'] = True
            session.permanent = True
            return redirect(destino_seguro(request.args.get('next'),
                                           url_for('admin.cotizaciones')))
        error = 'Contraseña incorrecta.'
    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin.login'))


# --- Cotizaciones -----------------------------------------------------

def _parse_price(raw):
    """Precio en pesos chilenos: enteros, sin decimales.

    Quita separadores de miles y cualquier caracter no numérico (símbolos,
    espacios, '$', etc.) y parsea como entero. Nunca devuelve negativo.
    """
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if not digits:
        return 0
    try:
        return abs(int(digits))
    except ValueError:
        return 0


def _parse_qty(raw):
    """Cantidad: puede ser decimal. Acepta '2,5' y '2.5' por igual.

    Nunca devuelve negativo. Si el texto es inválido, usa 1.0 por defecto.
    """
    s = (raw or '').strip().replace(',', '.')
    if not s:
        return 1.0
    try:
        return abs(float(s))
    except ValueError:
        return 1.0


def _parse_items_from_form(keep_empty=False):
    """Lee items[*][descripcion|cantidad|unidad|precio] del form y devuelve lista normalizada.

    Si keep_empty=True conserva también las filas con descripción vacía; se usa
    para re-mostrar el formulario tras un error sin que el usuario pierda una
    fila a medio llenar.
    """
    items = []
    descs = request.form.getlist('item_desc')
    qtys = request.form.getlist('item_qty')
    units = request.form.getlist('item_unit')
    prices = request.form.getlist('item_price')

    for desc, qty, unit, price in zip(descs, qtys, units, prices):
        desc = (desc or '').strip()
        if not desc and not keep_empty:
            continue
        q = _parse_qty(qty)
        p = _parse_price(price)
        items.append({
            'descripcion': desc,
            'cantidad': q,
            'unidad': (unit or '').strip(),
            'precio_unitario': p,
            'subtotal': int(round(q * p)),
        })
    return items


def _form_to_quote(form):
    items = _parse_items_from_form()
    if not items:
        raise ValueError('Debes agregar al menos un ítem a la cotización.')

    subtotal = sum(i['subtotal'] for i in items)
    iva_applies = 1 if form.get('iva_applies') == 'on' else 0
    iva_rate = 0.19 if iva_applies else 0.0
    iva_amount = int(round(subtotal * iva_rate)) if iva_applies else 0
    total = subtotal + iva_amount

    now = datetime.now()
    default_days = int(os.environ.get('VALIDEZ_DIAS_DEFAULT', '30') or 30)

    # La fecha de vencimiento se elige en un calendario (input type=date -> YYYY-MM-DD).
    # Si no llega o es inválida, se usa hoy + días por defecto.
    valid_until_raw = (form.get('valid_until') or '').strip()
    valid_until = None
    if valid_until_raw:
        try:
            valid_until = datetime.strptime(valid_until_raw, '%Y-%m-%d')
        except ValueError:
            valid_until = None
    if valid_until is None:
        valid_until = now + timedelta(days=default_days)

    validity_days = max((valid_until.date() - now.date()).days, 0)

    return {
        'created_at': now.isoformat(timespec='seconds'),
        'valid_until': valid_until.date().isoformat(),
        'validity_days': validity_days,
        'client_name': (form.get('client_name') or '').strip(),
        'client_rut': (form.get('client_rut') or '').strip(),
        'client_address': (form.get('client_address') or '').strip(),
        'client_phone': (form.get('client_phone') or '').strip(),
        'client_email': (form.get('client_email') or '').strip(),
        'origin': (form.get('origin') or '').strip(),
        'destination': (form.get('destination') or '').strip(),
        'items_json': json.dumps(items, ensure_ascii=False),
        'iva_applies': iva_applies,
        'iva_rate': iva_rate,
        'subtotal': subtotal,
        'iva_amount': iva_amount,
        'total': total,
        'payment_terms': (form.get('payment_terms') or '').strip(),
        'notes': (form.get('notes') or '').strip(),
    }


def _echo_quote(form):
    """Reconstruye un objeto tipo 'quote' desde el formulario, para re-mostrarlo
    sin perder lo escrito si la validación falla."""
    return {
        'client_name': (form.get('client_name') or '').strip(),
        'client_rut': (form.get('client_rut') or '').strip(),
        'client_address': (form.get('client_address') or '').strip(),
        'client_phone': (form.get('client_phone') or '').strip(),
        'client_email': (form.get('client_email') or '').strip(),
        'origin': (form.get('origin') or '').strip(),
        'destination': (form.get('destination') or '').strip(),
        'valid_until': (form.get('valid_until') or '').strip(),
        'payment_terms': (form.get('payment_terms') or '').strip(),
        'notes': (form.get('notes') or '').strip(),
        'iva_applies': 1 if form.get('iva_applies') == 'on' else 0,
        # keep_empty=True: conserva TODAS las filas enviadas (incluso con
        # descripción vacía) para no perder una fila a medio llenar.
        'items': _parse_items_from_form(keep_empty=True),
    }


@admin_bp.route('/cotizar', methods=['GET', 'POST'])
@login_required
def cotizar():
    if request.method == 'POST':
        error = None
        try:
            data = _form_to_quote(request.form)
        except ValueError as e:
            error = str(e)
        if not error and not data['client_name']:
            error = 'El nombre del cliente es obligatorio.'
        if error:
            flash(error, 'error')
            return render_template('admin/cotizar.html', quote=_echo_quote(request.form),
                                   edit_id=None, edit_number=None)

        year = datetime.now().year
        try:
            quote_id, number = insert_quote(year, data)
        except sqlite3.Error as e:
            current_app.logger.error('Error de BD al crear cotización: %s', e)
            flash('No se pudo guardar la cotización por un error de base de datos. '
                  'Revisa los datos y vuelve a intentarlo.', 'error')
            return render_template('admin/cotizar.html', quote=_echo_quote(request.form),
                                   edit_id=None, edit_number=None)

        flash(f'Cotización {number} creada.', 'success')
        return redirect(url_for('admin.ver_cotizacion', quote_id=quote_id))

    return render_template('admin/cotizar.html', quote=None, edit_id=None, edit_number=None)


@admin_bp.route('/cotizaciones/<int:quote_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_cotizacion(quote_id):
    quote = _load_quote(quote_id)  # 404 si no existe

    if request.method == 'POST':
        error = None
        try:
            data = _form_to_quote(request.form)
        except ValueError as e:
            error = str(e)
        if not error and not data['client_name']:
            error = 'El nombre del cliente es obligatorio.'
        if error:
            flash(error, 'error')
            return render_template('admin/cotizar.html', quote=_echo_quote(request.form),
                                   edit_id=quote_id, edit_number=quote['number'])

        # Se conservan número, año y fecha de emisión originales.
        try:
            with get_db() as conn:
                conn.execute("""
                    UPDATE quotes SET
                        valid_until=?, validity_days=?,
                        client_name=?, client_rut=?, client_address=?, client_phone=?, client_email=?,
                        origin=?, destination=?,
                        items_json=?,
                        iva_applies=?, iva_rate=?, subtotal=?, iva_amount=?, total=?,
                        payment_terms=?, notes=?
                    WHERE id=?
                """, (
                    data['valid_until'], data['validity_days'],
                    data['client_name'], data['client_rut'], data['client_address'], data['client_phone'], data['client_email'],
                    data['origin'], data['destination'],
                    data['items_json'],
                    data['iva_applies'], data['iva_rate'], data['subtotal'], data['iva_amount'], data['total'],
                    data['payment_terms'], data['notes'],
                    quote_id,
                ))
        except sqlite3.Error as e:
            current_app.logger.error('Error de BD al actualizar cotización %s: %s', quote_id, e)
            flash('No se pudo actualizar la cotización por un error de base de datos. '
                  'Revisa los datos y vuelve a intentarlo.', 'error')
            return render_template('admin/cotizar.html', quote=_echo_quote(request.form),
                                   edit_id=quote_id, edit_number=quote['number'])

        flash(f'Cotización {quote["number"]} actualizada.', 'success')
        return redirect(url_for('admin.ver_cotizacion', quote_id=quote_id))

    return render_template('admin/cotizar.html', quote=quote,
                           edit_id=quote_id, edit_number=quote['number'])


@admin_bp.route('/cotizaciones')
@login_required
def cotizaciones():
    q = (request.args.get('q') or '').strip()
    with get_db() as conn:
        if q:
            rows = conn.execute("""
                SELECT * FROM quotes
                WHERE client_name LIKE ? OR number LIKE ? OR client_rut LIKE ?
                ORDER BY id DESC
            """, (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
        else:
            rows = conn.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    return render_template('admin/cotizaciones.html', quotes=rows, q=q)


_MESES_ES = [
    '', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]


def _fecha_larga(iso_date):
    """'2026-06-19' -> '19 de junio de 2026'."""
    try:
        d = datetime.strptime(iso_date, '%Y-%m-%d').date()
        return f'{d.day} de {_MESES_ES[d.month]} de {d.year}'
    except (ValueError, TypeError):
        return iso_date


def _load_quote(quote_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if not row:
        abort(404)
    quote = dict(row)
    quote['items'] = json.loads(quote['items_json'])
    quote['created_at_dt'] = datetime.fromisoformat(quote['created_at'])
    quote['valid_until_fmt'] = _fecha_larga(quote['valid_until'])
    quote['created_at_fmt'] = _fecha_larga(quote['created_at'][:10])
    return quote


def _empresa_ctx():
    return {
        'nombre':     os.environ.get('EMPRESA_NOMBRE', 'Transportes Mar del Sur SPA'),
        'rut':        os.environ.get('EMPRESA_RUT', '77.779.818-9'),
        'direccion':  os.environ.get('EMPRESA_DIRECCION', 'Puerto Montt, Los Lagos'),
        'telefono':   os.environ.get('EMPRESA_TELEFONO', '+56 9 4285 7502'),
        'email':      os.environ.get('EMPRESA_EMAIL', 'transportesmardelsur@gmail.com'),
        'web':        os.environ.get('EMPRESA_WEB', 'www.transportesmardelsur.cl'),
        'resolucion': os.environ.get('EMPRESA_RESOLUCION', 'Resolución Sanitaria N° 2510389969'),
    }


def _banco_ctx():
    return {
        'nombre':       os.environ.get('BANCO_NOMBRE', ''),
        'tipo_cuenta':  os.environ.get('BANCO_TIPO_CUENTA', ''),
        'numero':       os.environ.get('BANCO_NUMERO', ''),
        'rut':          os.environ.get('BANCO_RUT', ''),
        'titular':      os.environ.get('BANCO_TITULAR', ''),
        'email':        os.environ.get('BANCO_EMAIL', ''),
    }


@admin_bp.route('/cotizaciones/<int:quote_id>')
@login_required
def ver_cotizacion(quote_id):
    quote = _load_quote(quote_id)
    return render_template('admin/cotizacion_detalle.html',
                           quote=quote, empresa=_empresa_ctx(), banco=_banco_ctx())


@lru_cache(maxsize=1)
def _logo_data_uri():
    path = Path(current_app.root_path) / 'static' / 'img' / 'logo.png'
    if not path.exists():
        return ''
    b64 = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'data:image/png;base64,{b64}'


@admin_bp.route('/cotizaciones/<int:quote_id>/pdf')
@login_required
def pdf_cotizacion(quote_id):
    quote = _load_quote(quote_id)
    html_str = render_template(
        'admin/cotizacion_pdf.html',
        quote=quote, empresa=_empresa_ctx(), banco=_banco_ctx(),
        logo_uri=_logo_data_uri(),
    )
    try:
        pdf_bytes = HTML(string=html_str).write_pdf()
    except Exception as e:
        current_app.logger.error('Error al generar el PDF de la cotización %s: %s', quote_id, e)
        flash('No se pudo generar el PDF de la cotización. Intenta nuevamente más tarde.', 'error')
        return redirect(url_for('admin.ver_cotizacion', quote_id=quote_id))

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{quote['number']}_{quote['client_name'].replace(' ', '_')}.pdf",
    )


@admin_bp.route('/cotizaciones/<int:quote_id>/eliminar', methods=['POST'])
@login_required
def eliminar_cotizacion(quote_id):
    with get_db() as conn:
        conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        conn.commit()
    flash('Cotización eliminada.', 'success')
    return redirect(url_for('admin.cotizaciones'))
