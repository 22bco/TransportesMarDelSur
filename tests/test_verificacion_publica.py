"""Página pública /verificar: RN-10, anti-enumeración y rate limit.

La página pública es la superficie expuesta del sistema. Los tests de aquí no
comprueban que funcione, sino que **no filtre**.
"""
import pytest

from constancias.dominio import formatear_codigo, ultimos_4_rut
from constancias.rutas_publicas import (
    CAMPOS_PUBLICOS, CAMPOS_PUBLICOS_CONDICIONALES, payload_publico,
)


# Valores centinela: si alguno aparece en la respuesta pública, hay una fuga.
CENTINELAS = {
    'receptor_nombre': 'RECEPTOR-SECRETO-XYZ',
    'receptor_cargo': 'CARGO-SECRETO-XYZ',
    'observaciones': 'OBSERVACION-INTERNA-XYZ',
    'comprobante_destino': 'COMPROBANTE-SECRETO-XYZ',
}


@pytest.fixture
def constancia_marcada(app, datos_base):
    """Constancia con centinelas en todos los campos reservados."""
    from constancias.repositorio import emitir
    datos_base.update(CENTINELAS)
    # Coordenadas deliberadamente absurdas: las de Puerto Montt (-41.4693,
    # -72.9424) ya están publicadas en los geo meta tags de base.html como
    # dirección de la empresa, así que no sirven de centinela.
    datos_base.update(lat=-11.1111, lng=-33.3333, receptor_rut='7123456-8')
    return emitir(datos_base)


def _verificar(cliente_http, codigo, rut4):
    return cliente_http.post('/verificar', data={'codigo': codigo, 'rut4': rut4})


def _sin_eco(respuesta):
    """Respuesta sin el eco del código que tecleó el usuario.

    El formulario re-muestra lo escrito para que quien se equivocó solo en el
    RUT no tenga que teclear otra vez los 12 caracteres. Eso hace que dos
    respuestas de error difieran en bytes, pero NO es un oráculo: el atacante
    ya sabe qué envió. Lo que debe ser idéntico es todo lo demás.
    """
    import re
    return re.sub(rb'value="[^"]*"', b'value=""', respuesta.get_data())


class TestRN10CamposReservados:

    def test_whitelist_congelada(self, constancia_marcada):
        """El payload no puede ganar campos sin que alguien lo note."""
        claves = set(payload_publico(constancia_marcada))
        assert claves <= (CAMPOS_PUBLICOS | CAMPOS_PUBLICOS_CONDICIONALES)
        assert CAMPOS_PUBLICOS <= claves

    @pytest.mark.parametrize('campo', sorted(CENTINELAS))
    def test_centinelas_no_estan_en_el_payload(self, constancia_marcada, campo):
        valores = ' '.join(str(v) for v in payload_publico(constancia_marcada).values())
        assert CENTINELAS[campo] not in valores

    @pytest.mark.parametrize('campo', sorted(CENTINELAS))
    def test_centinelas_no_estan_en_el_HTML(self, cliente_http, constancia_marcada, campo):
        """Ni siquiera dentro de un comentario o de un atributo."""
        r = _verificar(cliente_http, constancia_marcada['codigo_verificacion'],
                       ultimos_4_rut(constancia_marcada['snap_cliente_rut']))
        assert r.status_code == 200
        assert CENTINELAS[campo] not in r.get_data(as_text=True)

    def test_coordenadas_no_se_publican(self, cliente_http, constancia_marcada):
        r = _verificar(cliente_http, constancia_marcada['codigo_verificacion'],
                       ultimos_4_rut(constancia_marcada['snap_cliente_rut']))
        cuerpo = r.get_data(as_text=True)
        assert '-11.1111' not in cuerpo
        assert '-33.3333' not in cuerpo

    def test_hashes_no_se_publican(self, cliente_http, constancia_marcada):
        r = _verificar(cliente_http, constancia_marcada['codigo_verificacion'],
                       ultimos_4_rut(constancia_marcada['snap_cliente_rut']))
        cuerpo = r.get_data(as_text=True)
        assert constancia_marcada['hash_actual'] not in cuerpo
        assert constancia_marcada['hash_anterior'] not in cuerpo

    def test_direccion_exacta_no_se_publica(self, cliente_http, constancia_marcada):
        """Solo la comuna. La calle es información del cliente."""
        r = _verificar(cliente_http, constancia_marcada['codigo_verificacion'],
                       ultimos_4_rut(constancia_marcada['snap_cliente_rut']))
        cuerpo = r.get_data(as_text=True)
        assert 'Camino Chinquihue' not in cuerpo
        assert 'Puerto Montt' in cuerpo   # la comuna sí


