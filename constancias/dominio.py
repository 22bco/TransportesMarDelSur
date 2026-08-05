"""Reglas del dominio: RUT, código de verificación, cantidades y fechas.

Sin dependencias de Flask ni de la base de datos, a propósito: todo lo de este
módulo se puede testear sin levantar nada.
"""
import re
import secrets
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from zoneinfo import ZoneInfo


TZ_CHILE = ZoneInfo('America/Santiago')


# --- Tiempo -----------------------------------------------------------
# Única fuente de "ahora" del módulo. Nada llama a datetime.now() por su
# cuenta: así los tests inyectan el tiempo con un solo monkeypatch.

def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(momento: datetime) -> str:
    """'2026-08-05T14:03:27Z' — largo fijo 20, ordena lexicográficamente.

    Se guarda en UTC y no en hora local con offset porque Chile alterna entre
    -04:00 y -03:00: dos instantes con offsets distintos se ordenarían mal como
    texto, y este valor entra al hash de la cadena de integridad.
    """
    return momento.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def fecha_local(momento: datetime) -> str:
    """Fecha civil chilena 'YYYY-MM-DD'.

    Necesaria además del instante UTC: comparar "no se emite con fecha de
    retiro futura" contra un timestamp UTC falla hasta 4 horas cerca de
    medianoche, que es justo cuando alguien cierra el día de trabajo.
    """
    return momento.astimezone(TZ_CHILE).strftime('%Y-%m-%d')


# --- RUT chileno ------------------------------------------------------

class RutInvalido(ValueError):
    """El RUT no tiene formato válido o su dígito verificador no cuadra."""


def calcular_dv(cuerpo: str) -> str:
    """Dígito verificador por módulo 11.

    Se recorre de derecha a izquierda multiplicando por la serie cíclica
    2,3,4,5,6,7. El resto 11 da '0' y el resto 10 da 'K' — ese es el caso que
    casi todas las implementaciones caseras olvidan.
    """
    suma, factor = 0, 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    if resto == 11:
        return '0'
    if resto == 10:
        return 'K'
    return str(resto)


def normalizar_rut(texto: str) -> str:
    """'76.123.456-7' | '761234567' | '76123456k' -> '76123456-7' / '...-K'.

    Guardamos siempre sin puntos, con guion y en mayúscula. Los ceros a la
    izquierda se eliminan: si no, '07123456-1' y '7123456-1' serían dos
    clientes distintos para la base de datos siendo el mismo.
    """
    limpio = re.sub(r'[^0-9kK]', '', texto or '').upper()
    if len(limpio) < 2:
        raise RutInvalido('RUT demasiado corto.')

    cuerpo, dv = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit():
        raise RutInvalido('El cuerpo del RUT debe ser numérico.')
    if not 7 <= len(cuerpo.lstrip('0') or '0') <= 9:
        raise RutInvalido('Largo de RUT fuera de rango.')
    if dv != calcular_dv(cuerpo):
        raise RutInvalido('Dígito verificador incorrecto.')

    return f'{int(cuerpo)}-{dv}'


def ultimos_4_rut(rut_normalizado: str) -> str:
    """Segundo factor de la verificación pública: '76123456-7' -> '4567'.

    Se define una sola vez y se usa idéntica al imprimir el PDF (donde se le
    explica al cliente qué le van a pedir) y al verificar.
    """
    return rut_normalizado.replace('-', '')[-4:]


# --- Código de verificación -------------------------------------------

# 31 símbolos: se excluyen 0, O, 1, I y L porque se confunden al leerlos de un
# papel impreso. El código se teclea a mano tan a menudo como se escanea.
ALFABETO_CODIGO = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
LARGO_CODIGO = 12


def generar_codigo() -> str:
    """Código aleatorio de 12 caracteres.

    `secrets.choice` usa el CSPRNG del sistema y no introduce sesgo de módulo.
    NUNCA se deriva del folio: si fuera predecible, cualquiera podría iterar
    códigos y enumerar la cartera completa de clientes.

    Espacio de búsqueda: 31**12 ~ 7,9e17 (59,4 bits).
    """
    return ''.join(secrets.choice(ALFABETO_CODIGO) for _ in range(LARGO_CODIGO))


def formatear_codigo(codigo: str) -> str:
    """'K7M29XQP4RTF' -> 'K7M2-9XQP-4RTF', para el PDF y la pantalla."""
    return '-'.join(codigo[i:i + 4] for i in range(0, len(codigo), 4))


def normalizar_codigo(texto: str) -> str:
    """Acepta lo que sea que el usuario haya tecleado o pegado.

    Devuelve '' si no es un código válido; quien llama trata ese caso igual que
    "no encontrada", sin distinguirlo, para no filtrar información.
    """
    limpio = ''.join(c for c in (texto or '').upper() if c in ALFABETO_CODIGO)
    return limpio if len(limpio) == LARGO_CODIGO else ''


# --- Cantidades -------------------------------------------------------
# Los m3 se guardan como enteros de centésimas. SQLite degradaría un
# NUMERIC(10,2) a REAL (float binario), donde 12.10 no es exacto, y entonces el
# hash de la cadena dejaría de ser reproducible.

class CantidadInvalida(ValueError):
    """La cantidad no es un número positivo con dos decimales."""


def a_centesimas(texto) -> int:
    """'12,1' | '12.10' | Decimal('12.1') -> 1210. Nunca pasa por float."""
    if isinstance(texto, float):
        raise CantidadInvalida('No usar float para cantidades: pierde exactitud.')
    crudo = str(texto).strip().replace(',', '.')
    if not crudo:
        raise CantidadInvalida('Cantidad vacía.')
    try:
        valor = Decimal(crudo).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise CantidadInvalida(f'Cantidad no numérica: {texto!r}')
    if valor <= 0:
        raise CantidadInvalida('La cantidad debe ser mayor que cero.')
    return int(valor * 100)


def fmt_m3(centesimas: int) -> str:
    """1210 -> '12.10'. Punto decimal: es la forma canónica que entra al hash."""
    return f'{centesimas // 100}.{centesimas % 100:02d}'


def fmt_m3_es(centesimas: int) -> str:
    """1210 -> '12,10'. Coma decimal: convención chilena, solo para mostrar."""
    return fmt_m3(centesimas).replace('.', ',')


# --- Texto ------------------------------------------------------------

def nfc(texto: str) -> str:
    """Normaliza a NFC.

    'Petróleo' escrito con acento precompuesto y con acento combinante son dos
    cadenas distintas en bytes. Sin normalizar, producirían hashes distintos
    para el mismo material.
    """
    return unicodedata.normalize('NFC', texto or '')
