"""La aplicación arranca y las rutas responden.

Red de seguridad del refactor del Hito 0.5: la factory y la extracción de
db.py/auth.py no deben cambiar nada de lo que ya funcionaba.
"""
import pytest


RUTAS_PUBLICAS = [
    '/', '/servicios', '/certificaciones', '/flota', '/faq',
    '/contacto', '/nosotros', '/que-es-respel', '/que-es-reas',
    '/transporte-puerto-montt', '/robots.txt', '/sitemap.xml',
]


@pytest.mark.parametrize('ruta', RUTAS_PUBLICAS)
def test_rutas_publicas_responden(cliente_http, ruta):
    assert cliente_http.get(ruta).status_code == 200


def test_404_usa_su_plantilla(cliente_http):
    assert cliente_http.get('/no-existe').status_code == 404


class TestAislamientoDeLaFactory:
    """Cada app tiene su propia base: es lo que hace testeable el resto."""

    def test_cada_instancia_usa_su_db(self, tmp_path):
        from app import crear_app
        from db import db_path

        a = crear_app({'DB_PATH': tmp_path / 'a.db', 'SECRET_KEY': 'x'})
        b = crear_app({'DB_PATH': tmp_path / 'b.db', 'SECRET_KEY': 'x'})

        with a.app_context():
            ruta_a = db_path()
        with b.app_context():
            ruta_b = db_path()

        assert ruta_a != ruta_b
        assert ruta_a.exists() and ruta_b.exists()


class TestPanelProtegido:

    @pytest.mark.parametrize('ruta', [
        '/admin/cotizaciones', '/admin/cotizar', '/admin/cotizaciones/1',
        '/admin/cotizaciones/1/pdf',
    ])
    def test_exige_sesion(self, cliente_http, ruta):
        r = cliente_http.get(ruta)
        assert r.status_code == 302
        assert '/admin/login' in r.headers['Location']

    def test_login_se_muestra(self, cliente_http):
        assert cliente_http.get('/admin/login').status_code == 200


class TestRedirectAbierto:
    """El ?next= del login no puede sacar al usuario del sitio."""

    @pytest.mark.parametrize('malicioso', [
        'https://sitio-malicioso.cl',
        '//sitio-malicioso.cl',
        '/otra-cosa',
        None,
        '',
    ])
    def test_destino_externo_se_descarta(self, malicioso):
        from auth import destino_seguro
        assert destino_seguro(malicioso, '/admin/cotizaciones') == '/admin/cotizaciones'

    def test_destino_interno_se_respeta(self):
        from auth import destino_seguro
        assert destino_seguro('/admin/cotizar', '/admin/cotizaciones') == '/admin/cotizar'


class TestEsquemaExistente:
    """init_db() sigue creando la tabla de cotizaciones."""

    def test_tabla_quotes(self, conn):
        fila = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='quotes'"
        ).fetchone()
        assert fila is not None

    def test_foreign_keys_activas(self, conn):
        """Sin este PRAGMA por conexión, las FK son decorativas."""
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
