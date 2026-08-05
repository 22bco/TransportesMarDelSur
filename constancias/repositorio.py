"""Acceso a datos de constancias. Única puerta de escritura.

Regla del módulo: ninguna otra parte del código hace INSERT o UPDATE sobre
`constancia`. Si algo necesita cambiar una constancia emitida, no existe la
función: hay que anular y emitir de nuevo.
"""
import os
import sqlite3

from db import get_db
from .cadena import GENESIS, hash_constancia, ultimo_hash
from .dominio import ahora_utc, fecha_local, generar_codigo, iso_utc


PREFIJO_FOLIO = os.environ.get('PREFIJO_FOLIO', 'RT')

# Columnas que se copian tal cual desde el diccionario de entrada.
_CAMPOS = (
    'cliente_id', 'direccion_retiro_id',
    'fecha_retiro_inicio', 'fecha_retiro_termino',
    'tipo_material_id', 'cantidad_m3_cent', 'metodo_medicion',
    'peso_kg_cent', 'n_contenedores', 'tipo_contenedor_id', 'n_viajes',
    'vehiculo_id', 'conductor_id', 'destino_id', 'comprobante_destino',
    'receptor_nombre', 'receptor_rut', 'receptor_cargo',
    'firma_path', 'lat', 'lng', 'observaciones',
    'emitida_por_usuario_id', 'reemplaza_a',
)

_SNAPS = (
    'snap_cliente_razon_social', 'snap_cliente_rut', 'snap_comuna_retiro',
    'snap_tipo_material', 'snap_destino_nombre', 'snap_destino_comuna',
    'snap_destino_operacion',
)


class DatosInvalidos(ValueError):
    """Los datos no cumplen alguna regla de negocio."""


def _siguiente_folio(conn, anio):
    fila = conn.execute(
        'SELECT COALESCE(MAX(seq), 0) AS s FROM constancia WHERE anio = ?',
        (anio,)
    ).fetchone()
    seq = fila['s'] + 1
    return seq, f'{PREFIJO_FOLIO}-{anio}-{seq:05d}'


def _snapshots(conn, datos):
    """Congela los textos que verá el público y que entran al hash."""
    cliente = conn.execute(
        'SELECT razon_social, rut FROM cliente WHERE id = ?',
        (datos['cliente_id'],)
    ).fetchone()
    if not cliente:
        raise DatosInvalidos('El cliente no existe.')

    comuna_retiro = conn.execute("""
        SELECT c.nombre FROM direccion_retiro d
          JOIN comuna c ON c.id = d.comuna_id
         WHERE d.id = ?
    """, (datos['direccion_retiro_id'],)).fetchone()
    if not comuna_retiro:
        raise DatosInvalidos('La dirección de retiro no existe.')

    material = conn.execute(
        'SELECT nombre FROM tipo_material WHERE id = ?',
        (datos['tipo_material_id'],)
    ).fetchone()
    if not material:
        raise DatosInvalidos('El tipo de material no existe.')

    destino = conn.execute("""
        SELECT d.nombre, d.tipo_operacion, c.nombre AS comuna
          FROM destino d JOIN comuna c ON c.id = d.comuna_id
         WHERE d.id = ?
    """, (datos['destino_id'],)).fetchone()
    if not destino:
        raise DatosInvalidos('El destino no existe.')

    return {
        'snap_cliente_razon_social': cliente['razon_social'],
        'snap_cliente_rut': cliente['rut'],
        'snap_comuna_retiro': comuna_retiro['nombre'],
        'snap_tipo_material': material['nombre'],
        'snap_destino_nombre': destino['nombre'],
        'snap_destino_comuna': destino['comuna'],
        'snap_destino_operacion': destino['tipo_operacion'],
    }


