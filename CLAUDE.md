# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Flask application for "Transportes Mar del Sur SPA", a Chilean company specializing in hazardous materials (RESPEL) and medical waste (REAS) transportation. It serves a public marketing website **and** an authenticated admin panel at `/admin`. Built with Flask, SQLite and locally-compiled Tailwind CSS, structured in Spanish.

**Business Context:**
- Company: Transporte Mar del Sur SPA (RUT: 77.779.818-9)
- Location: Puerto Montt, Los Lagos, Chile
- Sanitary Resolution: N° 2510389969
- Services: RESPEL transport, REAS transport, general cargo, warehousing, SIDREP documentation

## Development Commands

### Running the Application
```bash
# Start development server (runs on port 5001)
python3 app.py

# Server will be accessible at http://localhost:5001
```

### Dependencies
```bash
pip install -r requirements.txt
```

Flask 3.0, Gunicorn, **WeasyPrint** (PDF server-side), **flask-wtf** (CSRF),
python-dotenv, tzdata. WeasyPrint needs pango/cairo: the Dockerfile installs them;
on macOS they come from Homebrew.

### CSS build
```bash
npm run build:css     # una vez
npm run watch:css     # en desarrollo
```
Tailwind v4 se **compila localmente** a `static/css/tailwind.css` (ya no es CDN).
Colores custom en `static/css/input.css`.

### Deployment — regla no negociable
> **Nadie edita archivos en `/opt/TransportesMarDelSur` con un editor.
> Se despliega con `git pull`.**

Hasta agosto de 2026 el panel admin existió *solo* en el VPS, sin historial ni
rollback. Ver `docs/adr/ADR-000-el-codigo-vivia-fuera-de-git.md`.

`mardelsur_nginx` es el **proxy central del VPS**: sirve ~11 dominios de otros
proyectos. Antes de cualquier reload, `docker exec mardelsur_nginx nginx -t`, y usar
`nginx -s reload` en vez de recrear el contenedor. Ver `deploy/INVENTARIO-VPS.md`.

## Architecture

### Multi-Route Structure
The application uses a **multi-page architecture** with dedicated routes for each section:

**Routes (app.py:6-56):**
- `/` → home.html (landing page with navigation cards)
- `/servicios` → services.html (service details)
- `/certificaciones` → certifications.html (certifications grid)
- `/flota` → fleet.html (vehicle specifications)
- `/faq` → faq.html (FAQ accordion)
- `/contacto` → contact.html (contact form)
- `/nosotros` → about.html (company info)
- `/index` → index.html (legacy monolithic page - kept for backwards compatibility)
- `/robots.txt` → static file
- `/sitemap.xml` → static file

### Template Inheritance System

