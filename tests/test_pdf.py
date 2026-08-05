"""Generación de PDF.

Existe por una regresión real: el refactor del Hito 0.5 quitó `from pathlib
import Path` de admin.py, `_logo_data_uri()` reventó con NameError y el PDF pasó
a devolver 500. La suite no lo detectó porque no había ningún test que llegara
a generar un documento.

Marcados `pdf` porque WeasyPrint necesita pango/cairo; en macOS vienen de
Homebrew y en CI puede no estar. Se corren siempre dentro del contenedor:
    docker run --rm -v "$PWD":/app -w /app mds-local python -m pytest -m pdf
"""
import pytest

pytest.importorskip('weasyprint')

pytestmark = pytest.mark.pdf


@pytest.fixture
def sesion(cliente_http, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-pass')
    cliente_http.post('/admin/login', data={'password': 'test-pass'})
    return cliente_http


@pytest.fixture
def cotizacion(sesion):
    r = sesion.post('/admin/cotizar', data={
        'client_name': 'Cliente de Prueba SPA',
        'client_rut': '77.779.818-9',
        'origin': 'Puerto Montt',
        'destination': 'Osorno',
        'item_desc': 'Retiro RESPEL',
        'item_qty': '2',
        'item_unit': 'viaje',
        'item_price': '250000',
        'iva_applies': 'on',
    }, follow_redirects=False)
    assert r.status_code == 302, 'no se creó la cotización'
    return 1


def test_pdf_se_genera(sesion, cotizacion):
    """El caso que la regresión rompió: la ruta completa hasta los bytes."""
    r = sesion.get(f'/admin/cotizaciones/{cotizacion}/pdf')
    assert r.status_code == 200
    assert r.mimetype == 'application/pdf'
    assert r.data[:5] == b'%PDF-', 'la respuesta no es un PDF'
    assert len(r.data) > 10_000, 'PDF sospechosamente pequeño'


def test_pdf_lleva_el_logo_embebido(sesion, cotizacion):
    """_logo_data_uri() es justo lo que falló: se comprueba que sí aporta bytes."""
    from admin import _logo_data_uri

    with sesion.application.app_context():
        _logo_data_uri.cache_clear()
        uri = _logo_data_uri()
    assert uri.startswith('data:image/png;base64,')
    assert len(uri) > 1_000


def test_pdf_nombre_de_descarga(sesion, cotizacion):
    r = sesion.get(f'/admin/cotizaciones/{cotizacion}/pdf')
    assert 'COT-' in r.headers['Content-Disposition']
    assert '.pdf' in r.headers['Content-Disposition']


def test_pdf_exige_sesion(cliente_http):
    r = cliente_http.get('/admin/cotizaciones/1/pdf')
    assert r.status_code == 302
    assert '/admin/login' in r.headers['Location']


def test_pdf_de_cotizacion_inexistente(sesion):
    assert sesion.get('/admin/cotizaciones/9999/pdf').status_code == 404


@pytest.mark.parametrize('nombre', [
    'Path',             # el que se borró y rompió el PDF
    'lru_cache',
    'get_db',           # ahora viene de db.py
    'login_required',   # ahora viene de auth.py
    'destino_seguro',
    'HTML',             # WeasyPrint
])
def test_admin_tiene_sus_imports(nombre):
    """Los nombres que el refactor del Hito 0.5 movió o pudo perder."""
    import admin
    assert nombre in vars(admin), f'falta {nombre} en admin.py'


# --- PDF de constancia ------------------------------------------------

def test_constancia_cabe_en_una_pagina(app, datos_base, semilla):
    """Caso extremo: todos los campos largos a la vez.

    El documento tiene que caber en A4 sí o sí. Si crece a dos páginas, el QR
    y el código quedan en una hoja distinta a los datos y el papel deja de
    servir en terreno.
    """
    from constancias.pdf import n_paginas
    from constancias.repositorio import emitir

    datos_base.update(
        metodo_medicion='contenedor', n_contenedores=99, tipo_contenedor_id=1,
        n_viajes=99, cantidad_m3_cent=9_999_999, peso_kg_cent=9_999_999,
        comprobante_destino='COMPROBANTE-MUY-LARGO-' + 'X' * 200,
        receptor_nombre='María Fernanda de las Mercedes González Etchevarría',
        receptor_cargo='Subgerenta de Operaciones y Medio Ambiente Zona Sur',
        observaciones='Observación interna muy larga. ' * 50,
    )
    c = emitir(datos_base)
    empresa = {'nombre': 'Transportes Mar del Sur SPA', 'rut': '77.779.818-9',
               'direccion': 'Puerto Montt, Los Lagos',
               'resolucion': 'Resolución Sanitaria N° 2510389969'}
    assert n_paginas(c, empresa) == 1


def test_constancia_pdf_lleva_codigo_y_leyenda(app, constancia):
    """El código va en TEXTO, no solo en el QR: mucha gente lo teclea."""
    from constancias.dominio import formatear_codigo
    from constancias.pdf import render_html

    empresa = {'nombre': 'Transportes Mar del Sur SPA', 'rut': '77.779.818-9',
               'direccion': 'Puerto Montt', 'resolucion': 'Res. 2510389969'}
    html = render_html(constancia, empresa)

    assert formatear_codigo(constancia['codigo_verificacion']) in html
    assert 'no constituye certificación' in html.lower()
    assert 'data:image/png;base64,' in html          # el QR va embebido
    for prohibida in ['entidad certificadora', 'organismo acreditado',
                      'certificación oficial']:
        assert prohibida not in html.lower()


def test_constancia_pdf_no_incluye_observaciones(app, datos_base, semilla):
    """Son notas internas y además harían crecer el documento."""
    from constancias.pdf import render_html
    from constancias.repositorio import emitir

    datos_base['observaciones'] = 'SECRETO-INTERNO-QUE-NO-VA-AL-PDF'
    c = emitir(datos_base)
    empresa = {'nombre': 'X', 'rut': 'Y', 'direccion': 'Z', 'resolucion': 'W'}
    assert 'SECRETO-INTERNO-QUE-NO-VA-AL-PDF' not in render_html(c, empresa)
