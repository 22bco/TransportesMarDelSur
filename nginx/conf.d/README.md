# nginx/conf.d — leer antes de tocar nada

**Este contenedor no es solo de Transportes Mar del Sur.** `mardelsur_nginx` es el
**proxy inverso central del VPS**: es el único que tiene los puertos 80 y 443, y
sirve a todos los dominios alojados en el servidor.

En el VPS, este directorio contiene ~12 archivos `.conf`. En git versionamos
**solo `mardelsur.conf`** (y este README). El resto pertenece a otros proyectos y
vive únicamente en el servidor.

Por eso el `.gitignore` usa lista blanca:

```gitignore
nginx/conf.d/*
!nginx/conf.d/mardelsur.conf
!nginx/conf.d/README.md
```

## Consecuencias prácticas

**Un clon limpio de este repo NO reproduce el proxy del VPS.** Si haces
`docker compose up` desde un clon en el servidor, `mardelsur_nginx` arranca sin las
confs ajenas y **deja caídos ~8 sitios**. El inventario completo está en
[`deploy/INVENTARIO-VPS.md`](../../deploy/INVENTARIO-VPS.md).

## Reglas de operación

1. **Nunca recrear el contenedor para aplicar un cambio de conf.** Usar reload:
   ```bash
   docker exec mardelsur_nginx nginx -t        # SIEMPRE primero
   docker exec mardelsur_nginx nginx -s reload
   ```
   Un error de sintaxis en `mardelsur.conf` tumba todos los dominios a la vez.

2. **`nginx -t` antes de cada reload.** Sin excepciones.

3. **Todo `restart` de un contenedor backend exige un `nginx -s reload` después.**
   nginx resuelve los nombres de contenedor (`mardelsur_web`, `robotica_backend`,
   `mardelsur_roundcube`) **una sola vez, al arrancar**, y cachea la IP. Docker
   reasigna la IP al recrear o reiniciar un contenedor → nginx sigue apuntando a la
   IP vieja y **devuelve 502 hasta que se recarga**:
   ```bash
   docker compose restart web
   docker exec mardelsur_nginx nginx -t && docker exec mardelsur_nginx nginx -s reload
   ```
   Comprobado en carne propia el 2026-08-04: un `restart web` sin reload dejó
   transportesmardelsur.cl en 502 durante ~3 minutos.

3. **Los certificados** los renueva `mardelsur_certbot` por webroot
   (`-w /var/www/certbot`). El cron recarga nginx a diario a las 03:50 para que
   tome los certificados renovados; ver `deploy/cron/mardelsur`.

4. **Detrás de Cloudflare.** Todos los dominios están proxied, por eso cada conf
   necesita los rangos `set_real_ip_from` de Cloudflare más
   `real_ip_header CF-Connecting-IP`. Sin eso, `$remote_addr` es la IP del edge de
   Cloudflare y **cualquier `limit_req` por IP deja de funcionar**: agrupa a todos
   los visitantes como un único cliente.
