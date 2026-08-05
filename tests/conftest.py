"""Fixtures compartidas.

La base de datos de los tests es un archivo real en `tmp_path`, no `:memory:`.
Dos razones: los triggers y `PRAGMA foreign_keys` son parte de lo que se
testea, y una base en memoria no comparte estado entre conexiones, que es
justo el patrón que usa get_db().

La fixture `app` deja empujado el contexto de aplicación durante todo el test,
para que get_db() funcione sin envolver cada línea en `with app.app_context()`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def app(tmp_path):
    from app import crear_app
    aplicacion = crear_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
        'DB_PATH': tmp_path / 'test.db',
    })
    ctx = aplicacion.app_context()
    ctx.push()
    yield aplicacion
    ctx.pop()


@pytest.fixture
def cliente_http(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    from db import get_db
    with get_db() as c:
        yield c


# --- Datos semilla ----------------------------------------------------

@pytest.fixture
def semilla(app):
    """Catálogos y maestros mínimos para poder emitir una constancia.

    Los RUT son válidos de verdad (módulo 11), no de relleno: el sistema los
    normaliza y valida en el borde, así que un RUT falso rompería el test por
    la razón equivocada.
    """
    from db import get_db

    with get_db() as c:
        c.executescript("""
            INSERT INTO comuna (id, nombre, region) VALUES
                (1, 'Puerto Montt', 'Los Lagos'),
                (2, 'Osorno',       'Los Lagos');

            INSERT INTO tipo_material (id, nombre) VALUES
                (1, 'Residuos peligrosos (RESPEL)'),
                (2, 'Residuos hospitalarios (REAS)');

            INSERT INTO tipo_contenedor (id, nombre, capacidad_m3_cent) VALUES
                (1, 'Contenedor 1 m3', 100),
                (2, 'Contenedor 5 m3', 500);

            INSERT INTO usuario (id, email, nombre, rol, created_at) VALUES
                (1, 'sistema@transportesmardelsur.cl', 'Sistema', 'admin',
                 '2026-01-01T00:00:00Z');

            INSERT INTO cliente (id, razon_social, rut, comuna_id, created_at) VALUES
                (1, 'Salmonera del Sur SPA', '76123456-0', 1, '2026-01-01T00:00:00Z'),
                (2, 'Otra Empresa Ltda',     '12345670-K', 2, '2026-01-01T00:00:00Z');

            INSERT INTO direccion_retiro
                   (id, cliente_id, nombre_referencia, calle, numero, comuna_id) VALUES
                (1, 1, 'Planta Chinquihue', 'Camino Chinquihue', 'km 8', 1),
                (2, 2, 'Bodega Osorno',     'Ruta 5 Sur',        'km 920', 2);

            INSERT INTO destino (id, nombre, comuna_id, tipo_operacion, autorizacion) VALUES
                (1, 'Relleno Sanitario Los Lagos', 2, 'disposicion_final', 'RES-1234'),
                (2, 'Planta de Valorización Sur',  1, 'valorizacion',      'RES-5678');

            INSERT INTO vehiculo (id, patente, tipo, capacidad_m3_cent) VALUES
                (1, 'KXTR-45', 'Camión ampliroll', 1200);

            INSERT INTO conductor (id, nombre, rut, telefono) VALUES
                (1, 'Juan Pérez', '7123456-8', '+56912345678');
        """)
    return True


@pytest.fixture
def datos_base(semilla):
    """Datos válidos para emitir. Cada test los modifica para romper una regla."""
    return {
        'cliente_id': 1,
        'direccion_retiro_id': 1,
        'fecha_retiro_inicio': '2026-08-01',
        'fecha_retiro_termino': '2026-08-01',
        'tipo_material_id': 1,
        'cantidad_m3_cent': 1210,          # 12,10 m3
        'metodo_medicion': 'pesaje',
        'peso_kg_cent': 850_000,           # 8.500,00 kg
        'n_contenedores': None,
        'tipo_contenedor_id': None,
        'n_viajes': 2,
        'vehiculo_id': 1,
        'conductor_id': 1,
        'destino_id': 1,
        'comprobante_destino': 'REC-99881',
        'receptor_nombre': 'María González',
        'receptor_rut': '7123456-8',
        'receptor_cargo': 'Jefa de planta',
        'observaciones': 'Retiro coordinado con turno de mañana.',
        'emitida_por_usuario_id': 1,
        'reemplaza_a': None,
        'firma_path': None,
        'lat': None,
        'lng': None,
    }


@pytest.fixture
def constancia(app, datos_base):
    from constancias.repositorio import emitir
    return emitir(datos_base)


@pytest.fixture
def otro_cliente(semilla):
    """Id de un cliente distinto al de `datos_base`, para probar RN-08."""
    return 2
