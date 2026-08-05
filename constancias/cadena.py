"""Cadena de integridad: cada constancia encadena con el hash de la anterior.

Alterar una constancia ya emitida obliga a recalcular todas las posteriores.
Combinado con el anclaje diario —publicar el último hash del día en un canal
externo con fecha verificable— eso acota la manipulación a la ventana desde el
último anclaje.

Lo que la cadena NO hace: probar que el retiro ocurrió. Prueba que este
documento existe desde tal fecha y no cambió desde entonces.
"""
import hashlib
from typing import Mapping

from .dominio import fmt_m3, nfc


VERSION = 1
DOMINIO = 'MDS-CONSTANCIA-v1'

# Separador ASCII "record separator": no imprimible, imposible de tecleaar en
# ninguno de estos campos, así que no hace falta escapar nada. Se valida igual.
SEP = '\x1e'

# Primer eslabón: no existe una fila 0 ficticia, se usa esta constante.
GENESIS = '0' * 64


class CadenaCorrupta(Exception):
    """La cadena no verifica: hay una fila alterada o un eslabón roto."""


def serializar(c: Mapping) -> bytes:
    """Forma canónica de una constancia, la que entra al SHA-256.

    Decisiones que hacen esto reproducible dentro de diez años:
      - Prefijo de dominio: separa este hash de cualquier otro uso.
      - `snap_*` en vez de joins vivos: renombrar un catálogo no puede romper
        la cadena de documentos ya emitidos.
      - `cantidad_m3` formateada desde el entero de centésimas, con punto y dos
        decimales. Nunca `str(float)`.
      - `emitida_at` en UTC con Z y largo fijo.
      - NFC: 'Petróleo' con acento precompuesto o combinante da el mismo hash.
    """
    campos = [
        DOMINIO,
        c['folio'],
        c['codigo_verificacion'],
        c['snap_cliente_rut'],
        c['fecha_retiro_inicio'],
        fmt_m3(c['cantidad_m3_cent']),
        c['snap_tipo_material'],
        str(int(c['destino_id'])),
        c['emitida_at'],
        c['hash_anterior'],
    ]
    for campo in campos:
        if SEP in campo:
            raise ValueError('El separador no puede aparecer dentro de un campo.')
    return SEP.join(nfc(campo) for campo in campos).encode('utf-8')


def hash_constancia(c: Mapping) -> str:
    return hashlib.sha256(serializar(c)).hexdigest()


def ultimo_hash(conn) -> str:
    """Hash del último eslabón, o GENESIS si la cadena está vacía.

    Debe leerse DENTRO de la misma transacción `BEGIN IMMEDIATE` en la que se
    inserta: si no, dos emisiones simultáneas leen el mismo último eslabón y
    bifurcan la cadena.
    """
    fila = conn.execute(
        'SELECT hash_actual FROM constancia ORDER BY id DESC LIMIT 1'
    ).fetchone()
    return fila['hash_actual'] if fila else GENESIS


def verificar_cadena(conn):
    """Recorre la cadena entera y comprueba encadenamiento y hashes.

    Devuelve (ok, n_verificadas, problemas). No lanza: quien llama decide si un
    problema es motivo de alerta o de corte.
    """
    problemas = []
    esperado = GENESIS
    n = 0

    for fila in conn.execute('SELECT * FROM constancia ORDER BY id ASC'):
        n += 1
        c = dict(fila)

        if c['hash_anterior'] != esperado:
            problemas.append(
                f"id={c['id']} folio={c['folio']}: hash_anterior no coincide "
                f"con el eslabon previo"
            )

        if c['hash_version'] != VERSION:
            problemas.append(
                f"id={c['id']}: hash_version {c['hash_version']} desconocida"
            )
        else:
            recalculado = hash_constancia(c)
            if recalculado != c['hash_actual']:
                problemas.append(
                    f"id={c['id']} folio={c['folio']}: el contenido no "
                    f"corresponde a su hash (fila alterada)"
                )

        esperado = c['hash_actual']

    return (not problemas), n, problemas


def verificar_anclajes(conn):
    """Compara cada anclaje publicado con la fila que decía anclar.

    Esto es lo que detecta manipulación POSTERIOR a un anclaje, que es el
    escenario que importa: si el hash de la constancia cambió pero el anclaje
    ya salió por correo a un tercero, la discrepancia queda a la vista.
    """
    problemas = []
    n = 0
    consulta = """
        SELECT a.fecha, a.folio, a.hash_actual AS anclado, c.hash_actual AS actual
          FROM anclaje_diario a
          LEFT JOIN constancia c ON c.id = a.constancia_id
         WHERE a.constancia_id IS NOT NULL
         ORDER BY a.fecha
    """
    for fila in conn.execute(consulta):
        n += 1
        if fila['actual'] is None:
            problemas.append(f"anclaje {fila['fecha']}: la constancia anclada ya no existe")
        elif fila['anclado'] != fila['actual']:
            problemas.append(
                f"anclaje {fila['fecha']} folio={fila['folio']}: el hash cambió "
                f"DESPUÉS de haber sido publicado"
            )
    return (not problemas), n, problemas
