"""Autenticación: usuarios nominados y migración sin corte.

Lo que se protege aquí es la trazabilidad: una constancia afirma quién la
emitió, y esa afirmación solo vale si detrás hay una persona identificable.
"""
import pytest

import auth


@pytest.fixture
def usuario(app):
    uid = auth.crear_usuario('operador@transportesmardelsur.cl', 'Operador de Prueba',
                             'contrasena-larga-123', rol='operador')
    return auth.por_id(uid)


class TestHashing:

    def test_la_contrasena_no_se_guarda_en_claro(self, usuario):
        assert 'contrasena-larga-123' not in usuario['password_hash']
        assert usuario['password_hash'].startswith(('scrypt:', 'pbkdf2:'))

    def test_dos_usuarios_con_la_misma_clave_tienen_hash_distinto(self, app):
        a = auth.por_id(auth.crear_usuario('a@x.cl', 'A', 'contrasena-larga-123'))
        b = auth.por_id(auth.crear_usuario('b@x.cl', 'B', 'contrasena-larga-123'))
        assert a['password_hash'] != b['password_hash']

    @pytest.mark.parametrize('mala', ['corta', '', 'password', '12345678901'])
    def test_rechaza_contrasenas_debiles(self, app, mala):
        with pytest.raises(auth.PasswordDebil):
            auth.crear_usuario('x@y.cl', 'X', mala)

    def test_rechaza_la_contrasena_igual_al_usuario(self, app):
        with pytest.raises(auth.PasswordDebil):
            auth.crear_usuario('mardelsur@x.cl', 'X', 'mardelsur')


class TestAutenticar:

    def test_credenciales_correctas(self, usuario):
        assert auth.autenticar(usuario['email'], 'contrasena-larga-123')

    def test_contrasena_incorrecta(self, usuario):
        assert auth.autenticar(usuario['email'], 'otra-cosa-larga-123') is None

    def test_usuario_inexistente(self, app):
        assert auth.autenticar('nadie@x.cl', 'contrasena-larga-123') is None

    def test_email_sin_distinguir_mayusculas(self, usuario):
        assert auth.autenticar('OPERADOR@TRANSPORTESMARDELSUR.CL',
                               'contrasena-larga-123')

    def test_usuario_desactivado_no_entra(self, app, usuario):
        from db import get_db
        with get_db() as conn:
            conn.execute('UPDATE usuario SET activo = 0 WHERE id = ?', (usuario['id'],))
        assert auth.autenticar(usuario['email'], 'contrasena-larga-123') is None


class TestLoginHTTP:

    def test_login_nominado(self, cliente_http, usuario):
        r = cliente_http.post('/admin/login', data={
            'email': usuario['email'], 'password': 'contrasena-larga-123'})
        assert r.status_code == 302
        with cliente_http.session_transaction() as s:
            assert s['usuario_id'] == usuario['id']
            assert s['rol'] == 'operador'
            # Compatibilidad: admin.py y sus plantillas leen esta marca.
            assert s['admin'] is True

    def test_credenciales_malas_no_abren_sesion(self, cliente_http, usuario):
        r = cliente_http.post('/admin/login', data={
            'email': usuario['email'], 'password': 'incorrecta-pero-larga'})
        assert r.status_code == 200
        with cliente_http.session_transaction() as s:
            assert not s.get('admin')

    def test_no_revela_si_la_cuenta_existe(self, cliente_http, usuario):
        """Mismo mensaje para 'no existe' y 'contraseña incorrecta'."""
        existe = cliente_http.post('/admin/login', data={
            'email': usuario['email'], 'password': 'incorrecta-pero-larga'})
        no_existe = cliente_http.post('/admin/login', data={
            'email': 'fantasma@x.cl', 'password': 'incorrecta-pero-larga'})
        assert existe.get_data() == no_existe.get_data()

    def test_login_legacy_sigue_funcionando(self, cliente_http, monkeypatch):
        """La migración no puede dejar a nadie fuera de golpe."""
        monkeypatch.setenv('ADMIN_PASSWORD', 'la-de-siempre')
        monkeypatch.setenv('PERMITIR_LOGIN_LEGACY', '1')
        r = cliente_http.post('/admin/login', data={'password': 'la-de-siempre'})
        assert r.status_code == 302
        with cliente_http.session_transaction() as s:
            assert s['admin'] is True
            assert 'usuario_id' not in s   # no hay persona detrás

    def test_legacy_se_puede_apagar(self, cliente_http, monkeypatch):
        monkeypatch.setenv('ADMIN_PASSWORD', 'la-de-siempre')
        monkeypatch.setenv('PERMITIR_LOGIN_LEGACY', '0')
        r = cliente_http.post('/admin/login', data={'password': 'la-de-siempre'})
        assert r.status_code == 200
        with cliente_http.session_transaction() as s:
            assert not s.get('admin')


