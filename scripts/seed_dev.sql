-- Datos mínimos para poder emitir una constancia.
-- El CRUD de maestros llega en el Hito 2; mientras tanto se siembra a mano:
--   docker exec -i mardelsur_web python -m scripts.seed  < scripts/seed_dev.sql
-- o bien con sqlite3 sobre una copia de desarrollo.
--
-- Los RUT son válidos por módulo 11: el sistema los valida en el borde, así
-- que un RUT de relleno haría fallar el alta por la razón equivocada.

INSERT OR IGNORE INTO comuna (id, nombre, region) VALUES
    (1, 'Puerto Montt', 'Los Lagos'),
    (2, 'Osorno',       'Los Lagos'),
    (3, 'Castro',       'Los Lagos'),
    (4, 'Valdivia',     'Los Ríos');

INSERT OR IGNORE INTO tipo_material (id, nombre) VALUES
    (1, 'Residuos peligrosos (RESPEL)'),
    (2, 'Residuos hospitalarios (REAS)'),
    (3, 'Aceites y lubricantes usados'),
    (4, 'Residuos industriales no peligrosos');

INSERT OR IGNORE INTO tipo_contenedor (id, nombre, capacidad_m3_cent) VALUES
    (1, 'Contenedor 1 m3',   100),
    (2, 'Contenedor 5 m3',   500),
    (3, 'Ampliroll 12 m3',  1200),
    (4, 'Tambor 200 L',       20);

-- Usuario semilla. El login nominado llega en el Hito 2; hasta entonces todas
-- las constancias se atribuyen a este registro.
INSERT OR IGNORE INTO usuario (id, email, nombre, rol, created_at) VALUES
    (1, 'sistema@transportesmardelsur.cl', 'Sistema', 'admin', '2026-01-01T00:00:00Z');

INSERT OR IGNORE INTO destino (id, nombre, direccion, comuna_id, tipo_operacion, autorizacion) VALUES
    (1, 'Relleno Sanitario Los Lagos', 'Ruta 5 Sur km 920', 2, 'disposicion_final', 'RES-1234'),
    (2, 'Planta de Valorización Sur',  'Camino a Chinquihue km 6', 1, 'valorizacion', 'RES-5678');
