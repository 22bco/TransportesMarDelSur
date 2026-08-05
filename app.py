"""Punto de entrada de la aplicación.

Usa el patrón *application factory*: `crear_app()` construye una instancia
nueva y aislada. Eso es lo que permite testear con una base de datos temporal
sin levantar Docker ni tocar variables de entorno globales.

Gunicorn sigue apuntando a `app:app`, que se construye al final del módulo.
"""
import os
from datetime import timedelta

from flask import Flask, render_template, send_from_directory

# Cargar variables de .env si está disponible (en dev) — en Docker llegan vía env_file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def crear_app(config=None):
    """Construye la aplicación. `config` sobreescribe cualquier valor (tests)."""
    app = Flask(__name__)

    # Sesiones / seguridad para el panel admin
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-env')
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # El sitio se sirve siempre por HTTPS (Cloudflare con "Always Use HTTPS").
    app.config['SESSION_COOKIE_SECURE'] = True

    if config:
        app.config.update(config)

    # Protección CSRF para los formularios del panel admin.
    from flask_wtf.csrf import CSRFProtect
    CSRFProtect(app)

    registrar_rutas_publicas(app)

    from admin import admin_bp, init_db
    app.register_blueprint(admin_bp)

    with app.app_context():
        init_db()

    return app


def registrar_rutas_publicas(app):
    """Sitio público: marketing y landings de SEO. Sin autenticación."""

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template('404.html'), 500

    @app.route('/')
    def home():
        return render_template('home.html')

    @app.route('/servicios')
    def servicios():
        return render_template('services.html')

    @app.route('/certificaciones')
    def certificaciones():
        return render_template('certifications.html')

    @app.route('/flota')
    def flota():
        return render_template('fleet.html')

    @app.route('/faq')
    def faq():
        return render_template('faq.html')

    @app.route('/contacto')
    def contacto():
        return render_template('contact.html')

    @app.route('/nosotros')
    def nosotros():
        return render_template('about.html')

    # Landings de SEO
    @app.route('/que-es-respel')
    def que_es_respel():
        return render_template('que-es-respel.html')

    @app.route('/que-es-reas')
    def que_es_reas():
        return render_template('que-es-reas.html')

    @app.route('/transporte-puerto-montt')
    def transporte_puerto_montt():
        return render_template('transporte-puerto-montt.html')

    # Página monolítica antigua; se conserva por compatibilidad.
    @app.route('/index')
    def index():
        return render_template('index.html')

    @app.route('/robots.txt')
    def robots():
        return send_from_directory(os.path.join(app.root_path, 'static'),
                                   'robots.txt', mimetype='text/plain')

    @app.route('/sitemap.xml')
    def sitemap():
        return send_from_directory(os.path.join(app.root_path, 'static'),
                                   'sitemap.xml', mimetype='application/xml')


# Instancia que usa gunicorn (`app:app`). No cambiar el nombre.
app = crear_app()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
