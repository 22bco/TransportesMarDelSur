"""Verificación pública de constancias. Sin autenticación.

Esta es la superficie expuesta del sistema y la que hay que tratar con más
cuidado. Tres amenazas concretas:

  1. Enumeración: iterar códigos para listar la cartera de clientes.
     Defensa: código de 12 caracteres sobre 31 símbolos (~7,9e17), doble factor
     con los 4 últimos del RUT, y rate limit por IP.
  2. Oráculo por diferencia: distinguir "el código no existe" de "el RUT no
     coincide" convertiría el segundo factor en un filtro. Defensa: respuesta
     idéntica byte a byte y tiempo de respuesta uniforme.
  3. Filtración de datos: la página muestra una lista blanca de campos, jamás
     la fila completa.
"""
import hmac
import os
import time

from flask import Blueprint, current_app, render_template, request

from db import get_db
from .dominio import (
    ahora_utc, fmt_m3_es, iso_utc, normalizar_codigo, ultimos_4_rut,
)
from .pdf import ETIQUETA_METODO, ETIQUETA_OPERACION


verificar_bp = Blueprint('verificar', __name__)


MAX_INTENTOS = int(os.environ.get('VERIFICAR_MAX_INTENTOS', '10'))
VENTANA_MIN = int(os.environ.get('VERIFICAR_VENTANA_MIN', '15'))
PISO_MS = int(os.environ.get('VERIFICAR_PISO_MS', '120'))

# Fila señuelo: cuando el código no existe se compara igual contra este RUT
# falso, para que el trabajo realizado sea el mismo en todos los caminos.
SENUELO = {'snap_cliente_rut': '00000000-0'}


def ip_cliente() -> str:
    """IP real del visitante.

    nginx ya resolvió CF-Connecting-IP (ver conf.d/00-cloudflare-realip.conf) y
    Flask solo escucha en 127.0.0.1, así que el único origen posible es el
    propio nginx. NO se usa X-Forwarded-For, que es manipulable por el cliente.
    """
    return (request.headers.get('CF-Connecting-IP')
            or request.headers.get('X-Real-IP')
            or request.remote_addr
            or '0.0.0.0')


def _registrar(conn, codigo, exito, motivo, ip):
    momento = ahora_utc()
    conn.execute("""
        INSERT INTO consulta_verificacion
               (codigo_consultado, exito, motivo, ip, user_agent,
                consultado_at, consultado_at_epoch)
        VALUES (?,?,?,?,?,?,?)
    """, (codigo[:32], 1 if exito else 0, motivo, ip,
          (request.headers.get('User-Agent') or '')[:300],
          iso_utc(momento), int(momento.timestamp())))


def _excede_limite(conn, ip) -> bool:
    """Ventana deslizante real de MAX_INTENTOS en VENTANA_MIN minutos.

    nginx aplica además un limit_req más grueso, que sirve para que un flood
    muera antes de despertar a un worker. Este de aquí es el autoritativo y el
    que queda auditado en consulta_verificacion.
    """
    desde = int(ahora_utc().timestamp()) - VENTANA_MIN * 60
    n = conn.execute("""
        SELECT COUNT(*) AS n FROM consulta_verificacion
         WHERE ip = ? AND consultado_at_epoch > ?
    """, (ip, desde)).fetchone()['n']
    return n >= MAX_INTENTOS


def payload_publico(c: dict) -> dict:
    """ÚNICA fuente de datos de la página pública.

    Construye un diccionario NUEVO, campo por campo. Es lista blanca a
    propósito: si mañana se agrega una columna a `constancia`, no queda
    expuesta por accidente. La plantilla recibe solo esto, nunca la fila.

    Reservado y nunca incluido: dirección exacta del retiro, receptor_*,
    datos de contacto, RUT del conductor, lat/lng, adjuntos, observaciones,
    motivo_anulacion, hashes e ids internos.
    """
    from .repositorio import folio_que_reemplaza_a

    datos = {
        'emisor_razon_social': _empresa()['nombre'],
        'emisor_rut': _empresa()['rut'],
        'folio': c['folio'],
        'estado': c['estado'],
        'fecha_retiro_inicio': c['fecha_retiro_inicio'],
        'fecha_retiro_termino': c['fecha_retiro_termino'],
        'cliente_razon_social': c['snap_cliente_razon_social'],
        'comuna_retiro': c['snap_comuna_retiro'],
        'tipo_material': c['snap_tipo_material'],
        'cantidad_m3': fmt_m3_es(c['cantidad_m3_cent']),
        'metodo_medicion': ETIQUETA_METODO.get(c['metodo_medicion'],
                                               c['metodo_medicion']),
        'destino_nombre': c['snap_destino_nombre'],
        'destino_comuna': c['snap_destino_comuna'],
        'destino_operacion': ETIQUETA_OPERACION.get(c['snap_destino_operacion'],
                                                    c['snap_destino_operacion']),
    }

    if c['estado'] == 'anulada':
        # La fecha sí; el motivo no: puede contener información del cliente.
        datos['anulada_fecha'] = (c['anulada_at'] or '')[:10]

    if c['estado'] == 'reemplazada':
        # Solo el FOLIO de la que la reemplaza. Enlazar con su código de
        # verificación la expondría sin su segundo factor.
        datos['reemplazada_por_folio'] = folio_que_reemplaza_a(c['id'])

    return datos


