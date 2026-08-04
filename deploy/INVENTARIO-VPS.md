# Inventario del VPS — qué vive fuera de este repo

Levantado el 2026-08-04 desde `72.61.4.202` (`/opt/TransportesMarDelSur`).

Este documento existe porque el contenedor nginx de este proyecto es el **proxy
central del servidor**: buena parte de lo que hace en producción no está —ni debe
estar— versionado aquí. Ignorar sin documentar es lo que convierte un
`docker compose up` rutinario en una caída de ocho sitios.

## 1. El servidor

| | |
|---|---|
| IP | `72.61.4.202` (hostname `mail`) |
| SO | Ubuntu 24.04 |
| Recursos | **1 CPU**, 3,9 GB RAM (~2,6 GB disponibles), **sin swap**, 48 GB de disco (50% usado) |
| Acceso | SSH como `root` con llave |

**Sin swap y con una sola CPU compartida por 7 contenedores.** Evitar builds
pesados en el servidor: preferir imágenes pre-construidas y `nginx -s reload` sobre
recrear contenedores.

## 2. Contenedores

| Contenedor | Proyecto | Rol |
|---|---|---|
| `mardelsur_nginx` | este | **Proxy central**: dueño de :80 y :443 para TODO el VPS |
| `mardelsur_web` | este | Flask + gunicorn, sitio público + panel `/admin` |
| `mardelsur_certbot` | este | Renovación Let's Encrypt por webroot |
| `mardelsur_mailserver` | este | `docker-mailserver` (**no** mailcow, pese al hostname) |
| `mardelsur_roundcube` | este | Webmail |
| `robotica_backend` | robotica-basti | Backend Node+SQLite de basti.cl/robotica |
| `apuntes_nginx` | apuntes-basti | Sitio estático de apuntes |
| `roadmap_nginx` | roadmap-basti | ⚠️ marcado *unhealthy* al 2026-08-03 |

## 3. Confs de nginx — solo `mardelsur.conf` está en git

| Archivo | Dominio | Backend / raíz |
|---|---|---|
| **`mardelsur.conf`** ✅ *versionado* | transportesmardelsur.cl, www | `mardelsur_web:5001` |
| `webmail.conf` | webmail.transportesmardelsur.cl | `mardelsur_roundcube:80` |
| `mail-acme.conf` | mail. / webmail.transportesmardelsur.cl | solo ACME (webroot) |
| `basti.conf` | basti.cl, www | estático `static/basti` + `robotica_backend:5003` |
| `chino.conf` | chino.basti.cl | estático `static/chino` |
| `mave.conf` | mave.basti.cl | estático `static/mave` |
| `lisbeth.conf` | lisbeth.cl, www | estático `static/lisbeth` |
| `apuntes.conf` | apuntes.basti.cl | `172.17.0.1:8082` |
| `roadmap.conf` | roadmap.basti.cl | `172.17.0.1:8083` |
| `sipud.conf` | sipud.cloud, www | `172.17.0.1:{3008,3009,9000}` |
| `demo-sipud.conf` | demo.sipud.cloud | upstream `$sipud_up` |

También hay `conf.d/disabled-20260710/` con confs apagadas
(`excalidraw.conf`, `excalidraw-ssl.conf`, `omr-basti.conf`) y varios `.bak-*`.

**El blast radius de un error de sintaxis en cualquiera de estos archivos son los
11 dominios a la vez.**

## 4. `static/` — 1,1 GB pertenecen a otros proyectos

`mardelsur_nginx` monta el `static/` de este repo en
`/usr/share/nginx/html/static`, y varias confs ajenas sirven **desde ahí**:

| Directorio | Tamaño | Servido por | ¿En git? |
|---|---|---|---|
| `static/apuntes` | 554M | (proxy a :8082) | ❌ ignorado |
| `static/chino` | 307M | `chino.conf` | ❌ ignorado |
| `static/mave` | 205M | `mave.conf` | ❌ ignorado |
| `static/aula` | 13M | ReporteAula | ❌ ignorado |
| `static/libs` | 1,1M | pack de libraries | ❌ ignorado |
| `static/basti` | 492K | `basti.conf` | ❌ ignorado |
| `static/lisbeth` | 8K | `lisbeth.conf` | ❌ ignorado |
| `static/img`, `css`, `js` | ~3,3M | este proyecto | ✅ |
| **`static/fontawesome`** | **888K** | este proyecto | ✅ **necesario** |

`static/fontawesome/` sí se versiona: `templates/admin/_base.html` carga
`static/fontawesome/css/all.min.css` y **sin él el panel admin pierde todos los
íconos**. La CSP actual solo permite `cdnjs` para `style-src`/`font-src`, así que
migrarlo a CDN exigiría tocar la CSP.

**No borrar los directorios ignorados en el servidor.** Son load-bearing.

## 5. Archivos que existen solo en el VPS

| Archivo | Qué es | Cómo se recupera |
|---|---|---|
| `.env` | **Único ejemplar.** Credenciales de admin, `SECRET_KEY`, datos bancarios del PDF, SMTP | Plantilla en `.env.example`; mantener copia cifrada fuera del VPS |
| `mailserver.env` | Config de `docker-mailserver` | Idem |
| `docker-data/quotes/quotes.db` | **La base de datos de producción** | Respaldo diario en `docker-data/backups/` |
| `docker-data/backups/` | ~30 respaldos `.db.gz` rotados | — |
| `certbot/` | Certificados Let's Encrypt | Reemisión con `ssl-setup.sh` |
| `node_modules/` | Solo para `npm run build:css` | `npm install` |

## 6. Cron

Vive en **`/etc/cron.d/mardelsur`**, fuera del repo. La copia versionada está en
[`deploy/cron/mardelsur`](cron/mardelsur). Para instalarla:

```bash
cp deploy/cron/mardelsur /etc/cron.d/mardelsur && chmod 644 /etc/cron.d/mardelsur
```

## 7. Reglas de operación

1. **El código se despliega con `git pull`, no editando archivos en el servidor.**
   Hasta el 2026-08-04 el sistema de cotizaciones existió *solo* en el VPS, sin
   historial ni posibilidad de revertir. Ver `docs/adr/ADR-000`.

2. **Ningún proceso del host abre `quotes.db` en modo escritura.** El archivo es de
   uid 1000 (`appuser` dentro del contenedor); si un script de cron corriendo como
   root escribe, deja `-wal`/`-shm` de root y **el contenedor pierde la escritura en
   silencio**. Por eso `scripts/backup_quotes.py` abre `mode=ro`, y cualquier job que
   necesite escribir corre dentro del contenedor con `docker exec -u appuser`.

3. **`nginx -t` antes de cada reload**, y `nginx -s reload` en vez de recrear el
   contenedor.

4. **Cuidado con el disco.** 24 GB libres, pero `static/` crece por proyectos ajenos.
