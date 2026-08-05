"""Anclaje diario: publica el último hash del día en un canal externo.

QUÉ PRUEBA Y QUÉ NO
La cadena de hash demuestra que las constancias no se alteraron *entre sí*.
Pero alguien con acceso a la base podría recalcular la cadena completa. Lo que
lo impide es que el hash del día ya haya salido a un lugar fuera de nuestro
control: un buzón de correo de un tercero, con sus propias cabeceras Received.

La firma DKIM la ponemos nosotros y NO prueba la fecha. Lo que da fecha cierta
es que el mensaje esté en el servidor de un tercero (Gmail), no el nuestro. Es
fecha cierta débil-media y cuesta cero. Por eso los destinatarios deben ser
externos, y preferentemente más de uno.

Se ejecuta DENTRO del contenedor porque escribe en la base:
    docker exec -u appuser mardelsur_web python -m constancias.anclaje

Si corriera como root en el host dejaría archivos -wal y -shm de root sobre una
base de uid 1000, y el contenedor perdería la escritura en silencio.
"""
import json
import os
import smtplib
import sys
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

from db import get_db
from .cadena import verificar_anclajes, verificar_cadena
from .dominio import TZ_CHILE, ahora_utc, iso_utc


DIR_ANCLAJES = Path(os.environ.get('ANCLAJES_DIR', '/app/data/anclajes'))
ARCHIVO_JSONL = DIR_ANCLAJES / 'anclajes.jsonl'
RETENCION_CONSULTAS_DIAS = 180
DISCO_MINIMO_GB = 5


def log(mensaje):
    linea = f"{ahora_utc().astimezone(TZ_CHILE):%Y-%m-%d %H:%M:%S}  {mensaje}"
    print(linea)


def fecha_objetivo():
    """Ayer, en hora de Chile: el día que ya cerró."""
    return (ahora_utc().astimezone(TZ_CHILE) - timedelta(days=1)).strftime('%Y-%m-%d')


def _ultima_del_dia(conn, fecha):
    return conn.execute("""
        SELECT id, folio, hash_actual FROM constancia
         WHERE emitida_fecha_local = ?
         ORDER BY id DESC LIMIT 1
    """, (fecha,)).fetchone()


def _ultima_global(conn):
    return conn.execute("""
        SELECT id, folio, hash_actual FROM constancia ORDER BY id DESC LIMIT 1
    """).fetchone()


def construir_linea(fecha, fila, n_dia, n_total):
    """Línea de anclaje: una sola línea, formato estable y legible por máquina."""
    return {
        'v': 1,
        'fecha': fecha,
        'emisor': os.environ.get('EMPRESA_RUT', '77.779.818-9').replace('.', ''),
        'folio': fila['folio'] if fila else None,
        'hash': fila['hash_actual'] if fila else None,
        'n_dia': n_dia,
        'n_total': n_total,
        'algo': 'sha256',
    }


def enviar_correo(asunto, cuerpo):
    """Envía por el docker-mailserver del propio VPS.

    Se conecta a mail.transportesmardelsur.cl y NO al nombre del contenedor:
    el certificado de Let's Encrypt está emitido para ese hostname, así que
    starttls() puede validarlo sin desactivar la verificación.
    """
    host = os.environ.get('ANCLAJE_SMTP_HOST', '')
    usuario = os.environ.get('ANCLAJE_SMTP_USER', '')
    clave = os.environ.get('ANCLAJE_SMTP_PASS', '')
    destinos = [d.strip() for d in
                os.environ.get('ANCLAJE_DESTINATARIOS', '').split(',') if d.strip()]

    if not (host and usuario and clave and destinos):
        log('SMTP no configurado: el anclaje queda solo en el archivo local. '
            'Sin un destinatario externo NO hay fecha cierta ante terceros.')
        return False

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = usuario
    msg['To'] = ', '.join(destinos)
    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP(host, int(os.environ.get('ANCLAJE_SMTP_PORT', '587')),
                          timeout=30) as smtp:
            smtp.starttls()
            smtp.login(usuario, clave)
            smtp.send_message(msg)
        log(f'Anclaje enviado a {len(destinos)} destinatario(s).')
        return True
    except Exception as e:
        log(f'ERROR al enviar el correo de anclaje: {e}')
        return False