# Congelado: hay un test que compara este conjunto contra lo que devuelve
# payload_publico(), para que nadie agregue un campo sin darse cuenta.
CAMPOS_PUBLICOS = frozenset({
    'emisor_razon_social', 'emisor_rut', 'folio', 'estado',
    'fecha_retiro_inicio', 'fecha_retiro_termino', 'cliente_razon_social',
    'comuna_retiro', 'tipo_material', 'cantidad_m3', 'metodo_medicion',
    'destino_nombre', 'destino_comuna', 'destino_operacion',
})

CAMPOS_PUBLICOS_CONDICIONALES = frozenset({
    'anulada_fecha', 'reemplazada_por_folio',
})


def _empresa():
    return {
        'nombre': os.environ.get('EMPRESA_NOMBRE', 'Transportes Mar del Sur SPA'),
        'rut': os.environ.get('EMPRESA_RUT', '77.779.818-9'),
        'direccion': os.environ.get('EMPRESA_DIRECCION', 'Puerto Montt, Los Lagos'),
        'resolucion': os.environ.get('EMPRESA_RESOLUCION',
                                     'Resolución Sanitaria N° 2510389969'),
    }


@verificar_bp.route('/verificar', methods=['GET'])
def formulario():
    return render_template('publico/verificar.html', codigo='', error=None)


@verificar_bp.route('/verificar/<codigo>', methods=['GET'])
def formulario_con_codigo(codigo):
    """Destino del QR. Prellena el código pero SIEMPRE pide el RUT.

    No revela si el código existe: la página es la misma en ambos casos.
    """
    return render_template('publico/verificar.html',
                           codigo=normalizar_codigo(codigo), error=None)


@verificar_bp.route('/verificar', methods=['POST'])
def verificar():
    inicio = time.perf_counter()
    ip = ip_cliente()
    codigo_crudo = request.form.get('codigo', '')
    codigo = normalizar_codigo(codigo_crudo)
    rut4 = (request.form.get('rut4') or '').strip().upper()

    with get_db() as conn:
        # El gate de rate limit va ANTES del piso de tiempo: si no, el piso se
        # convertiría en un amplificador de denegación de servicio.
        if _excede_limite(conn, ip):
            _registrar(conn, codigo or codigo_crudo, False, 'rate_limit', ip)
            return render_template('publico/verificar.html',
                                   codigo=codigo_crudo,
                                   error='Demasiados intentos. Espere unos minutos '
                                         'e inténtelo nuevamente.'), 429

        fila = None
        if codigo:
            f = conn.execute('SELECT * FROM constancia WHERE codigo_verificacion = ?',
                             (codigo,)).fetchone()
            fila = dict(f) if f else None

        # Camino único: si no hay fila se compara igual, contra el señuelo.
        referencia = fila or SENUELO
        esperado = ultimos_4_rut(referencia['snap_cliente_rut'])
        coincide = hmac.compare_digest(esperado, rut4)

        if fila and coincide:
            motivo, exito = 'ok', True
        elif not codigo:
            motivo, exito = 'formato', False
        elif not fila:
            motivo, exito = 'no_existe', False
        else:
            motivo, exito = 'rut_no_coincide', False

        _registrar(conn, codigo or codigo_crudo, exito, motivo, ip)
        datos = payload_publico(fila) if exito else None

    _igualar_tiempo(inicio)

    if datos:
        return render_template('publico/verificar_resultado.html', d=datos)

    # Respuesta IDÉNTICA para "no existe", "RUT no coincide" y "formato malo".
    # El motivo real quedó en la base para forense, pero no viaja al cliente.
    return render_template('publico/verificar.html',
                           codigo=codigo_crudo,
                           error='No encontramos un documento con esos datos. '
                                 'Revise el código y los 4 últimos caracteres del RUT.')


def _igualar_tiempo(inicio):
    """Lleva toda respuesta a un piso común, para no filtrar por cronómetro."""
    if PISO_MS <= 0:
        return
    restante = (PISO_MS / 1000.0) - (time.perf_counter() - inicio)
    if restante > 0:
        time.sleep(restante)


def purgar_consultas(conn, dias=180):
    """Evita que consulta_verificacion crezca sin techo. La llama el anclaje."""
    corte = int(ahora_utc().timestamp()) - dias * 86400
    cur = conn.execute('DELETE FROM consulta_verificacion WHERE consultado_at_epoch < ?',
                       (corte,))
    return cur.rowcount
