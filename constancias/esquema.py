"""Esquema de la base de datos de constancias: tablas, CHECK y triggers.

Principio rector: **una constancia emitida no se modifica ni se borra**.
Corregir significa anular y emitir una nueva que referencia a la anterior.

Esa invariante se hace cumplir en la BASE DE DATOS, no solo en la aplicación.
La aplicación puede tener un bug; el trigger no. Y como los triggers también
bloquean al CLI de sqlite3, una corrección de emergencia exige un DROP TRIGGER
explícito: esa fricción es deliberada.

Notas de tipos (SQLite no es PostgreSQL):
  - No hay NUMERIC: los m3 van como enteros de centésimas (`*_cent`). Un REAL
    haría que 12.10 no fuera exacto y el hash dejaría de ser reproducible.
  - No hay TIMESTAMPTZ: los instantes van en ISO-8601 UTC de largo fijo, más
    un epoch para índices y una fecha civil chilena para las reglas de negocio.
  - No hay BOOLEAN: INTEGER con CHECK (x IN (0,1)).
  - Todas las tablas son STRICT (SQLite >= 3.37): un INSERT con 'doce' en una
    columna INTEGER falla en vez de guardarse como texto.
"""
from db import get_db


GENESIS = '0' * 64


DDL = """
-- ── Catálogos ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS comuna (
  id      INTEGER PRIMARY KEY,
  nombre  TEXT NOT NULL,
  region  TEXT NOT NULL,
  UNIQUE (nombre, region)
) STRICT;

CREATE TABLE IF NOT EXISTS tipo_material (
  id     INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,
  activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
) STRICT;

CREATE TABLE IF NOT EXISTS tipo_contenedor (
  id                INTEGER PRIMARY KEY,
  nombre            TEXT NOT NULL UNIQUE,
  capacidad_m3_cent INTEGER CHECK (capacidad_m3_cent IS NULL OR capacidad_m3_cent > 0),
  activo            INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
) STRICT;

-- ── Maestros ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cliente (
  id                   INTEGER PRIMARY KEY,
  razon_social         TEXT NOT NULL,
  rut                  TEXT NOT NULL UNIQUE,   -- normalizado: 76123456-0
  giro                 TEXT,
  direccion_tributaria TEXT,
  comuna_id            INTEGER REFERENCES comuna(id),
  contacto_nombre      TEXT,
  contacto_email       TEXT,
  contacto_telefono    TEXT,
  activo               INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
  created_at           TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS direccion_retiro (
  id                INTEGER PRIMARY KEY,
  cliente_id        INTEGER NOT NULL REFERENCES cliente(id),
  nombre_referencia TEXT NOT NULL,
  calle             TEXT NOT NULL,
  numero            TEXT,
  comuna_id         INTEGER NOT NULL REFERENCES comuna(id),
  referencia        TEXT,
  lat               REAL,
  lng               REAL,
  activo            INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
) STRICT;

CREATE TABLE IF NOT EXISTS destino (
  id             INTEGER PRIMARY KEY,
  nombre         TEXT NOT NULL,
  direccion      TEXT,
  comuna_id      INTEGER NOT NULL REFERENCES comuna(id),
  tipo_operacion TEXT NOT NULL
                 CHECK (tipo_operacion IN ('disposicion_final','reciclaje','valorizacion')),
  autorizacion   TEXT,
  activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
) STRICT;

CREATE TABLE IF NOT EXISTS vehiculo (
  id                INTEGER PRIMARY KEY,
  patente           TEXT NOT NULL UNIQUE,
  tipo              TEXT,
  capacidad_m3_cent INTEGER CHECK (capacidad_m3_cent IS NULL OR capacidad_m3_cent > 0),
  activo            INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
) STRICT;

CREATE TABLE IF NOT EXISTS conductor (
  id       INTEGER PRIMARY KEY,
  nombre   TEXT NOT NULL,
  rut      TEXT UNIQUE,
  telefono TEXT,
  activo   INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1))
) STRICT;

-- ── Usuario ──────────────────────────────────────────────────────────
-- El login nominado llega en el Hito 2. La tabla se crea ya para que
-- constancia.emitida_por_usuario_id sea una clave foránea real desde el
-- principio: añadirla después obligaría a reescribir filas inmutables.
CREATE TABLE IF NOT EXISTS usuario (
  id                    INTEGER PRIMARY KEY,
  email                 TEXT NOT NULL UNIQUE COLLATE NOCASE,
  nombre                TEXT NOT NULL,
  password_hash         TEXT,
  rol                   TEXT NOT NULL DEFAULT 'operador'
                        CHECK (rol IN ('admin','operador')),
  activo                INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
  debe_cambiar_password INTEGER NOT NULL DEFAULT 0 CHECK (debe_cambiar_password IN (0,1)),
  created_at            TEXT NOT NULL,
  last_login_at         TEXT
) STRICT;

-- ── Constancia ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS constancia (
  id                    INTEGER PRIMARY KEY,
  folio                 TEXT NOT NULL UNIQUE,   -- RT-2026-00847
  anio                  INTEGER NOT NULL,
  seq                   INTEGER NOT NULL,
  codigo_verificacion   TEXT NOT NULL UNIQUE CHECK (length(codigo_verificacion) = 12),
  estado                TEXT NOT NULL DEFAULT 'vigente'
                        CHECK (estado IN ('vigente','anulada','reemplazada')),

  emitida_at            TEXT NOT NULL CHECK (length(emitida_at) = 20),
  emitida_at_epoch      INTEGER NOT NULL,
  emitida_fecha_local   TEXT NOT NULL CHECK (length(emitida_fecha_local) = 10),
  anulada_at            TEXT,
  motivo_anulacion      TEXT,
  reemplaza_a           INTEGER REFERENCES constancia(id),

  cliente_id            INTEGER NOT NULL REFERENCES cliente(id),
  direccion_retiro_id   INTEGER NOT NULL REFERENCES direccion_retiro(id),

  fecha_retiro_inicio   TEXT NOT NULL CHECK (length(fecha_retiro_inicio) = 10),
  fecha_retiro_termino  TEXT NOT NULL CHECK (length(fecha_retiro_termino) = 10),

  tipo_material_id      INTEGER NOT NULL REFERENCES tipo_material(id),
  cantidad_m3_cent      INTEGER NOT NULL CHECK (cantidad_m3_cent > 0),
  metodo_medicion       TEXT NOT NULL
                        CHECK (metodo_medicion IN ('contenedor','pesaje','estimacion_visual')),
  peso_kg_cent          INTEGER CHECK (peso_kg_cent IS NULL OR peso_kg_cent > 0),
  n_contenedores        INTEGER CHECK (n_contenedores IS NULL OR n_contenedores > 0),
  tipo_contenedor_id    INTEGER REFERENCES tipo_contenedor(id),
  n_viajes              INTEGER NOT NULL DEFAULT 1 CHECK (n_viajes > 0),

  vehiculo_id           INTEGER REFERENCES vehiculo(id),
  conductor_id          INTEGER REFERENCES conductor(id),
  destino_id            INTEGER NOT NULL REFERENCES destino(id),
  comprobante_destino   TEXT,

  receptor_nombre       TEXT,
  receptor_rut          TEXT,
  receptor_cargo        TEXT,
  firma_path            TEXT,
  lat                   REAL,
  lng                   REAL,
  observaciones         TEXT,

  emitida_por_usuario_id INTEGER NOT NULL REFERENCES usuario(id),

  -- Instantáneas inmutables. Alimentan la página pública y entran al hash.
  -- Si mañana se corrige la tilde de un material, la constancia ya emitida NO
  -- cambia y la cadena no se rompe. Por eso no se hace join contra el catálogo.
  snap_cliente_razon_social TEXT NOT NULL,
  snap_cliente_rut          TEXT NOT NULL,
  snap_comuna_retiro        TEXT NOT NULL,
  snap_tipo_material        TEXT NOT NULL,
  snap_destino_nombre       TEXT NOT NULL,
  snap_destino_comuna       TEXT NOT NULL,
  snap_destino_operacion    TEXT NOT NULL,

  hash_version          INTEGER NOT NULL DEFAULT 1,
  hash_anterior         TEXT NOT NULL CHECK (length(hash_anterior) = 64),
  hash_actual           TEXT NOT NULL UNIQUE CHECK (length(hash_actual) = 64),

  CHECK (fecha_retiro_termino >= fecha_retiro_inicio),                       -- RN-04
  CHECK (fecha_retiro_inicio <= emitida_fecha_local),                        -- RN-05
  CHECK (metodo_medicion <> 'contenedor'
         OR (n_contenedores IS NOT NULL AND tipo_contenedor_id IS NOT NULL)),-- RN-07
  CHECK (estado <> 'anulada'
         OR (anulada_at IS NOT NULL
             AND motivo_anulacion IS NOT NULL
             AND length(trim(motivo_anulacion)) >= 10)),                     -- RN-02
  UNIQUE (anio, seq)
) STRICT;

CREATE TABLE IF NOT EXISTS adjunto (
  id            INTEGER PRIMARY KEY,
  constancia_id INTEGER NOT NULL REFERENCES constancia(id),
  tipo          TEXT NOT NULL
                CHECK (tipo IN ('foto_antes','foto_despues','ticket_pesaje','firma','otro')),
  path          TEXT NOT NULL,
  bytes         INTEGER NOT NULL CHECK (bytes > 0),
  mime          TEXT NOT NULL,
  hash_archivo  TEXT NOT NULL CHECK (length(hash_archivo) = 64),
  created_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS consulta_verificacion (
  id                  INTEGER PRIMARY KEY,
  codigo_consultado   TEXT NOT NULL,
  exito               INTEGER NOT NULL CHECK (exito IN (0,1)),
  -- El motivo real se guarda para forense, pero JAMÁS viaja en la respuesta
  -- HTTP: distinguir "no existe" de "RUT no coincide" permitiría enumerar.
  motivo              TEXT NOT NULL
                      CHECK (motivo IN ('ok','no_existe','rut_no_coincide','formato','rate_limit')),
  ip                  TEXT NOT NULL,
  user_agent          TEXT,
  consultado_at       TEXT NOT NULL,
  consultado_at_epoch INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS anclaje_diario (
  id                  INTEGER PRIMARY KEY,
  fecha               TEXT NOT NULL UNIQUE,
  constancia_id       INTEGER REFERENCES constancia(id),
  folio               TEXT,
  hash_actual         TEXT NOT NULL,
  n_constancias_dia   INTEGER NOT NULL,
  n_constancias_total INTEGER NOT NULL,
  medio               TEXT NOT NULL CHECK (medio IN ('correo','archivo','ambos')),
  enviado_ok          INTEGER NOT NULL DEFAULT 0 CHECK (enviado_ok IN (0,1)),
  detalle             TEXT,
  creado_at           TEXT NOT NULL
) STRICT;

-- ── Índices ──────────────────────────────────────────────────────────
-- folio, codigo_verificacion, hash_actual, cliente.rut y (anio,seq) ya tienen
-- índice implícito por UNIQUE. No duplicarlos.

-- La consulta más caliente de la ruta pública: cuántos intentos hizo esta IP
-- en los últimos 15 minutos. Sin este índice, cada verificación hace full scan.
CREATE INDEX IF NOT EXISTS idx_consulta_ip_ts
  ON consulta_verificacion(ip, consultado_at_epoch);
-- Fuerza bruta distribuida contra un mismo código desde muchas IPs.
CREATE INDEX IF NOT EXISTS idx_consulta_cod_ts
  ON consulta_verificacion(codigo_consultado, consultado_at_epoch);

CREATE INDEX IF NOT EXISTS idx_const_cliente_fecha
  ON constancia(cliente_id, fecha_retiro_inicio DESC);
CREATE INDEX IF NOT EXISTS idx_const_estado_emit
  ON constancia(estado, emitida_at_epoch DESC);
-- Parcial: solo un puñado de filas tiene reemplaza_a, el índice no pesa nada.
CREATE INDEX IF NOT EXISTS idx_const_reemplaza
  ON constancia(reemplaza_a) WHERE reemplaza_a IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_const_fecha_local
  ON constancia(emitida_fecha_local, id DESC);
CREATE INDEX IF NOT EXISTS idx_const_destino    ON constancia(destino_id);
CREATE INDEX IF NOT EXISTS idx_dir_cliente      ON direccion_retiro(cliente_id, activo);
CREATE INDEX IF NOT EXISTS idx_adjunto_const    ON adjunto(constancia_id);
"""


