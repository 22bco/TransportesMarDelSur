"""Anulación y reemplazo: RN-01, RN-02 y RN-03.

Corregir una constancia emitida solo puede hacerse de dos formas: anularla, o
reemplazarla por una nueva. Cualquier otro camino tiene que fallar.
"""
import sqlite3

import pytest

from constancias.ciclo_vida import AnulacionInvalida, anular, reemplazar
from constancias.dominio import ultimos_4_rut


MOTIVO = 'La cantidad registrada no corresponde al retiro efectuado.'


class TestRN02Anulacion:

    def test_anular_con_motivo(self, app, constancia):
        c = anular(constancia['id'], MOTIVO)
        assert c['estado'] == 'anulada'
        assert c['anulada_at']
        assert c['motivo_anulacion'] == MOTIVO

    @pytest.mark.parametrize('motivo', ['', '   ', None, 'corto', 'nueve car'])
    def test_sin_motivo_o_muy_corto_falla(self, app, constancia, motivo):
        with pytest.raises(AnulacionInvalida):
            anular(constancia['id'], motivo)
        # Y la constancia sigue vigente.
        from constancias.repositorio import obtener
        assert obtener(constancia['id'])['estado'] == 'vigente'

    def test_el_motivo_no_se_reescribe(self, app, constancia):
        """Reescribir el motivo permitiría reinterpretar la historia."""
        from db import get_db
        anular(constancia['id'], MOTIVO)
        with get_db() as conn:
            with pytest.raises(sqlite3.IntegrityError, match='RN-02'):
                conn.execute(
                    'UPDATE constancia SET motivo_anulacion = ? WHERE id = ?',
                    ('Otro motivo completamente distinto.', constancia['id']))

    def test_no_se_puede_anular_dos_veces(self, app, constancia):
        anular(constancia['id'], MOTIVO)
        with pytest.raises(AnulacionInvalida):
            anular(constancia['id'], 'Otro motivo suficientemente largo.')

    def test_no_se_puede_desanular(self, app, constancia):
        """La máquina de estados no tiene marcha atrás."""
        from db import get_db
        anular(constancia['id'], MOTIVO)
        with get_db() as conn:
            with pytest.raises(sqlite3.IntegrityError, match='RN-03'):
                conn.execute("UPDATE constancia SET estado = 'vigente' WHERE id = ?",
                             (constancia['id'],))

    def test_anular_no_altera_los_datos_ni_el_hash(self, app, constancia):
        from constancias.cadena import hash_constancia
        anulada = anular(constancia['id'], MOTIVO)
        assert anulada['hash_actual'] == constancia['hash_actual']
        assert anulada['cantidad_m3_cent'] == constancia['cantidad_m3_cent']
        # El hash no cubre el estado: sigue verificando.
        assert hash_constancia(anulada) == anulada['hash_actual']


class TestRN03Reemplazo:

    def test_reemplazo_encadena_las_dos(self, app, constancia, datos_base):
        from constancias.repositorio import obtener
        datos_base['cantidad_m3_cent'] = 800
        nueva = reemplazar(constancia['id'], datos_base, 'Se corrige la cantidad medida.')

        anterior = obtener(constancia['id'])
        assert anterior['estado'] == 'reemplazada'
        assert nueva['reemplaza_a'] == constancia['id']
        assert nueva['folio'] != constancia['folio']
        assert nueva['codigo_verificacion'] != constancia['codigo_verificacion']
        assert nueva['cantidad_m3_cent'] == 800

    def test_la_cadena_sigue_integra_tras_el_reemplazo(self, app, constancia, datos_base):
        from constancias.cadena import verificar_cadena
        from db import get_db
        reemplazar(constancia['id'], datos_base, 'Se corrige la cantidad medida.')
        with get_db() as conn:
            ok, n, problemas = verificar_cadena(conn)
        assert ok, problemas
        assert n == 2

    def test_no_se_reemplaza_una_anulada(self, app, constancia, datos_base):
        anular(constancia['id'], MOTIVO)
        with pytest.raises(AnulacionInvalida):
            reemplazar(constancia['id'], datos_base, 'Motivo suficientemente largo.')

    def test_reemplazo_exige_motivo(self, app, constancia, datos_base):
        with pytest.raises(AnulacionInvalida):
            reemplazar(constancia['id'], datos_base, 'corto')

    def test_no_se_reemplaza_dos_veces(self, app, constancia, datos_base):
        reemplazar(constancia['id'], datos_base, 'Primera corrección de la cantidad.')
        with pytest.raises(AnulacionInvalida):
            reemplazar(constancia['id'], datos_base, 'Segunda corrección de la cantidad.')