**Base Template (templates/base.html):**
- Defines site-wide structure with comprehensive SEO meta tags
- Includes Schema.org JSON-LD structured data for local business
- Loads locally-compiled Tailwind CSS (`static/css/tailwind.css`; custom `hazard` color #FFD600 defined in `static/css/input.css`)
- Loads Font Awesome 6.0.0 for icons
- Includes shared components: navbar, footer, quick contact buttons
- Defines blocks: `{% block title %}`, `{% block description %}`, `{% block content %}`

**Shared Components:**
- `templates/navbar.html` - Desktop + mobile navigation with route-based links
- `templates/footer.html` - Footer with contact info, service links, company data
- Both included via `{% include 'navbar.html' ignore missing %}`

**Page Templates:**
All page templates extend base.html and override title/description/content blocks.

### JavaScript Interactive Features

**Infinite Carousel (static/js/carousel.js):**
- Used on legacy index.html page for certification images
- Implements triple-cloning technique for seamless infinite scroll
- Auto-scrolls at 1px/20ms with pause-on-hover
- Includes drag-to-scroll with momentum
- Modal system for certification details via `openModal(certId)` function
- checkInfiniteScroll() prevents visible jumps at boundaries

**Modal System:**
- Certification data stored in `certificationData` object (carousel.js)
- Opens via onclick handlers: `onclick="openModal('ds298')"`
- Closes via `closeModal()` or clicking backdrop

**Quick Contact Buttons (static/css/contact-button.css):**
- Floating action buttons (FAB) at bottom-right
- Dual buttons: WhatsApp (green gradient) + Phone (hazard yellow)
- Includes tooltip on hover, pulse animation
- Mobile-optimized with responsive sizing
- HTML in base.html:219-232

### Asset Organization

**Images:**
- `static/img/logo.png` - Primary logo (used in navbar/footer)
- `static/img/certificado_ds298.svg` - DS 298 certification badge
- `static/img/sidrep_certificado.svg` - SIDREP certification
- `static/img/resolucion_sanitaria.svg` - Sanitary resolution
- `static/img/clase[1-9]_*.svg` - NCh 2190 hazard class pictograms
- `static/img/logo_*.svg` - Alternative logo variations (not currently used)

**CSS:**
- Tailwind v4 compiled locally via `npm run build:css` (`static/css/input.css` → `static/css/tailwind.css`)
- Custom config in base.html:157-169 with hazard yellow (#FFD600)
- `static/css/contact-button.css` - Floating contact button styles

**JavaScript:**
- `static/js/carousel.js` - Carousel controller with infinite scroll + modal system

## Key Design Patterns

### Color Scheme
- **Hazard Yellow (#FFD600)**: Primary brand color, CTAs, highlights
- **Dark (#2D2D2D)**: Text, headers
- **Light (#F5F5F5)**: Backgrounds
- Gradients used extensively for hero sections

### Responsive Design
- Mobile-first with Tailwind breakpoints (sm, md, lg)
- Mobile menu toggle via JavaScript (navbar.html:26)
- Contact buttons resize for mobile (contact-button.css:59-77)

### Navigation Pattern
- Desktop: Horizontal navbar with 6 main links + CTA button
- Mobile: Hamburger menu with expanded vertical links
- Footer: Mirrors navigation structure with additional company info
- All navigation uses Flask routes (not anchor links)

### SEO Strategy
- Comprehensive meta tags in base.html (lines 8-100)
- Schema.org LocalBusiness structured data
- Geo-location tags for Puerto Montt
- Open Graph and Twitter Card meta tags
- Spanish language throughout (lang="es")

## Important Notes

### Legacy Page
The original single-page design is preserved at `/index` route. It contains the infinite carousel and was the previous monolithic structure. New development should focus on the multi-page structure.

### Contact Information
Hardcoded throughout templates:
- Email: Transportesmardelsur@gmail.com
- Phone/WhatsApp: +56 9 42857502
- Location: Puerto Montt, Los Lagos

### Admin panel (`admin.py`)
Blueprint autenticado bajo `/admin`: sistema de cotizaciones con folio correlativo
(`COT-2026-003`), PDF vía WeasyPrint y respaldo diario de la BD.

Patrones a reutilizar en cualquier módulo nuevo:
- `get_db()` — context manager con commit/rollback/close garantizado, WAL,
  `PRAGMA foreign_keys`, `busy_timeout`.
- `insert_quote()` — folio correlativo atómico con `BEGIN IMMEDIATE` + reintentos
  ante `IntegrityError`. **Obligatorio** con varios workers de gunicorn: sin el lock
  de escritura, dos POST simultáneos calculan el mismo correlativo.
- `login_required`, `_logo_data_uri()` (base64 → data URI para el PDF),
  `_echo_quote()` (re-muestra el formulario sin perder lo escrito si falla la
  validación), `_fecha_larga()`.
- Errores: `current_app.logger.error(...)` + `flash(msg, 'error')` + re-render.

Convención: **nombres de dominio, rutas, docstrings y comentarios en español**.

La BD (`quotes.db`) es de uid 1000. **Ningún proceso del host la abre en modo
escritura**: dejaría `-wal`/`-shm` de root y el contenedor perdería la escritura en
silencio. Por eso `scripts/backup_quotes.py` usa `mode=ro`, y los jobs que escriben
corren con `docker exec -u appuser`.

### Contact form
El formulario de contacto hace POST a **Formspree** (servicio externo); Flask no lo
procesa.

### Chilean Regulatory Context
References to Chilean regulations are central to content:
- DS 298: Hazardous cargo transport decree
- NCh 2190: Chilean hazard classification standard
- SIDREP: Hazardous waste declaration and tracking system
- REAS: Healthcare facility waste

When editing content, maintain compliance terminology and certification numbers.
