"""Reglas de negocio RN-01 a RN-09, verificadas contra la BASE DE DATOS.

Estos tests no ejercitan la aplicación: atacan directamente la tabla con SQL
crudo. Es a propósito. La invariante central —una constancia emitida no se
modifica— tiene que sostenerse aunque la aplicación tenga un bug, aunque
alguien escriba un script suelto, o aunque se entre por el CLI de sqlite3.
"""
import sqlite3

import pytest

from constancias.cadena import GENESIS, hash_constancia
from constancias.repositorio import DatosInvalidos, emitir


def test_rn01_update_de_un_campo_de_negocio_aborta(constancia):
    """El caso más importante del sistema entero."""
    with pytest.raises(sqlite3.IntegrityError, match='RN-01'):
        with_conn_update(constancia, 'cantidad_m3_cent', 1)


def test_rn01_update_del_hash_aborta(constancia):
    """Ni siquiera se puede 'arreglar' el hash para tapar una alteración."""
    with pytest.raises(sqlite3.IntegrityError, match='RN-01'):
        with_conn_update(constancia, 'hash_actual', 'f' * 64)


def test_rn01_update_del_folio_aborta(constancia):
    with pytest.raises(sqlite3.IntegrityError, match='RN-01'):
        with_conn_update(constancia, 'folio', 'RT-2026-99999')


def test_rn01_update_del_receptor_aborta(constancia):
    with pytest.raises(sqlite3.IntegrityError, match='RN-01'):
        with_conn_update(constancia, 'receptor_nombre', 'Otro Nombre')


def test_rn01_delete_aborta(app, constancia):
    from db import get_db
    with app.app_context(), get_db() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='RN-01'):
            conn.execute('DELETE FROM constancia WHERE id = ?', (constancia['id'],))


def test_rn01_delete_masivo_aborta(app, constancia):
    """'DELETE FROM constancia' sin WHERE, el clásico accidente."""
    from db import get_db
    with app.app_context(), get_db() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='RN-01'):
            conn.execute('DELETE FROM constancia')


class TestRN04FechasCoherentes:

    def test_termino_anterior_al_inicio(self, app, datos_base):
        datos_base.update(fecha_retiro_inicio='2026-08-05',
                          fecha_retiro_termino='2026-08-01')
        with app.app_context(), pytest.raises(sqlite3.IntegrityError):
            emitir(datos_base)

    def test_mismo_dia_es_valido(self, app, datos_base):
        datos_base.update(fecha_retiro_inicio='2026-08-01',
                          fecha_retiro_termino='2026-08-01')
        with app.app_context():
            assert emitir(datos_base)['folio']


class TestRN05SinFechasFuturas:

    def test_retiro_en_el_futuro_aborta(self, app, datos_base):
        datos_base.update(fecha_retiro_inicio='2099-01-01',
                          fecha_retiro_termino='2099-01-02')
        with app.app_context(), pytest.raises(sqlite3.IntegrityError):
            emitir(datos_base)


class TestRN06CantidadPositiva:

    @pytest.mark.parametrize('cantidad', [0, -1, -1210])
    def test_cantidad_no_positiva_aborta(self, app, datos_base, cantidad):
        datos_base['cantidad_m3_cent'] = cantidad
        with app.app_context(), pytest.raises(sqlite3.IntegrityError):
            emitir(datos_base)


class TestRN07MetodoContenedor:

    def test_contenedor_sin_datos_aborta(self, app, datos_base):
        datos_base.update(metodo_medicion='contenedor',
                          n_contenedores=None, tipo_contenedor_id=None)
        with app.app_context(), pytest.raises(sqlite3.IntegrityError):
            emitir(datos_base)

    def test_contenedor_sin_tipo_aborta(self, app, datos_base):
        datos_base.update(metodo_medicion='contenedor',
                          n_contenedores=2, tipo_contenedor_id=None)
        with app.app_context(), pytest.raises(sqlite3.IntegrityError):
            emitir(datos_base)

    def test_contenedor_completo_es_valido(self, app, datos_base):
        datos_base.update(metodo_medicion='contenedor',
                          n_contenedores=2, tipo_contenedor_id=1)
        with app.app_context():
            assert emitir(datos_base)['metodo_medicion'] == 'contenedor'

    def test_otros_metodos_no_exigen_contenedor(self, app, datos_base):
        datos_base['metodo_medicion'] = 'estimacion_visual'
        with app.app_context():
            assert emitir(datos_base)['folio']

    def test_metodo_inventado_aborta(self, app, datos_base):
        datos_base['metodo_medicion'] = 'a_ojo'
        with app.app_context(), pytest.raises(sqlite3.IntegrityError):
            emitir(datos_base)