def emitir(datos, intentos=3):
    """Emite una constancia. Devuelve la fila recién creada.

    Todo ocurre dentro de `BEGIN IMMEDIATE`: el folio correlativo, la lectura
    del último eslabón de la cadena y el INSERT quedan serializados frente a
    los demás workers de gunicorn. Sin ese lock, dos emisiones simultáneas
    leerían el mismo último hash y BIFURCARÍAN la cadena — que es un daño
    silencioso y no una excepción visible.

    Los triggers de la base son la red de seguridad si el lock fallara.
    """
    ultimo_error = None

    for _ in range(intentos):
        try:
            with get_db() as conn:
                conn.execute('BEGIN IMMEDIATE')

                momento = ahora_utc()
                anio = int(fecha_local(momento)[:4])
                seq, folio = _siguiente_folio(conn, anio)

                fila = dict(datos)
                fila.update(_snapshots(conn, datos))
                fila.update({
                    'folio': folio,
                    'anio': anio,
                    'seq': seq,
                    'codigo_verificacion': generar_codigo(),
                    'estado': 'vigente',
                    'emitida_at': iso_utc(momento),
                    'emitida_at_epoch': int(momento.timestamp()),
                    'emitida_fecha_local': fecha_local(momento),
                    'hash_version': 1,
                    'hash_anterior': ultimo_hash(conn),
                })
                fila['hash_actual'] = hash_constancia(fila)

                columnas = ('folio', 'anio', 'seq', 'codigo_verificacion', 'estado',
                            'emitida_at', 'emitida_at_epoch', 'emitida_fecha_local',
                            *_CAMPOS, *_SNAPS,
                            'hash_version', 'hash_anterior', 'hash_actual')
                marcadores = ','.join('?' * len(columnas))
                cur = conn.execute(
                    f"INSERT INTO constancia ({','.join(columnas)}) VALUES ({marcadores})",
                    tuple(fila.get(c) for c in columnas),
                )

                # Reemplazo: la anterior pasa a 'reemplazada' en la MISMA
                # transacción, para que no exista un instante con dos vigentes.
                if fila.get('reemplaza_a'):
                    conn.execute(
                        "UPDATE constancia SET estado = 'reemplazada' WHERE id = ?",
                        (fila['reemplaza_a'],)
                    )

                return obtener(cur.lastrowid, conn=conn)

        except sqlite3.IntegrityError as e:
            # Colisión de folio o de código de verificación: reintentar genera
            # un correlativo y un código nuevos.
            ultimo_error = e
            continue

    raise ultimo_error


def obtener(constancia_id, conn=None):
    def _leer(c):
        fila = c.execute('SELECT * FROM constancia WHERE id = ?',
                         (constancia_id,)).fetchone()
        return dict(fila) if fila else None

    if conn is not None:
        return _leer(conn)
    with get_db() as c:
        return _leer(c)


def por_codigo(codigo, conn=None):
    """Búsqueda de la verificación pública. Usa el índice UNIQUE."""
    def _leer(c):
        fila = c.execute('SELECT * FROM constancia WHERE codigo_verificacion = ?',
                         (codigo,)).fetchone()
        return dict(fila) if fila else None

    if conn is not None:
        return _leer(conn)
    with get_db() as c:
        return _leer(c)


def listar(limite=200):
    with get_db() as conn:
        return [dict(f) for f in conn.execute("""
            SELECT * FROM constancia
             ORDER BY emitida_at_epoch DESC, id DESC
             LIMIT ?
        """, (limite,))]


def folio_de(constancia_id):
    """Folio de una constancia, sin exponer su código de verificación."""
    with get_db() as conn:
        fila = conn.execute('SELECT folio FROM constancia WHERE id = ?',
                            (constancia_id,)).fetchone()
        return fila['folio'] if fila else None


def folio_que_reemplaza_a(constancia_id):
    with get_db() as conn:
        fila = conn.execute(
            'SELECT folio FROM constancia WHERE reemplaza_a = ? LIMIT 1',
            (constancia_id,)
        ).fetchone()
        return fila['folio'] if fila else None