class TestVerificacionPublicaTrasElCambio:

    def test_anulada_se_muestra_en_amarillo_sin_el_motivo(self, cliente_http, constancia):
        anular(constancia['id'], MOTIVO)
        r = cliente_http.post('/verificar', data={
            'codigo': constancia['codigo_verificacion'],
            'rut4': ultimos_4_rut(constancia['snap_cliente_rut'])})
        texto = r.get_data(as_text=True)
        assert 'Documento anulado' in texto
        assert 'No debe considerarse vigente' in texto
        # El motivo puede contener información del cliente: no se publica.
        assert MOTIVO not in texto

    def test_reemplazada_muestra_solo_el_folio_nuevo(self, cliente_http, constancia,
                                                     datos_base):
        nueva = reemplazar(constancia['id'], datos_base, 'Se corrige la cantidad medida.')
        r = cliente_http.post('/verificar', data={
            'codigo': constancia['codigo_verificacion'],
            'rut4': ultimos_4_rut(constancia['snap_cliente_rut'])})
        texto = r.get_data(as_text=True)
        assert 'Documento reemplazado' in texto
        assert nueva['folio'] in texto
        # El código de la nueva la expondría sin su segundo factor.
        assert nueva['codigo_verificacion'] not in texto

    def test_la_nueva_verifica_como_vigente(self, cliente_http, constancia, datos_base):
        nueva = reemplazar(constancia['id'], datos_base, 'Se corrige la cantidad medida.')
        r = cliente_http.post('/verificar', data={
            'codigo': nueva['codigo_verificacion'],
            'rut4': ultimos_4_rut(nueva['snap_cliente_rut'])})
        assert 'Documento verificado' in r.get_data(as_text=True)


class TestAnclaje:

    def test_ancla_el_ultimo_hash_del_dia(self, app, constancia, monkeypatch):
        from constancias import anclaje
        from db import get_db

        monkeypatch.setattr(anclaje, 'fecha_objetivo',
                            lambda: constancia['emitida_fecha_local'])
        monkeypatch.setattr(anclaje, 'enviar_correo', lambda *a, **k: False)
        assert anclaje.ejecutar() == 0

        with get_db() as conn:
            fila = conn.execute('SELECT * FROM anclaje_diario').fetchone()
        assert fila['hash_actual'] == constancia['hash_actual']
        assert fila['folio'] == constancia['folio']
        assert fila['n_constancias_dia'] == 1

    def test_es_idempotente(self, app, constancia, monkeypatch):
        """Reejecutar el mismo día no debe duplicar ni ensuciar."""
        from constancias import anclaje
        from db import get_db

        monkeypatch.setattr(anclaje, 'fecha_objetivo',
                            lambda: constancia['emitida_fecha_local'])
        monkeypatch.setattr(anclaje, 'enviar_correo', lambda *a, **k: False)
        anclaje.ejecutar()
        anclaje.ejecutar()

        with get_db() as conn:
            assert conn.execute('SELECT COUNT(*) AS n FROM anclaje_diario'
                                ).fetchone()['n'] == 1

    def test_detecta_manipulacion_posterior_al_anclaje(self, app, constancia,
                                                       monkeypatch):
        """El escenario que de verdad importa.

        Una vez que el hash salió a un tercero, cambiar la fila tiene que
        quedar a la vista aunque alguien lograra saltarse los triggers.
        """
        from constancias import anclaje
        from constancias.cadena import verificar_anclajes
        from db import get_db

        monkeypatch.setattr(anclaje, 'fecha_objetivo',
                            lambda: constancia['emitida_fecha_local'])
        monkeypatch.setattr(anclaje, 'enviar_correo', lambda *a, **k: False)
        anclaje.ejecutar()

        # Simula el ataque alterando el anclaje, que no tiene triggers.
        with get_db() as conn:
            conn.execute("UPDATE anclaje_diario SET hash_actual = ?",
                         ('f' * 64,))
            ok, n, problemas = verificar_anclajes(conn)

        assert not ok
        assert 'DESPUÉS' in problemas[0]

    def test_sin_emisiones_ancla_igual_para_no_dejar_huecos(self, app, constancia,
                                                            monkeypatch):
        from constancias import anclaje
        from db import get_db

        monkeypatch.setattr(anclaje, 'fecha_objetivo', lambda: '2099-12-31')
        monkeypatch.setattr(anclaje, 'enviar_correo', lambda *a, **k: False)
        anclaje.ejecutar()

        with get_db() as conn:
            fila = conn.execute("SELECT * FROM anclaje_diario WHERE fecha = '2099-12-31'"
                                ).fetchone()
        assert fila['n_constancias_dia'] == 0
        assert fila['hash_actual'] == constancia['hash_actual']


class TestPermisos:
    """Anular y reemplazar son solo de admin."""

    def test_operador_no_puede_anular(self, cliente_http, app, constancia):
        import auth
        auth.crear_usuario('op@x.cl', 'Operador', 'contrasena-larga-123',
                           rol='operador')
        cliente_http.post('/admin/login',
                          data={'email': 'op@x.cl', 'password': 'contrasena-larga-123'})
        r = cliente_http.get(f"/admin/constancias/{constancia['id']}/anular")
        assert r.status_code == 302   # rebotado

    def test_admin_si_puede(self, cliente_http, app, constancia):
        import auth
        auth.crear_usuario('jefe@x.cl', 'Jefe', 'contrasena-larga-123', rol='admin')
        cliente_http.post('/admin/login',
                          data={'email': 'jefe@x.cl', 'password': 'contrasena-larga-123'})
        r = cliente_http.get(f"/admin/constancias/{constancia['id']}/anular")
        assert r.status_code == 200
