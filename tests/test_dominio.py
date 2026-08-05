"""Código de verificación, cantidades y manejo de fechas."""
from datetime import datetime, timezone

import pytest

from constancias.dominio import (
    ALFABETO_CODIGO, LARGO_CODIGO, CantidadInvalida,
    a_centesimas, fecha_local, fmt_m3, fmt_m3_es, formatear_codigo,
    generar_codigo, iso_utc, nfc, normalizar_codigo,
)


class TestCodigoVerificacion:

    def test_alfabeto_sin_caracteres_ambiguos(self):
        for ambiguo in '0O1IL':
            assert ambiguo not in ALFABETO_CODIGO
        assert len(ALFABETO_CODIGO) == 31

    def test_forma_y_unicidad(self):
        """10.000 códigos: todos válidos y ninguno repetido."""
        codigos = {generar_codigo() for _ in range(10_000)}
        assert len(codigos) == 10_000, 'colisión en 10k códigos'
        for c in codigos:
            assert len(c) == LARGO_CODIGO
            assert set(c) <= set(ALFABETO_CODIGO)

    def test_usa_todo_el_alfabeto(self):
        """Detecta un generador sesgado que solo emita parte del alfabeto."""
        vistos = set(''.join(generar_codigo() for _ in range(2_000)))
        assert vistos == set(ALFABETO_CODIGO)

    def test_formateo_para_humanos(self):
        assert formatear_codigo('K7M29XQP4RTF') == 'K7M2-9XQP-4RTF'

    @pytest.mark.parametrize('entrada', [
        'K7M29XQP4RTF',
        'k7m2-9xqp-4rtf',
        'K7M2 9XQP 4RTF',
        '  K7M2-9XQP-4RTF  ',
    ])
    def test_normaliza_lo_que_teclee_el_usuario(self, entrada):
        assert normalizar_codigo(entrada) == 'K7M29XQP4RTF'

    @pytest.mark.parametrize('malo', ['', None, 'CORTO', 'K7M29XQP4RTF9', '000'])
    def test_invalidos_devuelven_vacio(self, malo):
        """Sin excepción: quien llama trata esto igual que 'no encontrada'."""
        assert normalizar_codigo(malo) == ''


class TestCantidades:
    """m3 en centésimas enteras. Un float aquí rompería el hash."""

    @pytest.mark.parametrize('entrada, esperado', [
        ('12.10', 1210),
        ('12,10', 1210),
        ('12,1', 1210),
        ('0.5', 50),
        ('3', 300),
        ('1000', 100_000),
    ])
    def test_conversion(self, entrada, esperado):
        assert a_centesimas(entrada) == esperado

    def test_redondeo_medio_hacia_arriba(self):
        assert a_centesimas('12.125') == 1213

    def test_rechaza_float(self):
        """0.1 + 0.2 no es 0.3 en binario: mejor fallar temprano y fuerte."""
        with pytest.raises(CantidadInvalida):
            a_centesimas(0.1)

    @pytest.mark.parametrize('malo', ['0', '-5', '', 'doce', None])
    def test_rechaza_invalidos(self, malo):
        with pytest.raises(CantidadInvalida):
            a_centesimas(malo)

    def test_formato_canonico_vs_pantalla(self):
        assert fmt_m3(1210) == '12.10'      # al hash
        assert fmt_m3_es(1210) == '12,10'   # a la pantalla
        assert fmt_m3(50) == '0.50'
        assert fmt_m3(100_000) == '1000.00'

    def test_ida_y_vuelta(self):
        for texto in ['12.10', '0.05', '999.99']:
            assert fmt_m3(a_centesimas(texto)) == texto


class TestTiempo:

    def test_iso_utc_largo_fijo(self):
        """Largo fijo 20: entra al hash y va con CHECK en la base."""
        s = iso_utc(datetime(2026, 8, 5, 14, 3, 27, tzinfo=timezone.utc))
        assert s == '2026-08-05T14:03:27Z'
        assert len(s) == 20

    def test_fecha_local_no_es_la_utc_cerca_de_medianoche(self):
        """23:50 en Chile ya es el día siguiente en UTC.

        Por eso se guarda la fecha civil chilena aparte: comparar la fecha de
        retiro contra un instante UTC daría un día de más al cerrar la jornada.
        """
        momento = datetime(2026, 8, 6, 3, 50, tzinfo=timezone.utc)
        assert fecha_local(momento) == '2026-08-05'
        assert iso_utc(momento)[:10] == '2026-08-06'

    def test_orden_lexicografico_se_respeta(self):
        """Guardar en UTC mantiene el orden como texto pese al cambio de hora."""
        verano = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)   # Chile -03
        invierno = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)  # Chile -04
        assert iso_utc(verano) < iso_utc(invierno)


class TestTexto:

    def test_nfc_unifica_acentos(self):
        precompuesto = 'Petróleo'
        combinante = 'Petróleo'
        assert precompuesto != combinante
        assert nfc(precompuesto) == nfc(combinante)
