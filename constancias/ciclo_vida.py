"""Anulación y reemplazo: el único camino para corregir una constancia.

RN-01 dice que una constancia emitida no se modifica. Corregir significa
anular la existente y, si procede, emitir una nueva que la referencia.

Las dos únicas escrituras que este módulo hace sobre `constancia` son cambios
de `estado`, `anulada_at` y `motivo_anulacion` — las tres columnas que el
trigger trg_constancia_inmutable deja pasar. Cualquier otra cosa aborta.
"""
import sqlite3

from db import get_db
from .dominio import ahora_utc, iso_utc
from .repositorio import emitir, obtener


LARGO_MINIMO_MOTIVO = 10


class AnulacionInvalida(ValueError):
    """El motivo falta, es muy corto, o la constancia no se puede anular."""


def anular(constancia_id, motivo, usuario_id=None):
    """Anula una constancia vigente. RN-02: el motivo es obligatorio.

    El motivo no se publica en la página pública —puede contener información
    del cliente— pero queda para siempre en la base y no se puede reescribir.
    """
    motivo = (motivo or '').strip()
    if len(motivo) < LARGO_MINIMO_MOTIVO:
        raise AnulacionInvalida(
            f'El motivo de anulación es obligatorio y debe tener al menos '
            f'{LARGO_MINIMO_MOTIVO} caracteres.')

    with get_db() as conn:
        actual = conn.execute('SELECT estado FROM constancia WHERE id = ?',
                              (constancia_id,)).fetchone()
        if not actual:
            raise AnulacionInvalida('La constancia no existe.')
        if actual['estado'] != 'vigente':
            raise AnulacionInvalida(
                f'Solo se puede anular una constancia vigente '
                f'(esta está {actual["estado"]}).')

        try:
            conn.execute("""
                UPDATE constancia
                   SET estado = 'anulada', anulada_at = ?, motivo_anulacion = ?
                 WHERE id = ?
            """, (iso_utc(ahora_utc()), motivo, constancia_id))
        except sqlite3.IntegrityError as e:
            # Los triggers son la autoridad final; si rechazan, se propaga el
            # mensaje con el número de regla.
            raise AnulacionInvalida(str(e))

    return obtener(constancia_id)


def reemplazar(constancia_id, datos, motivo, usuario_id=None):
    """Anula por corrección y emite una nueva que referencia a la anterior.

    Las dos operaciones van en la misma transacción de emitir(): la nueva
    constancia se inserta con `reemplaza_a` y el propio repositorio marca la
    anterior como 'reemplazada'. Así no existe un instante en el que ambas
    estén vigentes.
    """
    motivo = (motivo or '').strip()
    if len(motivo) < LARGO_MINIMO_MOTIVO:
        raise AnulacionInvalida(
            f'El motivo del reemplazo es obligatorio y debe tener al menos '
            f'{LARGO_MINIMO_MOTIVO} caracteres.')

    anterior = obtener(constancia_id)
    if not anterior:
        raise AnulacionInvalida('La constancia que se quiere reemplazar no existe.')
    if anterior['estado'] != 'vigente':
        raise AnulacionInvalida(
            f'Solo se puede reemplazar una constancia vigente '
            f'(esta está {anterior["estado"]}).')

    nuevos = dict(datos)
    nuevos['reemplaza_a'] = constancia_id
    # El reemplazo es del mismo cliente por definición; el trigger lo verifica.
    nuevos['cliente_id'] = anterior['cliente_id']
    if usuario_id:
        nuevos['emitida_por_usuario_id'] = usuario_id

    try:
        nueva = emitir(nuevos)
    except sqlite3.IntegrityError as e:
        raise AnulacionInvalida(str(e))

    # El motivo queda anotado en la constancia reemplazada, que ya cambió de
    # estado dentro de la transacción de emitir().
    with get_db() as conn:
        conn.execute("""
            UPDATE constancia SET motivo_anulacion = ?
             WHERE id = ? AND motivo_anulacion IS NULL
        """, (motivo, constancia_id))

    return nueva


def datos_para_reemplazo(constancia_id):
    """Precarga el formulario con los datos de la constancia a reemplazar.

    Se copian los campos de negocio, nunca folio, código, hashes ni fechas de
    emisión: la nueva constancia es un documento distinto.
    """
    c = obtener(constancia_id)
    if not c:
        return None
    campos = (
        'cliente_id', 'direccion_retiro_id', 'fecha_retiro_inicio',
        'fecha_retiro_termino', 'tipo_material_id', 'cantidad_m3_cent',
        'metodo_medicion', 'peso_kg_cent', 'n_contenedores',
        'tipo_contenedor_id', 'n_viajes', 'vehiculo_id', 'conductor_id',
        'destino_id', 'comprobante_destino', 'receptor_nombre',
        'receptor_rut', 'receptor_cargo', 'observaciones',
    )
    return {k: c[k] for k in campos}