class TestVerificacionCorrecta:

    def test_codigo_y_rut_correctos(self, cliente_http, constancia):
        r = _verificar(cliente_http, constancia['codigo_verificacion'],
                       ultimos_4_rut(constancia['snap_cliente_rut']))
        cuerpo = r.get_data(as_text=True)
        assert r.status_code == 200
        assert constancia['folio'] in cuerpo
        assert 'Documento verificado' in cuerpo

    def test_acepta_el_codigo_con_guiones(self, cliente_http, constancia):
        """Tal como está impreso en el PDF."""
        r = _verificar(cliente_http,
                       formatear_codigo(constancia['codigo_verificacion']),
                       ultimos_4_rut(constancia['snap_cliente_rut']))
        assert constancia['folio'] in r.get_data(as_text=True)

    def test_acepta_minusculas(self, cliente_http, constancia):
        r = _verificar(cliente_http, constancia['codigo_verificacion'].lower(),
                       ultimos_4_rut(constancia['snap_cliente_rut']))
        assert constancia['folio'] in r.get_data(as_text=True)

    def test_el_qr_prellena_pero_pide_el_rut(self, cliente_http, constancia):
        """GET /verificar/<codigo> no revela si el código existe."""
        r = cliente_http.get(f"/verificar/{constancia['codigo_verificacion']}")
        cuerpo = r.get_data(as_text=True)
        assert r.status_code == 200
        assert constancia['codigo_verificacion'] in cuerpo   # prellenado
        assert constancia['folio'] not in cuerpo             # pero sin datos


class TestAntiEnumeracion:

    def test_codigo_inexistente_y_rut_malo_dan_lo_mismo(self, cliente_http, constancia):
        """La defensa central: no se puede usar la respuesta como oráculo.

        Si "código no existe" y "RUT no coincide" se distinguieran, un atacante
        podría separar los dos factores y atacarlos por separado.
        """
        no_existe = _verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999')
        rut_malo = _verificar(cliente_http, constancia['codigo_verificacion'], '9999')

        assert no_existe.status_code == rut_malo.status_code == 200
        assert _sin_eco(no_existe) == _sin_eco(rut_malo)

    def test_formato_invalido_da_la_misma_respuesta(self, cliente_http, constancia):
        corto = _verificar(cliente_http, 'ABC', '9999')
        no_existe = _verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999')
        assert _sin_eco(corto) == _sin_eco(no_existe)

    def test_el_motivo_real_no_viaja_al_cliente(self, cliente_http, constancia):
        for cuerpo in (_verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999'),
                       _verificar(cliente_http, constancia['codigo_verificacion'], '0000')):
            texto = cuerpo.get_data(as_text=True)
            assert 'no_existe' not in texto
            assert 'rut_no_coincide' not in texto

    def test_el_motivo_real_sí_queda_registrado(self, cliente_http, constancia, conn):
        """Para forense: se guarda, pero no se responde."""
        _verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999')
        _verificar(cliente_http, constancia['codigo_verificacion'], '0000')
        motivos = [f['motivo'] for f in
                   conn.execute('SELECT motivo FROM consulta_verificacion ORDER BY id')]
        assert 'no_existe' in motivos
        assert 'rut_no_coincide' in motivos


