"""Validación de RUT chileno por módulo 11.

Los dígitos verificadores de estos casos NO están inventados: se contrastaron
contra una implementación independiente (suma directa, sin ciclo de factores) y
contra un RUT real conocido, el de la propia empresa, 77.779.818-9.
"""
import pytest

from constancias.dominio import (
    RutInvalido, calcular_dv, normalizar_rut, ultimos_4_rut,
)


def dv_referencia(cuerpo: str) -> str:
    """Segunda implementación, deliberadamente distinta a la de producción.

    Si ambas coinciden en un barrido amplio, el error tendría que estar en las
    dos a la vez y de la misma forma.
    """
    suma = sum(int(d) * (2 + i % 6) for i, d in enumerate(reversed(cuerpo)))
    resto = 11 - suma % 11
    return '0' if resto == 11 else 'K' if resto == 10 else str(resto)


class TestCalcularDv:

    def test_rut_real_de_la_empresa(self):
        """Anclaje contra la realidad: 77.779.818-9 está en la resolución sanitaria."""
        assert calcular_dv('77779818') == '9'

    @pytest.mark.parametrize('cuerpo, esperado', [
        ('76123456', '0'),
        ('12345678', '5'),
        ('11111111', '1'),
        ('22222222', '2'),
        ('7123456', '8'),
    ])
    def test_dv_numerico(self, cuerpo, esperado):
        assert calcular_dv(cuerpo) == esperado

    def test_dv_k(self):
        """resto == 10 -> 'K'. El caso que rompe las implementaciones caseras."""
        assert calcular_dv('12345670') == 'K'
        assert calcular_dv('10000013') == 'K'

    def test_dv_cero(self):
        """resto == 11 -> '0', que no es lo mismo que K."""
        assert calcular_dv('10000004') == '0'
        assert calcular_dv('76123456') == '0'

    def test_coincide_con_implementacion_independiente(self):
        """Barrido de ~3.700 cuerpos contra la implementación de referencia."""
        for n in range(1_000_000, 30_000_000, 7919):
            cuerpo = str(n)
            assert calcular_dv(cuerpo) == dv_referencia(cuerpo), cuerpo

    def test_ambos_casos_especiales_ocurren_de_verdad(self):
        dvs = {calcular_dv(str(n)) for n in range(10_000_000, 10_001_000)}
        assert 'K' in dvs
        assert '0' in dvs


class TestNormalizarRut:
    """Todo RUT entra normalizado: sin puntos, con guion, en mayúscula."""

    @pytest.mark.parametrize('entrada', [
        '76.123.456-0',
        '76123456-0',
        '761234560',
        ' 76.123.456 - 0 ',
        '76,123,456-0',
    ])
    def test_formatos_equivalentes(self, entrada):
        assert normalizar_rut(entrada) == '76123456-0'

    def test_k_minuscula_se_normaliza(self):
        assert normalizar_rut('12345670-k') == '12345670-K'
        assert normalizar_rut('12.345.670-k') == '12345670-K'

    def test_ceros_a_la_izquierda(self):
        """'07123456-8' y '7123456-8' son el mismo RUT, no dos clientes."""
        assert normalizar_rut('07123456-8') == normalizar_rut('7123456-8') == '7123456-8'

    def test_guion_largo_copiado_de_word(self):
        assert normalizar_rut('76.123.456–0') == '76123456-0'

    @pytest.mark.parametrize('malo', [
        '76123456-8',        # DV incorrecto (el correcto es 0)
        '76123456-K',        # K donde no corresponde
        '12345670-0',        # su DV es K, no 0
        '7',                 # demasiado corto
        '',
        None,
        'sin-numeros',
        '1234567890123-5',   # fuera de rango
    ])
    def test_rechaza_invalidos(self, malo):
        with pytest.raises(RutInvalido):
            normalizar_rut(malo)

    def test_ida_y_vuelta(self):
        rut = normalizar_rut('76.123.456-0')
        assert normalizar_rut(rut) == rut


class TestUltimos4:
    """Segundo factor de la verificación pública."""

    def test_quita_el_guion(self):
        assert ultimos_4_rut('76123456-0') == '4560'

    def test_con_dv_k(self):
        """El 'segundo factor' puede contener una letra: no asumir 4 dígitos."""
        assert ultimos_4_rut('12345670-K') == '670K'

    def test_rut_corto(self):
        assert ultimos_4_rut('7123456-8') == '4568'
