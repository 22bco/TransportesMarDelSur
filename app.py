from flask import Flask, render_template, send_from_directory, request, redirect
import os
from datetime import timedelta

# Cargar variables de .env si está disponible (en dev) — en Docker llegan vía env_file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from admin import admin_bp, init_db

app = Flask(__name__)

# Sesiones / seguridad para el panel admin
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-env')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# El sitio se sirve siempre por HTTPS (Cloudflare con "Always Use HTTPS").
app.config['SESSION_COOKIE_SECURE'] = True

# Protección CSRF para los formularios del panel admin.
# NOTA: flask-wtf se añade a requirements.txt en otro proceso; si la imagen
# actual no lo tiene instalado, el import fallará hasta el próximo rebuild.
from flask_wtf.csrf import CSRFProtect
CSRFProtect(app)

app.register_blueprint(admin_bp)

with app.app_context():
    init_db()

# Force HTTPS in production (disabled temporarily until SSL is configured)
# @app.before_request
# def force_https():
#     if not app.debug:
#         if request.headers.get('X-Forwarded-Proto', 'http') == 'http':
#             url = request.url.replace('http://', 'https://', 1)
#             return redirect(url, code=301)

# Custom error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('404.html'), 500

# Main landing page
@app.route('/')
def home():
    return render_template('home.html')

# Services page
@app.route('/servicios')
def servicios():
    return render_template('services.html')

# Certifications page
@app.route('/certificaciones')
def certificaciones():
    return render_template('certifications.html')

# Fleet page
@app.route('/flota')
def flota():
    return render_template('fleet.html')

# FAQ page
@app.route('/faq')
def faq():
    return render_template('faq.html')

# Contact page
@app.route('/contacto')
def contacto():
    return render_template('contact.html')

# About page
@app.route('/nosotros')
def nosotros():
    return render_template('about.html')

# SEO Landing Pages
@app.route('/que-es-respel')
def que_es_respel():
    return render_template('que-es-respel.html')

@app.route('/que-es-reas')
def que_es_reas():
    return render_template('que-es-reas.html')

@app.route('/transporte-puerto-montt')
def transporte_puerto_montt():
    return render_template('transporte-puerto-montt.html')

# Legacy route - redirect old single page (keep for backwards compatibility)
@app.route('/index')
def index():
    return render_template('index.html')

# SEO files
@app.route('/robots.txt')
def robots():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'robots.txt', mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'sitemap.xml', mimetype='application/xml')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