class TestAuditoria:

    def test_toda_consulta_queda_registrada(self, cliente_http, constancia, conn):
        _verificar(cliente_http, constancia['codigo_verificacion'],
                   ultimos_4_rut(constancia['snap_cliente_rut']))
        fila = conn.execute("""
            SELECT * FROM consulta_verificacion ORDER BY id DESC LIMIT 1
        """).fetchone()
        assert fila['exito'] == 1
        assert fila['motivo'] == 'ok'
        assert fila['ip']

    def test_la_ip_se_toma_de_la_cabecera_de_cloudflare(self, cliente_http, constancia, conn):
        cliente_http.post('/verificar',
                          data={'codigo': 'ZZZZZZZZZZZZ', 'rut4': '9999'},
                          headers={'CF-Connecting-IP': '186.104.54.103'})
        fila = conn.execute(
            'SELECT ip FROM consulta_verificacion ORDER BY id DESC LIMIT 1').fetchone()
        assert fila['ip'] == '186.104.54.103'


class TestRateLimit:

    def test_el_intento_11_es_rechazado(self, cliente_http, constancia):
        for _ in range(10):
            _verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999')
        r = _verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999')
        assert r.status_code == 429
        assert 'Demasiados intentos' in r.get_data(as_text=True)

    def test_el_limite_aplica_aunque_el_codigo_sea_correcto(self, cliente_http, constancia):
        """Si no, se podría agotar el límite con fallos y seguir con aciertos."""
        for _ in range(10):
            _verificar(cliente_http, 'ZZZZZZZZZZZZ', '9999')
        r = _verificar(cliente_http, constancia['codigo_verificacion'],
                       ultimos_4_rut(constancia['snap_cliente_rut']))
        assert r.status_code == 429

    def test_el_limite_es_por_ip(self, cliente_http, constancia):
        for _ in range(10):
            cliente_http.post('/verificar', data={'codigo': 'ZZZZZZZZZZZZ', 'rut4': '9'},
                              headers={'CF-Connecting-IP': '1.1.1.1'})
        otra = cliente_http.post('/verificar',
                                 data={'codigo': constancia['codigo_verificacion'],
                                       'rut4': ultimos_4_rut(constancia['snap_cliente_rut'])},
                                 headers={'CF-Connecting-IP': '2.2.2.2'})
        assert otra.status_code == 200
        assert constancia['folio'] in otra.get_data(as_text=True)


class TestTerminologia:
    """El posicionamiento legal no se negocia: ver §1 del spec."""

    PROHIBIDAS = ['entidad certificadora', 'organismo acreditado',
                  'certificación oficial', 'validado por organismo']

    def test_pagina_del_formulario(self, cliente_http):
        texto = cliente_http.get('/verificar').get_data(as_text=True).lower()
        for frase in self.PROHIBIDAS:
            assert frase not in texto

    def test_pagina_de_resultado(self, cliente_http, constancia):
        r = _verificar(cliente_http, constancia['codigo_verificacion'],
                       ultimos_4_rut(constancia['snap_cliente_rut']))
        texto = r.get_data(as_text=True).lower()
        for frase in self.PROHIBIDAS:
            assert frase not in texto

    def test_incluye_el_descargo_explicito(self, cliente_http, constancia):
        r = _verificar(cliente_http, constancia['codigo_verificacion'],
                       ultimos_4_rut(constancia['snap_cliente_rut']))
        texto = r.get_data(as_text=True).lower()
        assert 'no constituye certificación' in texto

    def test_las_paginas_publicas_no_se_indexan(self, cliente_http):
        """Mientras los textos legales están en revisión."""
        texto = cliente_http.get('/verificar').get_data(as_text=True)
        assert 'noindex' in texto