class TestRN08DireccionDelCliente:

    def test_direccion_de_otro_cliente_aborta(self, app, datos_base, otro_cliente):
        """El error clásico del formulario: cambiar de cliente y no de dirección."""
        datos_base['cliente_id'] = otro_cliente
        with app.app_context(), pytest.raises(sqlite3.IntegrityError, match='RN-08'):
            emitir(datos_base)


class TestRN09CadenaDeHash:

    def test_primera_constancia_arranca_en_genesis(self, app, datos_base):
        with app.app_context():
            c = emitir(datos_base)
        assert c['hash_anterior'] == GENESIS

    def test_cada_eslabon_apunta_al_anterior(self, app, datos_base):
        with app.app_context():
            a = emitir(datos_base)
            b = emitir(datos_base)
            c = emitir(datos_base)
        assert b['hash_anterior'] == a['hash_actual']
        assert c['hash_anterior'] == b['hash_actual']

    def test_hash_anterior_inventado_aborta(self, app, datos_base):
        """Insertar saltándose el repositorio tampoco permite bifurcar."""
        from db import get_db
        with app.app_context():
            emitir(datos_base)
            base = emitir(datos_base)
            with get_db() as conn:
                fila = dict(base)
                fila.update(id=None, folio='RT-2026-90001', seq=90001,
                            codigo_verificacion='ZZZZZZZZZZZZ',
                            hash_anterior='a' * 64, hash_actual='b' * 64)
                cols = [k for k in fila if k != 'id']
                sql = (f"INSERT INTO constancia ({','.join(cols)}) "
                       f"VALUES ({','.join('?' * len(cols))})")
                with pytest.raises(sqlite3.IntegrityError, match='RN-09'):
                    conn.execute(sql, tuple(fila[c] for c in cols))

    def test_hash_reproducible(self, app, datos_base):
        """Recalcular desde la fila guardada da el mismo hash."""
        with app.app_context():
            c = emitir(datos_base)
        assert hash_constancia(c) == c['hash_actual']

    def test_cadena_completa_verifica(self, app, datos_base):
        from constancias.cadena import verificar_cadena
        from db import get_db
        with app.app_context():
            for _ in range(5):
                emitir(datos_base)
            with get_db() as conn:
                ok, n, problemas = verificar_cadena(conn)
        assert ok, problemas
        assert n == 5

    def test_hash_cambia_si_cambia_cualquier_campo(self, app, datos_base):
        with app.app_context():
            c = emitir(datos_base)
        alterada = dict(c, cantidad_m3_cent=c['cantidad_m3_cent'] + 1)
        assert hash_constancia(alterada) != c['hash_actual']


class TestFolio:

    def test_formato_y_correlativo(self, app, datos_base):
        with app.app_context():
            a = emitir(datos_base)
            b = emitir(datos_base)
        anio = a['anio']
        assert a['folio'] == f'RT-{anio}-00001'
        assert b['folio'] == f'RT-{anio}-00002'

    def test_codigo_no_se_parece_al_folio(self, app, datos_base):
        """El código NO se deriva del folio: si fuera predecible, cualquiera
        podría iterar y enumerar la cartera completa de clientes."""
        with app.app_context():
            codigos = [emitir(datos_base)['codigo_verificacion'] for _ in range(10)]
        assert len(set(codigos)) == 10
        for cod in codigos:
            assert '00001' not in cod
            assert not cod.startswith('RT')


class TestDatosInexistentes:

    @pytest.mark.parametrize('campo', [
        'cliente_id', 'direccion_retiro_id', 'tipo_material_id', 'destino_id',
    ])
    def test_referencia_a_id_inexistente(self, app, datos_base, campo):
        datos_base[campo] = 9999
        with app.app_context():
            with pytest.raises((DatosInvalidos, sqlite3.IntegrityError)):
                emitir(datos_base)


# --- utilidades -------------------------------------------------------

def with_conn_update(constancia, campo, valor):
    """UPDATE crudo sobre una constancia, saltándose toda la aplicación."""
    from db import get_db
    with get_db() as conn:
        conn.execute(f'UPDATE constancia SET {campo} = ? WHERE id = ?',
                     (valor, constancia['id']))
