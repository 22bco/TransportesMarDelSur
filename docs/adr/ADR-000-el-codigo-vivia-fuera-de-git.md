# ADR-000 — El código de producción vivía fuera de git

- **Fecha:** 2026-08-04
- **Estado:** aceptado
- **Contexto:** rescate previo al módulo de constancias de retiro

## Contexto

Al empezar a planificar el módulo de constancias de retiro se descubrió que el
**sistema de cotizaciones, en producción desde mayo de 2026, no estaba en git**.

Estado encontrado el 2026-08-04:

- El repo local y GitHub estaban en `6b2bea2` (SEO, enero 2026).
- El VPS estaba en `17c138b`, **un commit por detrás**: nunca hizo `git pull`.
- El VPS tenía, sin commitear:
  - `admin.py` (550 líneas) — el sistema de cotizaciones completo
  - `templates/admin/` (6 plantillas, incluida la del PDF)
  - `scripts/backup_quotes.py`
  - `static/fontawesome/` (888K, del que depende el panel)
  - Modificaciones a `app.py`, `Dockerfile`, `docker-compose.yml`,
    `requirements.txt`, `nginx/nginx.conf`, `nginx/conf.d/mardelsur.conf`
  - `/etc/cron.d/mardelsur`, fuera del repo por definición

Es decir: **divergencia cruzada**. El servidor tenía código que el repo no tenía, y
el repo tenía templates que el servidor no tenía.

## Consecuencias que esto tenía

- Sin historial: imposible saber qué cambió, cuándo ni por qué.
- Sin rollback: un error se arreglaba editando en producción.
- Sin revisión previa a desplegar.
- **Un solo punto de falla:** perder el VPS era perder el sistema. La única copia
  de `admin.py` estaba en un servidor de 1 CPU sin swap.

No se perdió nada. Pero fue suerte, no diseño.

## Decisión

1. Rescatar todos los archivos del VPS al repo y commitear, separando por dominio
   para que el historial sirva de algo.
2. `.gitignore` con **lista blanca** para `nginx/conf.d/`: `mardelsur_nginx` es el
   proxy central del VPS y su `conf.d/` contiene confs de otros proyectos, que no
   pertenecen a este repo pero **tampoco pueden ignorarse en silencio** — de ahí
   `nginx/conf.d/README.md` y `deploy/INVENTARIO-VPS.md`.
3. Ignorar `docker-data/`, `backups/`, `*.db*` y los directorios de `static/` que
   sirven a otros proyectos (~1,1 GB).
4. Versionar `.env.example` y `deploy/cron/mardelsur`.
5. Re-sincronizar el VPS con `git merge --ff-only`.

## Regla que nace de aquí

> **Nadie edita archivos en `/opt/TransportesMarDelSur` con un editor.
> Se despliega con `git pull`.**

Si algo hay que arreglar en producción con urgencia: se arregla, y **el mismo día**
se lleva al repo. Un archivo modificado en el servidor y no commiteado es una
regresión esperando a ocurrir en el próximo `git pull`.

## Qué no se resolvió aquí

- El `.env` sigue siendo el único ejemplar y sigue fuera de git, como corresponde.
  Mitigación: `.env.example` versionado + copia cifrada fuera del VPS.
- `docker-compose.yml` monta el código como bind-mount encima de una imagen
  construida con `COPY . .`, así que imagen y montaje **pueden divergir en
  silencio**. Deuda anotada; el estado objetivo es desplegar con
  `git pull && docker compose up -d --build`, pospuesto porque un build en 1 CPU
  con WeasyPrint tarda varios minutos.