class TestCambioDePassword:

    def test_cambio_correcto(self, app, usuario):
        auth.cambiar_password(usuario['id'], 'otra-contrasena-larga')
        assert auth.autenticar(usuario['email'], 'otra-contrasena-larga')
        assert auth.autenticar(usuario['email'], 'contrasena-larga-123') is None

    def test_rechaza_debil(self, app, usuario):
        with pytest.raises(auth.PasswordDebil):
            auth.cambiar_password(usuario['id'], 'corta')

    def test_password_de_arranque_obliga_a_cambiarla(self, cliente_http, app):
        uid = auth.crear_usuario('nuevo@x.cl', 'Nuevo', 'contrasena-larga-123',
                                 debe_cambiar_password=1)
        cliente_http.post('/admin/login',
                          data={'email': 'nuevo@x.cl', 'password': 'contrasena-larga-123'})
        r = cliente_http.get('/admin/cotizaciones')
        assert r.status_code == 302
        assert 'cambiar-password' in r.headers['Location']


class TestBootstrap:

    def test_crea_el_primer_admin(self, app, monkeypatch):
        monkeypatch.setenv('ADMIN_EMAIL', 'jefe@transportesmardelsur.cl')
        monkeypatch.setenv('ADMIN_PASSWORD', 'contrasena-de-arranque')
        uid = auth.bootstrap_admin()
        creado = auth.por_id(uid)
        assert creado['rol'] == 'admin'
        assert creado['debe_cambiar_password'] == 1

    def test_no_pisa_usuarios_existentes(self, app, usuario, monkeypatch):
        monkeypatch.setenv('ADMIN_EMAIL', 'jefe@transportesmardelsur.cl')
        monkeypatch.setenv('ADMIN_PASSWORD', 'contrasena-de-arranque')
        assert auth.bootstrap_admin() is None

    def test_sin_variables_no_hace_nada(self, app, monkeypatch):
        monkeypatch.delenv('ADMIN_EMAIL', raising=False)
        assert auth.bootstrap_admin() is None


class TestAtribucion:
    """El punto de todo esto."""

    def test_la_constancia_registra_quien_la_emitio(self, cliente_http, usuario,
                                                    semilla, app):
        cliente_http.post('/admin/login', data={
            'email': usuario['email'], 'password': 'contrasena-larga-123'})
        r = cliente_http.post('/admin/constancias/emitir', data={
            'cliente_id': '1', 'direccion_retiro_id': '1',
            'fecha_retiro_inicio': '2026-08-01', 'fecha_retiro_termino': '2026-08-01',
            'tipo_material_id': '1', 'cantidad_m3': '5', 'metodo_medicion': 'pesaje',
            'destino_id': '1', 'n_viajes': '1',
        })
        assert r.status_code == 302

        from constancias.repositorio import listar
        assert listar()[0]['emitida_por_usuario_id'] == usuario['id']