def _alerta_disco():
    try:
        import shutil as _sh
        libres_gb = _sh.disk_usage('/app/data').free / 1024**3
        if libres_gb < DISCO_MINIMO_GB:
            return f'ATENCIÓN: quedan {libres_gb:.1f} GB libres en el servidor.'
    except OSError:
        pass
    return None


def ejecutar():
    """Devuelve 0 si todo está bien, 2 si la cadena está rota."""
    fecha = fecha_objetivo()
    log(f'Anclaje del {fecha}')

    with get_db() as conn:
        ok_cadena, n_verificadas, problemas = verificar_cadena(conn)
        ok_anclajes, n_anclajes, problemas_anclaje = verificar_anclajes(conn)

        if not (ok_cadena and ok_anclajes):
            detalle = '\n'.join(problemas + problemas_anclaje)
            log('CADENA ROTA:\n' + detalle)
            enviar_correo(
                '[ALERTA] Cadena de integridad rota — Mar del Sur',
                'La verificación diaria encontró problemas en la cadena de '
                'constancias.\n\n' + detalle +
                '\n\nRevise el respaldo del día antes de tocar nada.')
            return 2

        n_total = conn.execute('SELECT COUNT(*) AS n FROM constancia').fetchone()['n']
        n_dia = conn.execute("""
            SELECT COUNT(*) AS n FROM constancia WHERE emitida_fecha_local = ?
        """, (fecha,)).fetchone()['n']

        # Si el día no tuvo emisiones se ancla igual el último eslabón global,
        # para que la serie de anclajes no tenga huecos.
        fila = _ultima_del_dia(conn, fecha) or _ultima_global(conn)
        if not fila:
            log('Todavía no hay constancias emitidas: nada que anclar.')
            return 0

        linea = construir_linea(fecha, fila, n_dia, n_total)

        # Idempotente: se puede reejecutar el mismo día sin ensuciar.
        conn.execute("""
            INSERT OR IGNORE INTO anclaje_diario
                   (fecha, constancia_id, folio, hash_actual, n_constancias_dia,
                    n_constancias_total, medio, enviado_ok, creado_at)
            VALUES (?,?,?,?,?,?, 'ambos', 0, ?)
        """, (fecha, fila['id'], fila['folio'], fila['hash_actual'],
              n_dia, n_total, iso_utc(ahora_utc())))

        borradas = conn.execute("""
            DELETE FROM consulta_verificacion
             WHERE consultado_at_epoch < ?
        """, (int(ahora_utc().timestamp()) - RETENCION_CONSULTAS_DIAS * 86400,)).rowcount

    if borradas:
        log(f'Purgadas {borradas} consultas de verificación de más de '
            f'{RETENCION_CONSULTAS_DIAS} días.')

    DIR_ANCLAJES.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO_JSONL, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(linea, ensure_ascii=False) + '\n')

    aviso = _alerta_disco()
    cuerpo = (
        f"Registro diario de integridad — Transportes Mar del Sur SPA\n"
        f"{'=' * 60}\n\n"
        f"Fecha anclada:        {fecha}\n"
        f"Último folio del día: {linea['folio']}\n"
        f"Hash:                 {linea['hash']}\n"
        f"Emitidas ese día:     {n_dia}\n"
        f"Total acumulado:      {n_total}\n"
        f"Cadena verificada:    {n_verificadas} eslabón(es), sin problemas\n"
        f"Anclajes cotejados:   {n_anclajes}\n\n"
        f"{aviso + chr(10) + chr(10) if aviso else ''}"
        f"Línea de anclaje (formato estable):\n"
        f"{json.dumps(linea, ensure_ascii=False)}\n\n"
        f"Conserve este correo. Su fecha de recepción, registrada por el "
        f"proveedor de correo, es lo que permite demostrar ante un tercero que "
        f"las constancias existían con este contenido a esa fecha.\n"
    )
    enviado = enviar_correo(
        f'Registro de integridad {fecha} — Mar del Sur', cuerpo)

    with get_db() as conn:
        conn.execute('UPDATE anclaje_diario SET enviado_ok = ? WHERE fecha = ?',
                     (1 if enviado else 0, fecha))

    log(f"OK: {n_verificadas} eslabón(es) verificado(s), anclado {linea['folio']}")
    return 0


def main():
    sys.path.insert(0, '/app')
    from app import crear_app
    app = crear_app()
    with app.app_context():
        return ejecutar()


if __name__ == '__main__':
    sys.exit(main())
