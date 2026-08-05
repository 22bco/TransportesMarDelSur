"""Generación del PDF de la constancia, con QR de verificación.

El PDF no se guarda en disco: la fila es inmutable, así que se puede regenerar
idéntico cuando haga falta. El hash cubre los datos, no el archivo.
"""
import io
import os
from functools import lru_cache
from pathlib import Path

from flask import current_app, render_template, send_file

from .dominio import fmt_m3_es, formatear_codigo, ultimos_4_rut


ETIQUETA_METODO = {
    'contenedor': 'Conteo de contenedores',
    'pesaje': 'Pesaje en romana',
    'estimacion_visual': 'Estimación visual',
}

ETIQUETA_OPERACION = {
    'disposicion_final': 'Disposición final',
    'reciclaje': 'Reciclaje',
    'valorizacion': 'Valorización',
}

# Texto legal. Vive aquí como constante y no suelto en la plantilla para que
# un cambio quede en el diff de un .py y lo pille el test de terminología.
# NUNCA decir "certificado por", "entidad certificadora" ni "organismo
# acreditado": el documento acredita su emisión e integridad, nada más.
LEYENDA_LEGAL = (
    'Documento emitido por {emisor}. Su autenticidad puede verificarse en línea. '
    'Este documento acredita su emisión e integridad; no constituye certificación '
    'por un organismo externo ni acredita por sí solo la realización del servicio.'
)


@lru_cache(maxsize=1)
def _logo_data_uri():
    ruta = Path(current_app.root_path) / 'static' / 'img' / 'logo.png'
    if not ruta.exists():
        return ''
    import base64
    return 'data:image/png;base64,' + base64.b64encode(ruta.read_bytes()).decode('ascii')


@lru_cache(maxsize=256)
def _qr_data_uri(url: str) -> str:
    """QR como PNG embebido.

    segno es Python puro y no arrastra Pillow, que en python:3.11-slim
    significaría compilar y sumar decenas de MB a la imagen.

    Corrección de errores 'M' (~15%): el papel va a terreno, se moja, se dobla
    y se imprime con tóner gastado.
    """
    import segno
    return segno.make(url, error='m').png_data_uri(scale=4, border=2, dark='#000000')


def url_verificacion(codigo: str) -> str:
    base = os.environ.get('BASE_URL_PUBLICA', '').rstrip('/')
    return f'{base}/verificar/{codigo}'


def contexto_pdf(c: dict, empresa: dict) -> dict:
    """Todo lo que la plantilla necesita, ya formateado.

    Las observaciones NO se incluyen a propósito: son notas internas y el
    documento debe caber en una página.
    """
    return {
        'c': c,
        'empresa': empresa,
        'logo_uri': _logo_data_uri(),
        'qr_uri': _qr_data_uri(url_verificacion(c['codigo_verificacion'])),
        'codigo_legible': formatear_codigo(c['codigo_verificacion']),
        'url_verificar': url_verificacion(c['codigo_verificacion']),
        'cantidad': fmt_m3_es(c['cantidad_m3_cent']),
        'peso': fmt_m3_es(c['peso_kg_cent']) if c.get('peso_kg_cent') else None,
        'metodo': ETIQUETA_METODO.get(c['metodo_medicion'], c['metodo_medicion']),
        'operacion': ETIQUETA_OPERACION.get(c['snap_destino_operacion'],
                                            c['snap_destino_operacion']),
        'pista_rut': ultimos_4_rut(c['snap_cliente_rut']),
        'leyenda_legal': LEYENDA_LEGAL.format(emisor=empresa['nombre']),
        # Truncado explícito: un comprobante largo desbordaría la caja.
        'comprobante': (c.get('comprobante_destino') or '')[:80],
    }


def render_html(c: dict, empresa: dict) -> str:
    return render_template('admin/constancia_pdf.html', **contexto_pdf(c, empresa))


def generar_pdf(c: dict, empresa: dict) -> bytes:
    from weasyprint import HTML
    return HTML(string=render_html(c, empresa)).write_pdf()


def n_paginas(c: dict, empresa: dict) -> int:
    """Cuenta páginas sin escribir el archivo. Lo usa el test de una página."""
    from weasyprint import HTML
    return len(HTML(string=render_html(c, empresa)).render().pages)


def respuesta_pdf(c: dict, empresa: dict):
    return send_file(
        io.BytesIO(generar_pdf(c, empresa)),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{c['folio']}.pdf",
    )