# Los triggers van aparte porque llevan BEGIN...END y no conviene mezclarlos
# con el DDL en un solo executescript por legibilidad.
#
# Se usa `IS NOT` y no `<>`: `NULL <> NULL` devuelve NULL, con lo que el WHEN
# no dispararía y un campo que pasa de NULL a un valor se colaría sin ruido.
TRIGGERS = """
-- (1) RN-01 — inmutabilidad. Solo estado, anulada_at y motivo_anulacion
-- pueden cambiar, y solo una vez (ver trigger 3).
CREATE TRIGGER IF NOT EXISTS trg_constancia_inmutable
BEFORE UPDATE ON constancia
FOR EACH ROW WHEN (
     NEW.id IS NOT OLD.id
  OR NEW.folio IS NOT OLD.folio
  OR NEW.anio IS NOT OLD.anio
  OR NEW.seq IS NOT OLD.seq
  OR NEW.codigo_verificacion IS NOT OLD.codigo_verificacion
  OR NEW.emitida_at IS NOT OLD.emitida_at
  OR NEW.emitida_at_epoch IS NOT OLD.emitida_at_epoch
  OR NEW.emitida_fecha_local IS NOT OLD.emitida_fecha_local
  OR NEW.reemplaza_a IS NOT OLD.reemplaza_a
  OR NEW.cliente_id IS NOT OLD.cliente_id
  OR NEW.direccion_retiro_id IS NOT OLD.direccion_retiro_id
  OR NEW.fecha_retiro_inicio IS NOT OLD.fecha_retiro_inicio
  OR NEW.fecha_retiro_termino IS NOT OLD.fecha_retiro_termino
  OR NEW.tipo_material_id IS NOT OLD.tipo_material_id
  OR NEW.cantidad_m3_cent IS NOT OLD.cantidad_m3_cent
  OR NEW.metodo_medicion IS NOT OLD.metodo_medicion
  OR NEW.peso_kg_cent IS NOT OLD.peso_kg_cent
  OR NEW.n_contenedores IS NOT OLD.n_contenedores
  OR NEW.tipo_contenedor_id IS NOT OLD.tipo_contenedor_id
  OR NEW.n_viajes IS NOT OLD.n_viajes
  OR NEW.vehiculo_id IS NOT OLD.vehiculo_id
  OR NEW.conductor_id IS NOT OLD.conductor_id
  OR NEW.destino_id IS NOT OLD.destino_id
  OR NEW.comprobante_destino IS NOT OLD.comprobante_destino
  OR NEW.receptor_nombre IS NOT OLD.receptor_nombre
  OR NEW.receptor_rut IS NOT OLD.receptor_rut
  OR NEW.receptor_cargo IS NOT OLD.receptor_cargo
  OR NEW.firma_path IS NOT OLD.firma_path
  OR NEW.lat IS NOT OLD.lat
  OR NEW.lng IS NOT OLD.lng
  OR NEW.observaciones IS NOT OLD.observaciones
  OR NEW.emitida_por_usuario_id IS NOT OLD.emitida_por_usuario_id
  OR NEW.snap_cliente_razon_social IS NOT OLD.snap_cliente_razon_social
  OR NEW.snap_cliente_rut IS NOT OLD.snap_cliente_rut
  OR NEW.snap_comuna_retiro IS NOT OLD.snap_comuna_retiro
  OR NEW.snap_tipo_material IS NOT OLD.snap_tipo_material
  OR NEW.snap_destino_nombre IS NOT OLD.snap_destino_nombre
  OR NEW.snap_destino_comuna IS NOT OLD.snap_destino_comuna
  OR NEW.snap_destino_operacion IS NOT OLD.snap_destino_operacion
  OR NEW.hash_version IS NOT OLD.hash_version
  OR NEW.hash_anterior IS NOT OLD.hash_anterior
  OR NEW.hash_actual IS NOT OLD.hash_actual
)
BEGIN
  SELECT RAISE(ABORT,
    'RN-01: una constancia emitida no se modifica. Anule y emita una nueva.');
END;

-- (2) RN-03 — máquina de estados: solo vigente -> {anulada, reemplazada}.
CREATE TRIGGER IF NOT EXISTS trg_constancia_transicion
BEFORE UPDATE OF estado ON constancia
FOR EACH ROW WHEN NOT (
     OLD.estado = NEW.estado
  OR (OLD.estado = 'vigente' AND NEW.estado IN ('anulada','reemplazada'))
)
BEGIN
  SELECT RAISE(ABORT, 'RN-03: transicion de estado no permitida.');
END;

-- (3) RN-02 — el motivo es obligatorio al anular y no se reescribe después.
CREATE TRIGGER IF NOT EXISTS trg_constancia_motivo
BEFORE UPDATE ON constancia
FOR EACH ROW WHEN (
     (NEW.estado = 'anulada' AND OLD.estado <> 'anulada'
      AND (NEW.motivo_anulacion IS NULL
           OR length(trim(NEW.motivo_anulacion)) < 10
           OR NEW.anulada_at IS NULL))
  OR (OLD.motivo_anulacion IS NOT NULL
      AND NEW.motivo_anulacion IS NOT OLD.motivo_anulacion)
  OR (OLD.anulada_at IS NOT NULL AND NEW.anulada_at IS NOT OLD.anulada_at)
)
BEGIN
  SELECT RAISE(ABORT,
    'RN-02: anular exige motivo escrito (>=10 caracteres) y no se reescribe.');
END;

-- (4) RN-01 — nada se borra.
CREATE TRIGGER IF NOT EXISTS trg_constancia_no_delete
BEFORE DELETE ON constancia
BEGIN
  SELECT RAISE(ABORT, 'RN-01: una constancia emitida no se elimina.');
END;

-- (5) Reglas cruzadas en la inserción: RN-08, RN-09 y RN-03.
CREATE TRIGGER IF NOT EXISTS trg_constancia_insert_reglas
BEFORE INSERT ON constancia
FOR EACH ROW
BEGIN
  SELECT CASE
    WHEN (SELECT cliente_id FROM direccion_retiro WHERE id = NEW.direccion_retiro_id)
         IS NOT NEW.cliente_id
      THEN RAISE(ABORT, 'RN-08: la direccion de retiro no pertenece al cliente.')
    WHEN NEW.hash_anterior <> COALESCE(
           (SELECT hash_actual FROM constancia ORDER BY id DESC LIMIT 1),
           '0000000000000000000000000000000000000000000000000000000000000000')
      THEN RAISE(ABORT, 'RN-09: hash_anterior no corresponde al ultimo eslabon.')
    WHEN NEW.reemplaza_a IS NOT NULL AND
         (SELECT estado FROM constancia WHERE id = NEW.reemplaza_a) <> 'vigente'
      THEN RAISE(ABORT, 'RN-03: solo se puede reemplazar una constancia vigente.')
    WHEN NEW.reemplaza_a IS NOT NULL AND
         (SELECT cliente_id FROM constancia WHERE id = NEW.reemplaza_a) <> NEW.cliente_id
      THEN RAISE(ABORT, 'RN-03: el reemplazo debe ser del mismo cliente.')
  END;
END;

-- (6) Un adjunto es parte del documento: mismo trato que la constancia.
CREATE TRIGGER IF NOT EXISTS trg_adjunto_no_update
BEFORE UPDATE ON adjunto
BEGIN
  SELECT RAISE(ABORT, 'RN-01: los adjuntos de una constancia no se modifican.');
END;

CREATE TRIGGER IF NOT EXISTS trg_adjunto_no_delete
BEFORE DELETE ON adjunto
BEGIN
  SELECT RAISE(ABORT, 'RN-01: los adjuntos de una constancia no se eliminan.');
END;
"""


def init_db_constancias():
    """Crea tablas, índices y triggers. Idempotente."""
    with get_db() as conn:
        conn.executescript(DDL)
        conn.executescript(TRIGGERS)
